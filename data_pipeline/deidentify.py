#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline, local-only de-identification pipeline for CM outbound transcripts.

Safety contract (non-negotiable)
--------------------------------
* Source files are opened in binary, read-only, with strict UTF-8 decoding.
  ``errors="ignore"`` / ``errors="replace"`` are never used.
* Raw text never leaves the process: it is never written to disk, never
  printed, and never included in exception messages or logs.
* Only masked records and opaque HMAC transcript ids are written.
* No network access, no external model/API, no third-party dependency.

v2 changes (privacy fixes F1-F11)
--------------------------------
* F1: digit entities are normalised into a digit stream plus an offset map and
  every 11-digit window is checked for a ``1[3-9]`` mobile candidate. When a
  phone is embedded in a longer run the *whole* entity is replaced, so no
  leading/trailing digits can survive.
* F2: 9-14 digit entities that are not confirmed phones become ``<ACCOUNT>``
  (or are quarantined when they look like a malformed phone); 6-8 digit
  entities keep context-bound rules; >14 digit entities stay ID/card.
* F4: output path containment is validated before any directory is created.
* F5: only tool-created key directories are chmodded; existing directories are
  never touched; key files must be regular files, not symlinks.
* F6: address windows extend to the sentence end or 80 characters and an
  unprovable boundary quarantines the unit.
* F7: only a fixed event vocabulary plus a strict uppercase tag syntax becomes
  an EVENT; other bracketed speech is preserved as text.
* F8-F11: reproducible manifest, occurrence/record/transcript statistics, and a
  fail-closed structural gate before publication.

Output record schema is documented in the generated ``README.md`` and in
``PIPELINE.md`` next to this module.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Sequence

SCHEMA_VERSION = "1.0"
PIPELINE_VERSION = "2.0.0"
MANIFEST_VERSION = "2.0"
DATASET_NAME = "cm_outbound_v2"
REQUIRED_PYTHON = (3, 12, 16)

GENDERS = ("男", "女", "儿童")
LANGUAGES = ("普通话", "方言")
CHANNELS = (1, 2, 3, 4, 5)

# Known audio/system events. ``*`` is an opaque vendor marker, not a wildcard.
EVENT_MAP = {
    "SYSTEM": "SYSTEM",
    "ENS": "ENS",
    "DTMF": "DTMF",
    "MUSIC": "MUSIC",
    "SONANT": "SONANT",
    "LAUGH": "LAUGH",
    "*": "UNCLASSIFIED",
    "UNKNOWN": "UNKNOWN",
}
EVENT_ALIASES = {"SSTEM": ("SYSTEM", "event_spelling_variant")}
EVENT_CODES = (
    "SYSTEM", "ENS", "DTMF", "MUSIC", "SONANT", "LAUGH", "UNCLASSIFIED", "UNKNOWN",
)
# Only a strict uppercase vendor-tag shape may become an unknown EVENT. Any
# other bracketed text is ordinary speech and must be preserved, never dropped.
STRICT_EVENT_TAG_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")

PLACEHOLDERS = {
    "PHONE": "<PHONE>",
    "NAME": "<NAME>",
    "ID_NO": "<ID_NO>",
    "ADDRESS": "<ADDRESS>",
    "ACCOUNT": "<ACCOUNT>",
    "CARD_NO": "<CARD_NO>",
    "VERIFICATION_CODE": "<VERIFICATION_CODE>",
    "PASSWORD": "<PASSWORD>",
    "EMAIL": "<EMAIL>",
    "URL": "<URL>",
    "CONTACT": "<CONTACT>",
    "PLATE": "<PLATE>",
}
ALLOWED_PLACEHOLDERS = frozenset(PLACEHOLDERS.values()) | {"<ASR_TOKEN>"}
PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]*>")

# Higher value wins when two candidate spans start at the same offset.
SPAN_PRIORITY = {
    "ID_NO": 0,
    "CARD_NO": 1,
    "PHONE": 2,
    "VERIFICATION_CODE": 3,
    "PASSWORD": 4,
    "ACCOUNT": 5,
    "EMAIL": 6,
    "URL": 7,
    "CONTACT": 8,
    "PLATE": 9,
    "NAME": 10,
    "ADDRESS": 11,
}

ALLOWED_ASR_FLAGS = frozenset({
    "event_spelling_variant",
    "malformed_event_tag",
    "ambiguous_phone_candidate",
    "id_pattern_masked",
    "long_digit_run_masked",
    "phone_embedded_long_run",
    "account_long_run_masked",
    "phone_split_merged",
    "yen_token",
    "empty_after_mask",
    "ambiguous_plus_position",
    "short_unclassified_digit_masked",
})

ALLOWED_ROLES = frozenset({"UNKNOWN"})
ALLOWED_REDACTION_KINDS = frozenset(PLACEHOLDERS)

TRANSCRIPT_ID_RE = re.compile(r"^CALL_[0-9a-f]{24}$")

QUARANTINE_KEYS = frozenset({"transcript_id", "reason_codes", "affected_seq", "status"})

RECORD_KEYS = (
    "seq", "start_ms", "end_ms", "channel_id", "role", "payload_type",
    "event", "text_masked", "is_truncated", "asr_flags", "redaction_flags",
)
RECORD_KEY_SET = frozenset(RECORD_KEYS)


class ParseError(Exception):
    """Parse failure carrying a stable, content-free reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PathSafetyError(RuntimeError):
    """Output/key path rejected before any filesystem write."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PublishError(RuntimeError):
    """Post-write verification failed; staging must be removed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Chinese numeral handling
# ---------------------------------------------------------------------------

CN_DIGITS = {
    "零": "0", "〇": "0", "洞": "0",
    "一": "1", "幺": "1",
    "二": "2", "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7", "拐": "7",
    "八": "8",
    "九": "9",
}

CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_BIG_UNITS = {"万": 10000, "亿": 100000000}

# Characters allowed *between* spoken digits of one number. A bounded number of
# separators may appear; a longer gap ends the entity (controlled normalisation).
DIGIT_SEPARATORS = set(" \t，,、;；:：.．·-—~～()（）[]【】\"'“”《》")
DIGIT_BREAKS = set("。！？!?\n\r")
MAX_SEPARATOR_GAP = 6

PHONE_PREFIX_SECOND = frozenset("3456789")

# Punctuation that may surround a leading/trailing fragment.
BOUNDARY_FILLER = set(" \t，,、；;：:。.！!？?）)】]\"'“”《》")


def cn_digit_value(ch: str) -> str | None:
    """Map one character to a single decimal digit string, or None."""
    if ch in CN_DIGITS:
        return CN_DIGITS[ch]
    if "0" <= ch <= "9":
        return ch
    if "\uff10" <= ch <= "\uff19":  # full-width digits
        return chr(ord(ch) - 0xFEE0)
    return None


def cn_to_int(text: str) -> int | None:
    """Convert a positional Chinese numeral (e.g. 一百二十八) to int.

    Returns None when the string is not a well-formed numeral. This helper is
    intentionally conservative: it is used for tests and diagnostics, not for
    deciding what to mask.
    """
    if not text:
        return None
    if all(c in CN_DIGITS or c in "0123456789" for c in text):
        return int("".join(cn_digit_value(c) for c in text))  # type: ignore[arg-type]
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in CN_DIGITS or ch.isdigit():
            number = number * 10 + int(cn_digit_value(ch) or "0")
        elif ch in CN_UNITS:
            if number == 0:
                number = 1
            section += number * CN_UNITS[ch]
            number = 0
        elif ch in CN_BIG_UNITS:
            section += number
            total += (section or 1) * CN_BIG_UNITS[ch]
            section = 0
            number = 0
        else:
            return None
    return total + section + number


@dataclass(frozen=True)
class DigitRun:
    """A maximal digit entity with a map back to raw character offsets."""

    start: int
    end: int
    digits: str
    offsets: tuple[int, ...] = ()


def iter_digit_runs(text: str) -> Iterable[DigitRun]:
    """Yield maximal digit entities, tolerating bounded punctuation/space gaps."""
    start: int | None = None
    digits: list[str] = []
    offsets: list[int] = []
    last_end = 0
    gap = 0

    for index, ch in enumerate(text):
        value = cn_digit_value(ch)
        if value is not None:
            if start is None:
                start = index
            digits.append(value)
            offsets.append(index)
            last_end = index + 1
            gap = 0
        elif ch in DIGIT_BREAKS:
            if digits:
                yield DigitRun(start, last_end, "".join(digits), tuple(offsets))  # type: ignore[arg-type]
            start, digits, offsets, gap = None, [], [], 0
        elif ch in DIGIT_SEPARATORS:
            if digits:
                gap += 1
                if gap > MAX_SEPARATOR_GAP:
                    yield DigitRun(start, last_end, "".join(digits), tuple(offsets))  # type: ignore[arg-type]
                    start, digits, offsets, gap = None, [], [], 0
        else:
            if digits:
                yield DigitRun(start, last_end, "".join(digits), tuple(offsets))  # type: ignore[arg-type]
            start, digits, offsets, gap = None, [], [], 0
    if digits:
        yield DigitRun(start, last_end, "".join(digits), tuple(offsets))  # type: ignore[arg-type]


def phone_window_offsets(digits: str) -> list[int]:
    """Return every 11-digit window offset that starts like a mobile number."""
    hits: list[int] = []
    for index in range(0, max(0, len(digits) - 10)):
        if digits[index] == "1" and digits[index + 1] in PHONE_PREFIX_SECOND:
            hits.append(index)
    return hits


def valid_id18(digits: str) -> bool:
    if len(digits) != 18:
        return False
    try:
        year = int(digits[6:10])
        month = int(digits[10:12])
        day = int(digits[12:14])
    except ValueError:
        return False
    return 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def valid_id15(digits: str) -> bool:
    if len(digits) != 15:
        return False
    try:
        month = int(digits[8:10])
        day = int(digits[10:12])
    except ValueError:
        return False
    return 1 <= month <= 12 and 1 <= day <= 31


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

TS_FIELD_RE = re.compile(r"^\[(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\]$")


@dataclass(frozen=True)
class RawRecord:
    seq: int
    start: Decimal
    end: Decimal
    start_ms: int
    end_ms: int
    channel: int
    gender: str
    language: str
    payload: str


def decode_strict(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ParseError("PARSE_DECODE") from None


def split_lines(text: str) -> list[str]:
    if text == "":
        raise ParseError("PARSE_EMPTY_FILE")
    if not text.endswith("\r\n"):
        raise ParseError("PARSE_LINE_ENDING")
    body = text[:-2]
    if "\r" in body.replace("\r\n", "") or "\n" in body.replace("\r\n", ""):
        raise ParseError("PARSE_LINE_ENDING")
    return body.split("\r\n")


def to_ms(value: str) -> int:
    try:
        dec = Decimal(value)
    except InvalidOperation:
        raise ParseError("PARSE_TIMESTAMP") from None
    if not dec.is_finite() or dec < 0:
        raise ParseError("PARSE_TIMESTAMP")
    return int((dec * 1000).to_integral_value(rounding=ROUND_HALF_UP))


def parse_line(line: str, seq: int) -> RawRecord:
    parts = line.split("\t")
    if len(parts) != 4:
        raise ParseError("PARSE_FIELD_COUNT")
    ts_field, channel_field, profile_field, payload = parts
    match = TS_FIELD_RE.match(ts_field)
    if not match:
        raise ParseError("PARSE_TIMESTAMP")
    start = Decimal(match.group(1))
    end = Decimal(match.group(2))
    if channel_field not in ("1", "2", "3", "4", "5"):
        raise ParseError("PARSE_CHANNEL")
    channel = int(channel_field)
    profile = profile_field.split(",")
    if len(profile) != 2:
        raise ParseError("PARSE_PROFILE")
    gender, language = profile
    if gender not in GENDERS or language not in LANGUAGES:
        raise ParseError("PARSE_PROFILE")
    if payload == "":
        raise ParseError("PARSE_EMPTY_PAYLOAD")
    return RawRecord(
        seq=seq,
        start=start,
        end=end,
        start_ms=to_ms(match.group(1)),
        end_ms=to_ms(match.group(2)),
        channel=channel,
        gender=gender,
        language=language,
        payload=payload,
    )


def parse_transcript(raw: bytes) -> list[RawRecord]:
    """Parse one transcript unit. Raises ParseError with a content-free code."""
    text = decode_strict(raw)
    lines = split_lines(text)
    records: list[RawRecord] = []
    prev_start: Decimal | None = None
    prev_end: Decimal | None = None
    for seq, line in enumerate(lines):
        record = parse_line(line, seq)
        if record.end < record.start:
            raise ParseError("TIMESTAMP_INVALID")
        if prev_start is not None and record.start < prev_start:
            raise ParseError("TIMESTAMP_NON_MONOTONIC")
        if prev_end is not None and record.start < prev_end:
            raise ParseError("TIMESTAMP_OVERLAP")
        prev_start, prev_end = record.start, record.end
        records.append(record)
    return records


def classify_payload(payload: str) -> tuple[str, str | None, list[str]]:
    """Return (payload_type, event_code, flags).

    F7: only the fixed vocabulary, a known spelling alias, or a strict
    uppercase vendor tag becomes an EVENT. Anything else (e.g. ``[你好]``,
    ``[MU SIC]``) remains speech text so quoted content is never dropped.
    """
    stripped = payload.strip()
    if 3 <= len(stripped) <= 24 and stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1].strip()
        if inner in EVENT_MAP:
            return "EVENT", EVENT_MAP[inner], []
        if inner in EVENT_ALIASES:
            code, flag = EVENT_ALIASES[inner]
            return "EVENT", code, [flag]
        if STRICT_EVENT_TAG_RE.match(inner):
            return "EVENT", "UNKNOWN", ["malformed_event_tag"]
    return "SPEECH", None, []


# ---------------------------------------------------------------------------
# Key management and opaque ids
# ---------------------------------------------------------------------------

KEY_SUBDIR = Path(".local/share/cm-outbound-bench/keys")
KEY_FILENAME = "transcript_id_hmac.key"
DEFAULT_KEY_ROOT = Path.home() / KEY_SUBDIR


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def ensure_key(
    path: Path,
    forbidden_roots: Sequence[Path],
    key_root: Path | None = None,
) -> bytes:
    """Load or create the HMAC key without touching existing directory modes.

    F5 controls:
    * the key must live under the dedicated key root (default
      ``~/.local/share/cm-outbound-bench/keys``);
    * an existing key must be a regular file (no symlink) with no group/other
      permission bits; it is reused, never regenerated and never chmodded;
    * only directories created by this function are set to 0700;
    * a newly created key file is opened with mode 0600.
    """
    root = Path(key_root).expanduser().resolve() if key_root else DEFAULT_KEY_ROOT.resolve()
    if root in (Path("/"), Path.home().resolve(), Path("/tmp"), Path("/home")):
        raise PathSafetyError("KEY_ROOT_UNSAFE")

    raw_path = Path(path).expanduser()
    try:
        lst = os.lstat(raw_path)
    except FileNotFoundError:
        lst = None
    except OSError:
        raise PathSafetyError("KEY_STAT_FAILED") from None

    if lst is not None:
        if stat.S_ISLNK(lst.st_mode):
            raise PathSafetyError("KEY_SYMLINK")
        if not stat.S_ISREG(lst.st_mode):
            raise PathSafetyError("KEY_NOT_REGULAR")

    resolved = raw_path.resolve(strict=False)
    if resolved.name in ("", ".", ".."):
        raise PathSafetyError("KEY_INVALID_PATH")
    if not _is_within(resolved, root):
        raise PathSafetyError("KEY_OUTSIDE_DEDICATED_ROOT")
    for forbidden in forbidden_roots:
        forbidden_resolved = Path(forbidden).expanduser().resolve(strict=False)
        if resolved == forbidden_resolved or _is_within(resolved, forbidden_resolved):
            raise PathSafetyError("KEY_FORBIDDEN_LOCATION")

    if lst is not None:
        mode = stat.S_IMODE(lst.st_mode)
        if mode & 0o077:
            raise PathSafetyError("KEY_PERMISSIONS_TOO_OPEN")
        try:
            key = raw_path.read_bytes()
        except OSError:
            raise PathSafetyError("KEY_READ_FAILED") from None
        if len(key) < 32:
            raise PathSafetyError("KEY_TOO_SHORT")
        return key

    # Create only the missing directory layers; never chmod an existing one.
    missing: list[Path] = []
    parent = resolved.parent
    while not parent.exists():
        missing.append(parent)
        if parent == parent.parent:
            break
        parent = parent.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        except OSError:
            raise PathSafetyError("KEY_DIR_CREATE_FAILED") from None
    if not resolved.parent.is_dir():
        raise PathSafetyError("KEY_PARENT_INVALID")

    key = secrets.token_bytes(32)
    try:
        fd = os.open(resolved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise PathSafetyError("KEY_CREATE_RACE") from None
    except OSError:
        raise PathSafetyError("KEY_CREATE_FAILED") from None
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        os.chmod(resolved, 0o600)
    except OSError:
        try:
            resolved.unlink()
        except OSError:
            pass
        raise PathSafetyError("KEY_CHMOD_FAILED") from None
    return key


def make_transcript_id(key: bytes, filename: str) -> str:
    digest = hmac.new(key, filename.encode("utf-8"), hashlib.sha256).hexdigest()
    return "CALL_" + digest[:24]


# ---------------------------------------------------------------------------
# Redaction engine
# ---------------------------------------------------------------------------

SINGLE_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻"
    "柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤"
    "滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄"
    "米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭"
    "梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓"
    "郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松"
    "井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘"
    "景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍卻璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴"
    "瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧"
    "殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查"
    "后荆红游竺权逯盖益桓公"
)
COMPOUND_SURNAMES = (
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容",
    "长孙", "宇文", "司徒", "夏侯", "令狐", "端木", "独孤", "南宫", "西门",
    "百里", "呼延", "东郭", "申屠",
)
HONORIFICS = (
    "总经理", "先生", "女士", "小姐", "师傅", "阿姨", "叔叔",
    "老师", "经理", "主任", "总",
)

_SURNAME_CLASS = "[" + "".join(dict.fromkeys(SINGLE_SURNAMES)) + "]"
_SURNAME_ALT = "(?:" + "|".join(COMPOUND_SURNAMES) + "|" + _SURNAME_CLASS + ")"
NAME_RE = re.compile(
    r"(?P<name>" + _SURNAME_ALT + r"[\u4e00-\u9fff]{0,2}?)"
    r"(?P<hon>" + "|".join(HONORIFICS) + r")"
)
XING_RE = re.compile(r"姓(?![名称])(?P<surname>" + _SURNAME_ALT + r")")
WOJIAO_RE = re.compile(r"(?:我叫|名字叫|姓名是|名字是)(?P<name>[\u4e00-\u9fff]{2,3})")
NAME_STOPWORDS = {"什么", "一下", "大家", "你们", "我们", "他们", "这里", "那里"}

ADDRESS_TRIGGERS = (
    "详细地址", "家庭地址", "收货地址", "我的地址", "住址", "家住", "住在",
    "搬到", "送到", "寄到", "我家", "地址",
)
# Only these imply a *personal* address; generic "地址" is not enough to
# quarantine on, because business venues are also described as 地址.
PERSONAL_ADDRESS_TRIGGERS = (
    "详细地址", "家庭地址", "收货地址", "我的地址", "住址", "家住", "住在",
    "搬到", "寄到", "我家",
)
ADDRESS_MARKERS = (
    "省", "市", "区", "县", "镇", "乡", "街道", "路", "街", "巷", "号",
    "栋", "幢", "单元", "室", "小区", "村", "组", "楼", "大厦", "公寓",
    "花园", "家园", "苑", "弄",
)
ADDRESS_MAX_WINDOW = 80
ADDRESS_SENTENCE_BREAKS = set("。！？!?\n\r")
ADDRESS_FILLERS = set(" 是：:在为，,、")

ADDRESS_RE_URBAN = re.compile(
    r"(?:[\u4e00-\u9fff]{1,6}(?:省|自治区))?"
    r"[\u4e00-\u9fff]{1,6}(?:市|州|盟)?"
    r"[\u4e00-\u9fff]{1,6}(?:区|县|旗)"
    r"[\u4e00-\u9fff]{1,10}(?:路|街|巷|道|大街)"
    r"[\u4e00-\u9fff0-9]{0,6}(?:号|号院|号楼)"
)
ADDRESS_RE_COMMUNITY = re.compile(
    r"[\u4e00-\u9fff]{2,10}(?:小区|花园|公寓|大厦|家园|苑)"
    r"[\u4e00-\u9fff0-9]{0,12}"
    r"(?:(?:号楼|栋|幢|单元|室|层|号|户)[\u4e00-\u9fff0-9]{0,4}){0,4}"
)
ADDRESS_RE_ROAD = re.compile(
    r"[\u4e00-\u9fff]{2,8}(?:路|街|巷|大道)[\u4e00-\u9fff0-9]{0,8}(?:号|号院|号楼)"
)
# A structured address can stop immediately before a building/room tail
# ("...88号" + "3号楼2单元301室"); extend through that tail so no door number
# or room number is left behind.
ADDRESS_TAIL_RE = re.compile(
    r"^(?:[0-9]{0,4})?(?:(?:号楼|栋|幢|单元|室|层|户|楼)[\u4e00-\u9fff0-9]{0,8}){1,4}"
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(?:https?://|www\.)[A-Za-z0-9./?=&_%#:\-]+", re.IGNORECASE)
QQ_RE = re.compile(r"(?:微信号?|QQ号?|qq|扣扣)\s*[:：]?\s*(?P<v>[A-Za-z0-9_\-]{5,20})")
PASSWORD_LATIN_RE = re.compile(
    r"(?:密码|口令)[^。！？]{0,4}(?P<pw>[A-Za-z][A-Za-z0-9]{3,19})"
)
PLATE_RE = re.compile(
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
    r"[A-HJ-NP-Z][A-HJ-NP-Z0-9]{5,6}"
)

# (kind, keywords, min_len, max_len, rule)
CONTEXT_RULES = (
    # Local landline-style numbers only become PII when the sentence says it is
    # a contact number; 7-8 digits alone (amounts, durations) are left alone.
    ("PHONE", ("电话", "座机", "手机", "号码", "联系", "回拨", "拨打"), 7, 8, "landline"),
    ("VERIFICATION_CODE", ("验证码", "校验码", "动态码", "短信码", "确认码", "授权码"),
     4, 8, "verification_context"),
    ("PASSWORD", ("密码", "口令"), 4, 20, "password_context"),
    ("ACCOUNT", ("账号", "帐号", "账户", "卡号", "银行卡", "信用卡", "储蓄卡",
                 "户号", "工号", "订单号", "订单", "流水号", "会员号", "宽带账号",
                 "宽带帐号", "手机号", "号码", "qq", "QQ", "微信"), 6, 24,
     "account_context"),
)
CONTEXT_FILLER = set(" 是为：:，,、")
CONTEXT_PREFIX_WINDOW = 14

# 6-8 digit entities are normally business numbers only when a unit/period
# word (or a price/plan prefix) makes that explicit. Everything else is an
# unclassified identifier and is masked as <ACCOUNT> (F2, conservative).
BUSINESS_SUFFIXES = (
    "元", "块", "折", "GB", "G", "M", "兆", "分钟", "小时", "天", "个月", "月",
    "年", "岁", "度", "分", "秒", "次", "条", "个", "位", "户", "台", "件",
    "份", "公里", "kg", "KG", "%",
)
BUSINESS_PREFIXES = (
    "套餐", "月租", "流量", "通话", "优惠", "合约", "期限", "积分", "余额",
    "费用", "价格", "资费", "赠送", "包含", "每月", "首月", "活动", "办理",
    "生效", "到期", "标准", "第", "共", "约",
)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    flag: str | None = None
    rule: str | None = None


@dataclass
class Redaction:
    text: str
    hits: Counter
    flags: list[str]
    ambiguous: list[str]
    rules: Counter = field(default_factory=Counter)


class Redactor:
    """Deterministic entity redactor. Stateless per call, cheap to reuse."""

    def redact(self, text: str, extra_spans: Sequence[Span] = ()) -> Redaction:
        hits: Counter = Counter()
        rules: Counter = Counter()
        flags: list[str] = []
        ambiguous: list[str] = []
        runs = list(iter_digit_runs(text))
        spans: list[Span] = self._digit_spans(runs, ambiguous, flags)

        # Context-bound short numbers (landline / account / verification / password).
        for run in runs:
            if any(span.start <= run.start < span.end for span in spans):
                continue
            kind, rule = self._context_kind(text, run)
            if kind:
                spans.append(Span(run.start, run.end, kind, None, rule))

        # F2 hardening: a bare 6-8 digit entity with neither an account/phone
        # context nor an explicit business unit/period context is treated as an
        # unclassified identifier and masked, not released as business data.
        for run in runs:
            if any(span.start <= run.start < span.end for span in spans):
                continue
            if 6 <= len(run.digits) <= 8 and not self._business_context(text, run):
                spans.append(Span(run.start, run.end, "ACCOUNT",
                                  "short_unclassified_digit_masked",
                                  "account_short_unclassified"))

        spans.extend(self._name_spans(text))
        spans.extend(self._address_spans(text, ambiguous))
        spans.extend(self._contact_spans(text))
        spans.extend(extra_spans)

        spans = [span for span in spans if 0 <= span.start < span.end <= len(text)]
        spans.sort(key=lambda span: (span.start, -(span.end - span.start),
                                     SPAN_PRIORITY.get(span.kind, 99)))

        chosen: list[Span] = []
        last_end = -1
        for span in spans:
            if span.start < last_end:
                continue
            chosen.append(span)
            last_end = span.end

        pieces: list[str] = []
        cursor = 0
        for span in chosen:
            pieces.append(text[cursor:span.start])
            pieces.append(PLACEHOLDERS[span.kind])
            cursor = span.end
            hits[span.kind] += 1
            if span.rule:
                rules[span.rule] += 1
            if span.flag:
                flags.append(span.flag)
        pieces.append(text[cursor:])
        masked = "".join(pieces)

        return Redaction(
            text=masked,
            hits=hits,
            flags=list(dict.fromkeys(flags)),
            ambiguous=list(dict.fromkeys(ambiguous)),
            rules=rules,
        )

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _digit_spans(
        runs: Sequence[DigitRun],
        ambiguous: list[str],
        flags: list[str],
    ) -> list[Span]:
        """F1/F2 layered digit classification.

        Order matters: >14-digit entities are ID/card (replaced whole); 11-digit
        entities are direct phones; 9-14-digit entities are checked with an
        11-digit sliding window first and otherwise become accounts. No digit
        entity of 9+ digits is ever released unclassified.
        """
        spans: list[Span] = []
        for run in runs:
            digits = run.digits
            n = len(digits)
            windows = phone_window_offsets(digits)

            if n >= 20:
                spans.append(Span(run.start, run.end, "CARD_NO",
                                  "long_digit_run_masked", "card_long"))
            elif n in (15, 18):
                rule = "id_15" if n == 15 else "id_18"
                spans.append(Span(run.start, run.end, "ID_NO", None, rule))
                valid = valid_id15(digits) if n == 15 else valid_id18(digits)
                if not valid:
                    flags.append("id_pattern_masked")
            elif 16 <= n <= 19:
                spans.append(Span(run.start, run.end, "CARD_NO", None, "card_16_19"))
            elif n == 11 and digits[0] == "1" and digits[1] in PHONE_PREFIX_SECOND:
                spans.append(Span(run.start, run.end, "PHONE", None, "phone_11"))
            elif 9 <= n <= 14:
                if windows:
                    # A phone is embedded inside a longer (possibly punctuated)
                    # entity: replace the *entire* entity so no digits survive.
                    spans.append(Span(run.start, run.end, "PHONE",
                                      "phone_embedded_long_run", "phone_embedded"))
                elif n in (10, 11, 12) and digits[0] in "01":
                    ambiguous.append("AMBIGUOUS_PHONE")
                    flags.append("ambiguous_phone_candidate")
                else:
                    spans.append(Span(run.start, run.end, "ACCOUNT",
                                      "account_long_run_masked", "account_long"))
            elif n in (10, 11, 12) and digits[0] in "01":
                ambiguous.append("AMBIGUOUS_PHONE")
                flags.append("ambiguous_phone_candidate")
        return spans

    @staticmethod
    def _context_kind(text: str, run: DigitRun) -> tuple[str | None, str | None]:
        prefix = text[max(0, run.start - CONTEXT_PREFIX_WINDOW): run.start]
        n = len(run.digits)
        for kind, keywords, low, high, rule in CONTEXT_RULES:
            if not (low <= n <= high):
                continue
            for keyword in keywords:
                idx = prefix.rfind(keyword)
                if idx < 0:
                    continue
                between = prefix[idx + len(keyword):]
                if len(between) <= 4 and all(ch in CONTEXT_FILLER for ch in between):
                    return kind, rule
        return None, None

    @staticmethod
    def _business_context(text: str, run: DigitRun) -> bool:
        tail = text[run.end:run.end + 4]
        head = text[max(0, run.start - 8):run.start]
        return (any(tail.startswith(suffix) for suffix in BUSINESS_SUFFIXES)
                or any(head.endswith(prefix) for prefix in BUSINESS_PREFIXES))

    @staticmethod
    def _name_spans(text: str) -> list[Span]:
        spans: list[Span] = []
        for match in NAME_RE.finditer(text):
            spans.append(Span(match.start("name"), match.end("name"), "NAME",
                              None, "name_honorific"))
        for match in XING_RE.finditer(text):
            spans.append(Span(match.start("surname"), match.end("surname"), "NAME",
                              None, "name_xing"))
        for match in WOJIAO_RE.finditer(text):
            if match.group("name") not in NAME_STOPWORDS:
                spans.append(Span(match.start("name"), match.end("name"), "NAME",
                                  None, "name_wojiao"))
        return spans

    @staticmethod
    def _address_spans(text: str, ambiguous: list[str]) -> list[Span]:
        """F6: bounded window that reaches the sentence end (max 80 chars).

        If a trigger yields address markers but the sentence does not end inside
        the window, the boundary is unprovable and the whole unit is quarantined
        instead of leaving a tail (door/building/room) behind. Structured
        matches are extended through any immediately following building/room
        tail so a door or room number can never survive alone.
        """
        raw_spans: list[Span] = []
        for trigger in ADDRESS_TRIGGERS:
            for match in re.finditer(re.escape(trigger), text):
                pos = match.end()
                while pos < len(text) and text[pos] in ADDRESS_FILLERS:
                    pos += 1
                limit = min(len(text), pos + ADDRESS_MAX_WINDOW)
                end = pos
                while end < limit and text[end] not in ADDRESS_SENTENCE_BREAKS:
                    end += 1
                boundary_truncated = (
                    end == limit
                    and limit < len(text)
                    and text[end] not in ADDRESS_SENTENCE_BREAKS
                )
                window = text[pos:end]
                marker_hits = sum(1 for ch in ADDRESS_MARKERS if ch in window)
                if len(window) >= 3 and marker_hits >= 1:
                    # Include the trigger itself so it cannot out-rank a
                    # structured match that starts earlier but stops sooner.
                    raw_spans.append(Span(match.start(), end, "ADDRESS",
                                          None, "address_trigger"))
                    if boundary_truncated:
                        ambiguous.append("ADDRESS_BOUNDARY")
                elif trigger in PERSONAL_ADDRESS_TRIGGERS and len(window) >= 2:
                    ambiguous.append("ADDRESS_AMBIGUOUS")
        for pattern in (ADDRESS_RE_URBAN, ADDRESS_RE_COMMUNITY, ADDRESS_RE_ROAD):
            for match in pattern.finditer(text):
                raw_spans.append(Span(match.start(), match.end(), "ADDRESS",
                                      None, "address_structured"))

        spans: list[Span] = []
        for span in raw_spans:
            tail = ADDRESS_TAIL_RE.match(text, span.end)
            end = tail.end() if tail else span.end
            spans.append(Span(span.start, min(end, len(text)), "ADDRESS",
                              span.flag, span.rule))
        return spans

    @staticmethod
    def _contact_spans(text: str) -> list[Span]:
        spans: list[Span] = []
        for match in EMAIL_RE.finditer(text):
            spans.append(Span(match.start(), match.end(), "EMAIL", None, "email"))
        for match in URL_RE.finditer(text):
            spans.append(Span(match.start(), match.end(), "URL", None, "url"))
        for match in QQ_RE.finditer(text):
            spans.append(Span(match.start("v"), match.end("v"), "CONTACT", None, "contact"))
        for match in PASSWORD_LATIN_RE.finditer(text):
            spans.append(Span(match.start("pw"), match.end("pw"), "PASSWORD",
                              None, "password_latin"))
        for match in PLATE_RE.finditer(text):
            spans.append(Span(match.start(), match.end(), "PLATE", None, "plate"))
        return spans


DEFAULT_REDACTOR = Redactor()


# ---------------------------------------------------------------------------
# Split-phone detection across adjacent records
# ---------------------------------------------------------------------------

SPLIT_MAX_GAP_MS = 1500
SPLIT_AMBIGUOUS_MAX_GAP_MS = 3000
SPLIT_MIN_FRAGMENT = 3
SPLIT_MAX_FRAGMENT = 8


def _leading_run(text: str) -> DigitRun | None:
    for run in iter_digit_runs(text):
        if not text[:run.start].strip("".join(BOUNDARY_FILLER)):
            return run
        return None
    return None


def _trailing_run(text: str) -> DigitRun | None:
    last = None
    for run in iter_digit_runs(text):
        last = run
    if last is None:
        return None
    if text[last.end:].strip("".join(BOUNDARY_FILLER)):
        return None
    return last


def _whole_run(text: str) -> DigitRun | None:
    """Return the digit run when the whole payload is only that digit entity."""
    runs = list(iter_digit_runs(text))
    if len(runs) != 1:
        return None
    run = runs[0]
    if text[:run.start].strip("".join(BOUNDARY_FILLER)):
        return None
    if text[run.end:].strip("".join(BOUNDARY_FILLER)):
        return None
    return run


def detect_split_phones(
    records: Sequence[RawRecord],
    infos: Sequence[tuple[str, str | None, list[str]]],
) -> tuple[dict[int, list[Span]], list[tuple[int, str]], int]:
    """F1.3: limited cross-record detection.

    Merge only when: same transcript unit, both records SPEECH, same channel
    (speaker), adjacent (no EVENT between), gap within 1500 ms, and the
    trailing/leading fragments compose exactly one valid 11-digit mobile.
    Two- and three-record fragments are handled. If the same composition is
    plausible but the channel/gap constraints fail, the unit is quarantined
    instead of being silently kept.

    Returns (extra spans by seq, ambiguous (seq, reason) pairs, merged groups).
    """
    extra: dict[int, list[Span]] = defaultdict(list)
    ambiguous: list[tuple[int, str]] = []
    merged_groups = 0

    for i in range(len(records) - 1):
        first, second = records[i], records[i + 1]
        if infos[i][0] != "SPEECH" or infos[i + 1][0] != "SPEECH":
            continue
        tail = _trailing_run(first.payload)
        head = _leading_run(second.payload)
        if tail is None or head is None:
            continue
        if not (SPLIT_MIN_FRAGMENT <= len(tail.digits) <= SPLIT_MAX_FRAGMENT):
            continue
        if not (SPLIT_MIN_FRAGMENT <= len(head.digits) <= SPLIT_MAX_FRAGMENT):
            continue
        if len(tail.digits) + len(head.digits) != 11:
            continue
        combined = tail.digits + head.digits
        if combined[0] != "1" or combined[1] not in PHONE_PREFIX_SECOND:
            continue
        gap = second.start_ms - first.end_ms
        if first.channel == second.channel and -50 <= gap <= SPLIT_MAX_GAP_MS:
            extra[first.seq].append(Span(tail.start, tail.end, "PHONE",
                                         "phone_split_merged", "phone_split"))
            extra[second.seq].append(Span(head.start, head.end, "PHONE",
                                          "phone_split_merged", "phone_split"))
            merged_groups += 1
        elif gap <= SPLIT_AMBIGUOUS_MAX_GAP_MS:
            ambiguous.append((first.seq, "SPLIT_PHONE_AMBIGUOUS"))
            ambiguous.append((second.seq, "SPLIT_PHONE_AMBIGUOUS"))

    # Three-record split: tail fragment + a pure-digit middle record + head.
    for i in range(len(records) - 2):
        first, middle, last = records[i], records[i + 1], records[i + 2]
        if not all(infos[i + offset][0] == "SPEECH" for offset in range(3)):
            continue
        if not (first.channel == middle.channel == last.channel):
            continue
        gap1 = middle.start_ms - first.end_ms
        gap2 = last.start_ms - middle.end_ms
        if not (-50 <= gap1 <= SPLIT_MAX_GAP_MS and -50 <= gap2 <= SPLIT_MAX_GAP_MS):
            continue
        tail = _trailing_run(first.payload)
        body = _whole_run(middle.payload)
        head = _leading_run(last.payload)
        if tail is None or body is None or head is None:
            continue
        lengths = (len(tail.digits), len(body.digits), len(head.digits))
        if not all(SPLIT_MIN_FRAGMENT <= length <= SPLIT_MAX_FRAGMENT for length in lengths):
            continue
        if sum(lengths) != 11:
            continue
        combined = tail.digits + body.digits + head.digits
        if combined[0] != "1" or combined[1] not in PHONE_PREFIX_SECOND:
            continue
        for record, run in ((first, tail), (middle, body), (last, head)):
            extra[record.seq].append(Span(run.start, run.end, "PHONE",
                                          "phone_split_merged", "phone_split"))
        merged_groups += 1

    return extra, ambiguous, merged_groups


# ---------------------------------------------------------------------------
# Record / call construction
# ---------------------------------------------------------------------------

def make_quarantine(
    transcript_id: str, reasons: Iterable[str], seqs: Iterable[int]
) -> dict:
    return {
        "transcript_id": transcript_id,
        "reason_codes": sorted(set(reasons)),
        "affected_seq": sorted(set(seqs)),
        "status": "QUARANTINED",
    }


def _new_stats() -> dict:
    return {
        "records": 0,
        "speech": 0,
        "events": 0,
        "speech_records": 0,
        "event_records": 0,
        "hits": Counter(),
        "rules": Counter(),
        "record_kinds": Counter(),
        "record_rules": Counter(),
        "events_dist": Counter(),
        "asr_flags": Counter(),
        "split_groups": 0,
    }


def process_transcript(
    raw: bytes,
    transcript_id: str,
    redactor: Redactor | None = None,
) -> tuple[dict | None, dict | None, dict]:
    """Return (call, quarantine, stats); exactly one of call/quarantine is set."""
    redactor = redactor or DEFAULT_REDACTOR
    stats = _new_stats()
    try:
        records = parse_transcript(raw)
    except ParseError as exc:
        return None, make_quarantine(transcript_id, [exc.code], []), stats

    stats["records"] = len(records)
    if any(record.gender == "儿童" for record in records):
        child_seqs = [record.seq for record in records if record.gender == "儿童"]
        infos = [classify_payload(record.payload) for record in records]
        stats["events"] = sum(1 for info in infos if info[0] == "EVENT")
        stats["speech"] = len(infos) - stats["events"]
        stats["event_records"] = stats["events"]
        stats["speech_records"] = stats["speech"]
        for payload_type, event_code, _ in infos:
            if payload_type == "EVENT" and event_code:
                stats["events_dist"][event_code] += 1
        return None, make_quarantine(transcript_id, ["CHILD_CHANNEL"], child_seqs), stats

    infos = [classify_payload(record.payload) for record in records]
    split_spans, split_ambiguous, split_groups = detect_split_phones(records, infos)
    stats["split_groups"] += split_groups

    built: list[dict] = []
    ambiguous: list[str] = []
    ambiguous_seqs: list[int] = []
    total_speech = 0
    total_event = 0

    for (seq, reason) in split_ambiguous:
        ambiguous.append(reason)
        ambiguous_seqs.append(seq)

    for record, (payload_type, event_code, base_flags) in zip(records, infos):
        if payload_type == "EVENT":
            total_event += 1
            stats["event_records"] += 1
            stats["events_dist"][event_code or "UNKNOWN"] += 1
            stats["asr_flags"].update(set(base_flags))
            built.append({
                "seq": record.seq,
                "start_ms": record.start_ms,
                "end_ms": record.end_ms,
                "channel_id": record.channel,
                "role": "UNKNOWN",
                "payload_type": "EVENT",
                "event": event_code,
                "text_masked": None,
                "is_truncated": False,
                "asr_flags": list(base_flags),
                "redaction_flags": [],
            })
            continue

        total_speech += 1
        stats["speech_records"] += 1
        flags = list(base_flags)
        body = record.payload
        truncated = False
        if body.endswith("+"):
            truncated = True
            body = body[:-1]
        if "+" in body:
            flags.append("ambiguous_plus_position")
            ambiguous.append("AMBIGUOUS_PLUS")
            ambiguous_seqs.append(record.seq)
            body = body.replace("+", "")

        result = redactor.redact(body, extra_spans=split_spans.get(record.seq, ()))
        flags.extend(result.flags)
        masked = result.text
        if "￥" in masked:
            masked = masked.replace("￥", "<ASR_TOKEN>")
            flags.append("yen_token")
        if masked == "":
            flags.append("empty_after_mask")
        stats["hits"].update(result.hits)
        stats["rules"].update(result.rules)
        for kind in set(result.hits):
            stats["record_kinds"][kind] += 1
        for rule in set(result.rules):
            stats["record_rules"][rule] += 1
        stats["asr_flags"].update(set(flags))
        if result.ambiguous:
            ambiguous.extend(result.ambiguous)
            ambiguous_seqs.append(record.seq)

        built.append({
            "seq": record.seq,
            "start_ms": record.start_ms,
            "end_ms": record.end_ms,
            "channel_id": record.channel,
            "role": "UNKNOWN",
            "payload_type": "SPEECH",
            "event": None,
            "text_masked": masked,
            "is_truncated": truncated,
            "asr_flags": list(dict.fromkeys(flags)),
            "redaction_flags": sorted(result.hits),
        })

    stats["speech"] = total_speech
    stats["events"] = total_event

    if ambiguous:
        return None, make_quarantine(transcript_id, ambiguous, ambiguous_seqs), stats

    call = {
        "schema_version": SCHEMA_VERSION,
        "transcript_id": transcript_id,
        "duration_ms": max(record.end_ms for record in records),
        "records": built,
    }
    return call, None, stats


# ---------------------------------------------------------------------------
# Filesystem helpers: snapshot and read-only source access
# ---------------------------------------------------------------------------

def read_transcript(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def snapshot_directory(root: Path) -> tuple[list[tuple[str, int, str]], str]:
    """Return (in-memory entries, aggregate sha256). No data leaves the process."""
    entries: list[tuple[str, int, str]] = []
    hasher = hashlib.sha256()
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        data = read_transcript(path)
        digest = hashlib.sha256(data).hexdigest()
        entries.append((path.name, len(data), digest))
        hasher.update(path.name.encode("utf-8"))
        hasher.update(str(len(data)).encode("ascii"))
        hasher.update(bytes.fromhex(digest))
    return entries, hasher.hexdigest()


# ---------------------------------------------------------------------------
# Output validation (F10/F11)
# ---------------------------------------------------------------------------

FORBIDDEN_KEYS = {
    "raw_text", "payload", "gender", "language", "file_name", "source_path",
    "original_text", "raw", "phone", "id_number", "address", "customer_name",
}


def collect_keys(obj) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(key)
            keys |= collect_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            keys |= collect_keys(item)
    return keys


def _placeholder_problems(text: str) -> list[str]:
    problems: list[str] = []
    for placeholder in PLACEHOLDER_RE.findall(text):
        if placeholder not in ALLOWED_PLACEHOLDERS:
            problems.append("bad_placeholder")
            break
    if "<" in PLACEHOLDER_RE.sub("", text) or ">" in PLACEHOLDER_RE.sub("", text):
        problems.append("stray_angle_bracket")
    return problems


def validate_call_schema(call: dict) -> list[str]:
    """Full pre-publication structural gate for one transcript unit."""
    problems: list[str] = []
    for key in ("schema_version", "transcript_id", "duration_ms", "records"):
        if key not in call:
            problems.append("missing:" + key)
    if call.get("schema_version") != SCHEMA_VERSION:
        problems.append("bad_schema_version")
    transcript_id = call.get("transcript_id")
    if not isinstance(transcript_id, str) or not TRANSCRIPT_ID_RE.match(transcript_id):
        problems.append("bad_transcript_id")
    records = call.get("records")
    if not isinstance(records, list) or not records:
        problems.append("empty_records")
        records = []
    duration = call.get("duration_ms")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        problems.append("bad_duration")

    prev_start: int | None = None
    prev_end: int | None = None
    max_end = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            problems.append("record_not_object")
            continue
        if set(record) != RECORD_KEY_SET:
            problems.append("record_key_set")
        seq = record.get("seq")
        if seq != index:
            problems.append("bad_seq")
        start = record.get("start_ms")
        end = record.get("end_ms")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            problems.append("bad_start_ms")
            start = None
        if not isinstance(end, int) or isinstance(end, bool) or end < 0:
            problems.append("bad_end_ms")
            end = None
        if start is not None and end is not None:
            if end < start:
                problems.append("end_before_start")
            if prev_start is not None and start < prev_start:
                problems.append("time_not_monotonic")
            if prev_end is not None and start < prev_end:
                problems.append("time_overlap")
            prev_start, prev_end = start, end
            max_end = max(max_end, end)

        if record.get("channel_id") not in CHANNELS:
            problems.append("bad_channel")
        if record.get("role") not in ALLOWED_ROLES:
            problems.append("bad_role")
        payload_type = record.get("payload_type")
        if payload_type not in ("SPEECH", "EVENT"):
            problems.append("bad_payload_type")
        event = record.get("event")
        text = record.get("text_masked")
        if payload_type == "SPEECH":
            if event is not None:
                problems.append("speech_with_event")
            if not isinstance(text, str):
                problems.append("speech_without_text")
        elif payload_type == "EVENT":
            if event not in EVENT_CODES:
                problems.append("bad_event")
            if text is not None:
                problems.append("event_with_text")
        if not isinstance(record.get("is_truncated"), bool):
            problems.append("bad_is_truncated")
        for flag_key in ("asr_flags", "redaction_flags"):
            values = record.get(flag_key)
            if not isinstance(values, list):
                problems.append("bad_" + flag_key)
                continue
            if len(values) != len(set(values)):
                problems.append("duplicate_" + flag_key)
            if not all(isinstance(value, str) for value in values):
                problems.append("bad_" + flag_key + "_type")
                continue
            if flag_key == "asr_flags":
                if not set(values) <= ALLOWED_ASR_FLAGS:
                    problems.append("unknown_asr_flag")
            else:
                if not set(values) <= ALLOWED_REDACTION_KINDS:
                    problems.append("unknown_redaction_flag")

        if isinstance(text, str):
            if "\n" in text or "\r" in text or "\t" in text:
                problems.append("text_control_char")
            problems.extend(_placeholder_problems(text))
            if "￥" in text:
                problems.append("yen_token_left")
            if text == "":
                if not record.get("is_truncated"):
                    problems.append("empty_text_not_truncated")
                if "empty_after_mask" not in record.get("asr_flags", []):
                    problems.append("empty_text_without_flag")

    if isinstance(duration, int) and not isinstance(duration, bool) and max_end != duration:
        problems.append("duration_mismatch")

    bad_keys = collect_keys(call) & FORBIDDEN_KEYS
    if bad_keys:
        problems.extend("forbidden_key:" + key for key in sorted(bad_keys))
    return problems


def validate_dataset(calls: Sequence[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for call in calls:
        transcript_id = call.get("transcript_id", "")
        if transcript_id in seen:
            problems.append("duplicate_transcript_id")
        seen.add(transcript_id)
        problems.extend(validate_call_schema(call))
    return problems


def validate_quarantine(obj: dict) -> list[str]:
    problems: list[str] = []
    if set(obj) != QUARANTINE_KEYS:
        problems.append("quarantine_key_set")
    if not isinstance(obj.get("transcript_id"), str) or not TRANSCRIPT_ID_RE.match(
            obj.get("transcript_id", "")):
        problems.append("bad_transcript_id")
    reasons = obj.get("reason_codes")
    if not isinstance(reasons, list) or not reasons or not all(
            isinstance(item, str) and item for item in reasons):
        problems.append("bad_reason_codes")
    seqs = obj.get("affected_seq")
    if not isinstance(seqs, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in seqs):
        problems.append("bad_affected_seq")
    if obj.get("status") != "QUARANTINED":
        problems.append("bad_status")
    return problems


# ---------------------------------------------------------------------------
# Path safety (F4)
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "data" / "deidentified"


def validate_output_path(source_dir: Path, output_dir: Path, approved_root: Path) -> Path:
    """Resolve and validate paths *before* creating any directory.

    Rejects output == input, output inside input, output parent inside input,
    input inside output, and output outside the approved data root; follows
    symlinks before deciding.
    """
    source = Path(source_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve(strict=False)
    root = Path(approved_root).expanduser().resolve(strict=False)

    if output == source:
        raise PathSafetyError("OUTPUT_IS_INPUT")
    if _is_within(output, source):
        raise PathSafetyError("OUTPUT_INSIDE_INPUT")
    if _is_within(output.parent, source):
        raise PathSafetyError("OUTPUT_PARENT_INSIDE_INPUT")
    if _is_within(source, output):
        raise PathSafetyError("INPUT_INSIDE_OUTPUT")
    if output == root or not _is_within(output, root):
        raise PathSafetyError("OUTPUT_OUTSIDE_APPROVED_ROOT")
    if os.path.lexists(output):
        raise PathSafetyError("OUTPUT_EXISTS")
    return output


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def iter_source_files(root: Path) -> list[Path]:
    files = [p for p in sorted(root.iterdir(), key=lambda x: x.name) if p.is_file()]
    if not files:
        raise RuntimeError("input directory contains no files")
    non_txt = [p for p in files if p.suffix.lower() != ".txt"]
    if non_txt:
        raise RuntimeError("input directory contains non-txt files")
    return files


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            count += 1
    return count


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _counter_dict(counter: Counter) -> dict[str, int]:
    return {key: int(value) for key, value in sorted(counter.items())}


def _sum_counter(stats_by_id: dict[str, dict], key: str, ids: Iterable[str]) -> Counter:
    total: Counter = Counter()
    for transcript_id in ids:
        total.update(stats_by_id[transcript_id][key])
    return total


def _sum_int(stats_by_id: dict[str, dict], key: str, ids: Iterable[str]) -> int:
    return sum(int(stats_by_id[transcript_id][key]) for transcript_id in ids)


def verify_calls_file(path: Path) -> dict:
    """Read back the masked deliverable and re-validate every unit."""
    count = 0
    problems: list[str] = []
    findings: Counter = Counter()
    ids: set[str] = set()
    from privacy_scan import scan_call  # local, no network

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                problems.append("blank_line")
                continue
            count += 1
            try:
                call = json.loads(line)
            except json.JSONDecodeError:
                problems.append("json_decode")
                continue
            transcript_id = call.get("transcript_id", "")
            if transcript_id in ids:
                problems.append("duplicate_transcript_id")
            ids.add(transcript_id)
            problems.extend(validate_call_schema(call))
            findings.update(scan_call(call))
    return {
        "calls": count,
        "unique_ids": len(ids),
        "problems": problems[:50],
        "problem_count": len(problems),
        "finding_codes": _counter_dict(findings),
    }


def verify_quarantine_file(path: Path) -> dict:
    problems: list[str] = []
    count = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                problems.append("blank_line")
                continue
            count += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                problems.append("json_decode")
                continue
            problems.extend(validate_quarantine(obj))
    return {"entries": count, "problem_count": len(problems), "problems": problems[:20]}


OUTPUT_README = """# cm_outbound_v2 — de-identified outbound transcripts

This directory is produced by `cm-outbound-bench/data_pipeline/deidentify.py`
(pipeline 2.0.0, schema 1.0). It contains **no raw transcript text**, no
original file names, no phone numbers, no personal names, no addresses and no
gender/language profiles.

## Files

| File | Content |
|---|---|
| `calls.jsonl` | One JSON object per delivered transcript unit, all records masked. |
| `quarantine.jsonl` | Units withheld from delivery; opaque id + reason codes only. |
| `manifest.json` | Runtime, counts, redaction statistics, scans and integrity data. |
| `README.md` | This document. |

## Record schema

```
{
  "schema_version": "1.0",
  "transcript_id": "CALL_<24 hex chars>",
  "duration_ms": <int>,
  "records": [
    {
      "seq": <int>,
      "start_ms": <int>,
      "end_ms": <int>,
      "channel_id": <1..5>,
      "role": "UNKNOWN",
      "payload_type": "SPEECH" | "EVENT",
      "event": null | "SYSTEM" | "ENS" | "DTMF" | "MUSIC" | "SONANT"
                     | "LAUGH" | "UNCLASSIFIED" | "UNKNOWN",
      "text_masked": <string> | null,
      "is_truncated": <bool>,
      "asr_flags": [<string>],
      "redaction_flags": [<string>]
    }
  ]
}
```

* `role` is always `UNKNOWN`: the source has no confirmed agent/customer
  mapping. Channel ids are preserved verbatim.
* `event` records carry no text; `text_masked` is `null`.
* `is_truncated` is the stripped trailing `+` marker.
* Placeholders: `<PHONE>`, `<NAME>`, `<ID_NO>`, `<ADDRESS>`, `<ACCOUNT>`,
  `<CARD_NO>`, `<VERIFICATION_CODE>`, `<PASSWORD>`, `<EMAIL>`, `<URL>`,
  `<CONTACT>`, `<PLATE>`, `<ASR_TOKEN>` (unexplained leading `￥`).

## v2 privacy changes

* An 11-digit mobile candidate is searched inside every normalised digit
  entity, including 12-14 digit runs and runs split by punctuation. The whole
  entity becomes `<PHONE>`.
* 9-14 digit entities that are not phones become `<ACCOUNT>`; malformed
  10-12 digit phone-like runs are quarantined.
* Address windows reach the sentence end (max 80 chars); an unprovable
  boundary quarantines the unit.
* Only fixed event tags (or strict uppercase vendor tags) are treated as
  events; other bracketed speech is preserved as text.
* Every delivered unit passes a structural gate before publication.

## Known limitations

* One transcript unit is not proven to equal one phone call; there is no
  `call_id` in the source.
* The `+`, `[*]` and `ENS` vendor semantics are inferred, not confirmed.
* Ambiguous phone/address candidates are quarantined (fail-closed), so
  coverage is intentionally lower than the source file count.
* De-identification is rule-based; a sampled residual-risk review is still
  required before any external use.
"""


def run(args: argparse.Namespace) -> int:
    if sys.version_info[:3] != REQUIRED_PYTHON or platform.python_implementation() != "CPython":
        print("ERROR: CPython 3.12.16 is required for v2", file=sys.stderr)
        return 8

    source_dir = Path(args.input).expanduser().resolve()
    if not source_dir.is_dir():
        print("ERROR: input directory does not exist", file=sys.stderr)
        return 2

    approved_root = Path(args.output_root).expanduser().resolve(strict=False)
    try:
        output_dir = validate_output_path(source_dir, Path(args.output), approved_root)
    except PathSafetyError as exc:
        print("ERROR: output path rejected: " + exc.code, file=sys.stderr)
        return 3 if exc.code == "OUTPUT_EXISTS" else 7

    project_root = Path(__file__).resolve().parents[1]
    key_root = Path(args.key_root).expanduser().resolve(strict=False)
    try:
        key = ensure_key(
            Path(args.key_path),
            [source_dir, output_dir, project_root, approved_root],
            key_root,
        )
    except PathSafetyError as exc:
        print("ERROR: key rejected: " + exc.code, file=sys.stderr)
        return 12

    started = time.time()
    try:
        files = iter_source_files(source_dir)
        pre_entries, pre_sha = snapshot_directory(source_dir)
    except OSError:
        print("ERROR: source read failure", file=sys.stderr)
        return 11
    except RuntimeError:
        print("ERROR: invalid input directory layout", file=sys.stderr)
        return 2
    pre_bytes = sum(size for _, size, _ in pre_entries)
    baseline = args.expect_baseline_sha256
    if baseline and pre_sha != baseline:
        print("ERROR: source baseline fingerprint mismatch; aborting", file=sys.stderr)
        return 4

    redactor = Redactor()
    calls: list[dict] = []
    quarantined: list[dict] = []
    reason_counts: Counter = Counter()
    stats_by_id: dict[str, dict] = {}
    source_records = 0
    source_speech = 0
    source_events = 0

    for index, path in enumerate(files, start=1):
        transcript_id = make_transcript_id(key, path.name)
        try:
            raw = read_transcript(path)
        except OSError:
            print("ERROR: source read failure", file=sys.stderr)
            return 11
        call, quarantine, stats = process_transcript(raw, transcript_id, redactor)
        stats_by_id[transcript_id] = stats
        source_records += stats["records"]
        source_speech += stats["speech"]
        source_events += stats["events"]
        if quarantine is not None:
            quarantined.append(quarantine)
            reason_counts.update(quarantine["reason_codes"])
        else:
            problems = validate_call_schema(call)
            if problems:
                quarantined.append(make_quarantine(transcript_id, ["SCHEMA_INVALID"], []))
                reason_counts["SCHEMA_INVALID"] += 1
            else:
                calls.append(call)
        if index % 500 == 0:
            print(f"progress: {index}/{len(files)}", file=sys.stderr, flush=True)

    calls.sort(key=lambda c: c["transcript_id"])

    # Independent privacy rescan of every deliverable candidate before writing.
    from privacy_scan import scan_call  # local, no network

    residual_calls: list[dict] = []
    scan_findings: Counter = Counter()
    clean_calls: list[dict] = []
    for call in calls:
        findings = scan_call(call)
        if findings:
            residual_calls.append(call)
            for code in findings:
                scan_findings[code] += 1
        else:
            clean_calls.append(call)
    for call in residual_calls:
        quarantined.append(make_quarantine(call["transcript_id"], ["PRIVACY_RESIDUAL"], []))
        reason_counts["PRIVACY_RESIDUAL"] += 1
    calls = clean_calls

    # Second pass must be clean; otherwise the scanner/redactor contract fails.
    second_pass_findings: Counter = Counter()
    for call in calls:
        for code in scan_call(call):
            second_pass_findings[code] += 1

    dataset_problems = validate_dataset(calls)
    if dataset_problems:
        print("ERROR: dataset structural gate failed; nothing published", file=sys.stderr)
        print("problem_count=" + str(len(dataset_problems)), file=sys.stderr)
        return 9

    quarantined.sort(key=lambda q: q["transcript_id"])

    delivered_ids = {call["transcript_id"] for call in calls}
    quarantined_ids = {entry["transcript_id"] for entry in quarantined}
    all_ids = set(stats_by_id)
    missing_stats = (delivered_ids | quarantined_ids) - all_ids
    if missing_stats:
        print("ERROR: internal statistics mismatch; nothing published", file=sys.stderr)
        return 9

    speech_out = sum(1 for call in calls for record in call["records"]
                     if record["payload_type"] == "SPEECH")
    event_out = sum(1 for call in calls for record in call["records"]
                    if record["payload_type"] == "EVENT")
    records_out = speech_out + event_out

    all_hits = _sum_counter(stats_by_id, "hits", all_ids)
    delivered_hits = _sum_counter(stats_by_id, "hits", delivered_ids)
    all_record_kinds = _sum_counter(stats_by_id, "record_kinds", all_ids)
    delivered_record_kinds = _sum_counter(stats_by_id, "record_kinds", delivered_ids)
    all_record_rules = _sum_counter(stats_by_id, "record_rules", all_ids)
    delivered_record_rules = _sum_counter(stats_by_id, "record_rules", delivered_ids)
    all_asr_flags = _sum_counter(stats_by_id, "asr_flags", all_ids)
    delivered_asr_flags = _sum_counter(stats_by_id, "asr_flags", delivered_ids)
    all_events = _sum_counter(stats_by_id, "events_dist", all_ids)
    delivered_events: Counter = Counter()
    for call in calls:
        for record in call["records"]:
            if record["payload_type"] == "EVENT" and record["event"]:
                delivered_events[record["event"]] += 1

    delivered_transcript_kinds: Counter = Counter()
    all_transcript_kinds: Counter = Counter()
    for transcript_id in all_ids:
        for kind in stats_by_id[transcript_id]["record_kinds"]:
            all_transcript_kinds[kind] += 1
    for transcript_id in delivered_ids:
        for kind in stats_by_id[transcript_id]["record_kinds"]:
            delivered_transcript_kinds[kind] += 1

    delivered_rules = _sum_counter(stats_by_id, "rules", delivered_ids)
    all_rules = _sum_counter(stats_by_id, "rules", all_ids)

    def entity_block(hits, kinds, transcripts) -> dict[str, dict]:
        return {
            kind: {
                "occurrences": int(hits.get(kind, 0)),
                "records": int(kinds.get(kind, 0)),
                "transcripts": int(transcripts.get(kind, 0)),
            }
            for kind in sorted(PLACEHOLDERS)
        }

    def rule_block(rules) -> dict[str, dict]:
        return {
            rule: {
                "occurrences": int(rules.get(rule, 0)),
                "records": int(delivered_record_rules.get(rule, 0)),
            }
            for rule in sorted(rules)
        }

    empty_text_records = 0
    empty_text_flagged = 0
    truncated_records = 0
    for call in calls:
        for record in call["records"]:
            if record["payload_type"] != "SPEECH":
                continue
            if record["is_truncated"]:
                truncated_records += 1
            if record["text_masked"] == "":
                empty_text_records += 1
                if "empty_after_mask" in record["asr_flags"]:
                    empty_text_flagged += 1

    quarantined_records = _sum_int(stats_by_id, "records", quarantined_ids)
    quarantined_speech = _sum_int(stats_by_id, "speech", quarantined_ids)
    quarantined_events = _sum_int(stats_by_id, "events", quarantined_ids)

    post_entries, post_sha = snapshot_directory(source_dir)
    post_bytes = sum(size for _, size, _ in post_entries)
    unchanged = pre_entries == post_entries and pre_sha == post_sha

    scanner_module_path = Path(sys.modules["privacy_scan"].__file__).resolve()
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "dataset": DATASET_NAME,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "pipeline_source_sha256": file_sha256(Path(__file__).resolve()),
            "scanner_source_sha256": file_sha256(scanner_module_path),
            "schema_version": SCHEMA_VERSION,
            "manifest_version": MANIFEST_VERSION,
            "stdlib_only": True,
            "network_used": False,
        },
        "environment": {
            "required_python": "3.12.16",
            "actual_python": platform.python_version(),
            "python_match": sys.version_info[:3] == REQUIRED_PYTHON,
        },
        "input": {
            "files": len(pre_entries),
            "bytes": pre_bytes,
            "aggregate_sha256": pre_sha,
            "baseline_expected_sha256": baseline,
            "baseline_match": (pre_sha == baseline) if baseline else None,
            "records": source_records,
            "speech_records": source_speech,
            "event_records": source_events,
        },
        "output": {
            "directory": DATASET_NAME,
            "calls": len(calls),
            "quarantined": len(quarantined),
            "records": records_out,
            "speech_records": speech_out,
            "event_records": event_out,
        },
        "granularity": {
            "occurrences": "entity replacement spans applied to all processed speech",
            "records": "records containing at least one replacement of that entity",
            "transcripts": "transcript units containing at least one replacement",
            "asr_flags": "record level (deduplicated per record)",
            "quarantine_by_reason": "unit level; a unit with two reasons counts once per reason",
        },
        "processing": {
            "entities": entity_block(delivered_hits, delivered_record_kinds,
                                     delivered_transcript_kinds),
            "entities_all_processed": entity_block(all_hits, all_record_kinds,
                                                   all_transcript_kinds),
            "phone": {
                "rules": {
                    "mobile_11digit": int(delivered_record_rules.get("phone_11", 0)),
                    "mobile_embedded_long_run": int(delivered_record_rules.get("phone_embedded", 0)),
                    "mobile_split_across_records": int(delivered_record_rules.get("phone_split", 0)),
                    "landline_with_context": int(delivered_record_rules.get("landline", 0)),
                },
                "rule_occurrences": {
                    "mobile_11digit": int(delivered_rules.get("phone_11", 0)),
                    "mobile_embedded_long_run": int(delivered_rules.get("phone_embedded", 0)),
                    "mobile_split_across_records": int(delivered_rules.get("phone_split", 0)),
                    "landline_with_context": int(delivered_rules.get("landline", 0)),
                },
                "split_groups_merged": _sum_int(stats_by_id, "split_groups", delivered_ids),
                "total_placeholders": int(delivered_hits.get("PHONE", 0)),
            },
            "account": {
                "context": int(delivered_rules.get("account_context", 0)),
                "long_run": int(delivered_rules.get("account_long", 0)),
                "total_placeholders": int(delivered_hits.get("ACCOUNT", 0)),
            },
            "name": {
                "honorific": int(delivered_rules.get("name_honorific", 0)),
                "xing": int(delivered_rules.get("name_xing", 0)),
                "wojiao": int(delivered_rules.get("name_wojiao", 0)),
                "total_placeholders": int(delivered_hits.get("NAME", 0)),
            },
            "address": {
                "trigger": int(delivered_rules.get("address_trigger", 0)),
                "structured": int(delivered_rules.get("address_structured", 0)),
                "total_placeholders": int(delivered_hits.get("ADDRESS", 0)),
            },
            "identity": {
                "id_15": int(delivered_rules.get("id_15", 0)),
                "id_18": int(delivered_rules.get("id_18", 0)),
                "card_16_19": int(delivered_rules.get("card_16_19", 0)),
                "card_long": int(delivered_rules.get("card_long", 0)),
            },
            "other_rules": rule_block(delivered_rules),
            "rules_all_processed": _counter_dict(all_rules),
            "event_distribution": {
                "delivered": _counter_dict(delivered_events),
                "all_processed": _counter_dict(all_events),
            },
            "empty_text": {
                "records": empty_text_records,
                "with_empty_after_mask_flag": empty_text_flagged,
                "truncated_records": truncated_records,
            },
            "asr_flags": {
                "delivered_records": _counter_dict(delivered_asr_flags),
                "all_processed_records": _counter_dict(all_asr_flags),
            },
            "quarantine": {
                "units": len(quarantined),
                "records": quarantined_records,
                "speech_records": quarantined_speech,
                "event_records": quarantined_events,
                "by_reason": _counter_dict(reason_counts),
                "affected_transcripts_by_reason": _counter_dict(reason_counts),
            },
        },
        "redaction_counts": _counter_dict(delivered_hits),
        "redaction_counts_all_processed": _counter_dict(all_hits),
        "quarantine_reason_counts": _counter_dict(reason_counts),
        "privacy_scan": {
            "engine": "privacy_scan.py",
            "independent": True,
            "scanned_calls": len(calls) + len(residual_calls),
            "findings": int(sum(scan_findings.values())),
            "finding_codes": _counter_dict(scan_findings),
            "residual_calls_quarantined": len(residual_calls),
            "second_pass_findings": int(sum(second_pass_findings.values())),
            "second_pass_codes": _counter_dict(second_pass_findings),
        },
        "integrity": {
            "pre_files": len(pre_entries),
            "post_files": len(post_entries),
            "pre_bytes": pre_bytes,
            "post_bytes": post_bytes,
            "pre_sha256": pre_sha,
            "post_sha256": post_sha,
            "unchanged": unchanged,
            "baseline_sha256": baseline,
            "baseline_match": (pre_sha == baseline) if baseline else None,
        },
        "runtime_seconds": round(time.time() - started, 1),
    }

    if not unchanged:
        print("ERROR: source directory changed during processing", file=sys.stderr)
        return 5
    if second_pass_findings:
        print("ERROR: residual privacy findings remain; nothing published", file=sys.stderr)
        print("finding_codes=" + json.dumps(_counter_dict(second_pass_findings),
                                            ensure_ascii=False), file=sys.stderr)
        return 6
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    try:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-cm-outbound-v2-",
                                        dir=str(output_dir.parent)))
    except OSError:
        print("ERROR: staging creation failed", file=sys.stderr)
        return 10

    try:
        write_jsonl(staging / "calls.jsonl", calls)
        write_jsonl(staging / "quarantine.jsonl", quarantined)
        (staging / "README.md").write_text(OUTPUT_README, encoding="utf-8")

        readback = verify_calls_file(staging / "calls.jsonl")
        quarantine_check = verify_quarantine_file(staging / "quarantine.jsonl")
        if readback["calls"] != len(calls) or readback["problem_count"]:
            raise PublishError("READBACK_CALLS_INVALID")
        if readback["finding_codes"]:
            raise PublishError("READBACK_PRIVACY_FINDING")
        if quarantine_check["entries"] != len(quarantined) or quarantine_check["problem_count"]:
            raise PublishError("READBACK_QUARANTINE_INVALID")

        manifest["output"]["files"] = {
            "calls.jsonl": {
                "bytes": (staging / "calls.jsonl").stat().st_size,
                "sha256": file_sha256(staging / "calls.jsonl"),
                "lines": len(calls),
            },
            "quarantine.jsonl": {
                "bytes": (staging / "quarantine.jsonl").stat().st_size,
                "sha256": file_sha256(staging / "quarantine.jsonl"),
                "lines": len(quarantined),
            },
            "README.md": {
                "bytes": (staging / "README.md").stat().st_size,
                "sha256": file_sha256(staging / "README.md"),
            },
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output_dir)
    except PublishError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        print("ERROR: publication gate failed: " + exc.code, file=sys.stderr)
        return 10
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("published: " + DATASET_NAME, file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local-only de-identification pipeline for CM outbound TXT transcripts."
    )
    parser.add_argument("--input", required=True, help="source TXT directory (read-only)")
    parser.add_argument("--output", required=True, help="new output directory (must not exist)")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="approved data output root; the output directory must live below it",
    )
    parser.add_argument(
        "--key-path",
        default=str(Path.home() / KEY_SUBDIR / KEY_FILENAME),
        help="HMAC key file (0600, dedicated key root only)",
    )
    parser.add_argument(
        "--key-root",
        default=str(Path.home() / KEY_SUBDIR),
        help="dedicated directory allowed to contain the HMAC key",
    )
    parser.add_argument(
        "--expect-baseline-sha256",
        default=None,
        help="abort unless the in-memory source fingerprint matches this value",
    )
    parser.add_argument("--dry-run", action="store_true", help="compute statistics only")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
