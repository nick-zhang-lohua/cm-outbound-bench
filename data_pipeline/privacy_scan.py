#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent privacy rescan for de-identified CM outbound transcripts.

This module is a *second, separate* implementation. It never imports
``deidentify`` and re-derives Chinese digit values, normalisation, digit-run
construction and sliding-window detection on its own, so a bug in the primary
redactor does not silently propagate into the verification step.

Detection model (deliberately different from the redactor)
----------------------------------------------------------
* NFKC normalisation followed by an independent digit/offset scan.
* Every 11-character window of every digit entity is tested for ``1[3-9]``,
  including windows inside 12-19 digit runs and runs split by punctuation.
* 9-14 digit entities with no phone window are account candidates.
* 7-8 digit entities near a phone context, 6-8 digit entities near an account
  context, and 15/18-digit ID plus 16-19 digit card patterns are checked.
* Names (surname + honorific, ``姓X``, ``我叫X``), addresses, e-mail/URL/IM,
  placeholder-adjacent digit fragments and cross-record split candidates are
  checked independently.
* Only code / transcript_id / seq / count are emitted. Matched text is never
  stored, returned or printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# Independent digit table (ordered by value, not by the redactor's table).
_SCAN_DIGIT = {
    "零": "0", "〇": "0", "洞": "0",
    "幺": "1", "一": "1",
    "两": "2", "二": "2",
    "三": "3", "四": "4", "五": "5", "六": "6",
    "拐": "7", "七": "7", "八": "8", "九": "9",
}
_SCAN_SEPARATORS = set(" \t，,、;；:：.．·-—~～()（）[]【】\"'“”《》")
_SCAN_STOPS = set("。！？!?\n\r")
_SCAN_GAP = 8  # slightly more tolerant than the redactor on purpose

PHONE_CODE = "PHONE_RESIDUAL"
PHONE_EMBEDDED_CODE = "PHONE_EMBEDDED_RESIDUAL"
PHONE_CONTEXT_CODE = "PHONE_CONTEXT_RESIDUAL"
PHONE_SPLIT_CODE = "PHONE_SPLIT_RESIDUAL"
ID_CODE = "ID_RESIDUAL"
CARD_CODE = "CARD_RESIDUAL"
LONG_CODE = "LONG_DIGIT_RESIDUAL"
ACCOUNT_CODE = "ACCOUNT_RESIDUAL"
VERIFY_CODE = "VERIFICATION_RESIDUAL"
PASSWORD_CODE = "PASSWORD_RESIDUAL"
NAME_CODE = "NAME_RESIDUAL"
ADDRESS_CODE = "ADDRESS_RESIDUAL"
CONTACT_CODE = "CONTACT_RESIDUAL"
PLACEHOLDER_BOUNDARY_CODE = "PLACEHOLDER_BOUNDARY_RESIDUAL"
SHORT_DIGIT_CODE = "SHORT_DIGIT_RESIDUAL"

# A different (re-ordered) surname set. It is data, not an algorithm; keeping
# it separate makes an accidental edit to the redactor table visible here.
_SCAN_SURNAMES = (
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
    "于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江"
    "尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤常温康施文牛樊葛邢安齐"
    "易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌毕"
    "单季裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆"
    "农司卓古吉缪简车项连芦麦褚娄窦戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵苟"
    "邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻苑迟邝"
)
_SURNAME_CLASS = "[" + "".join(dict.fromkeys(_SCAN_SURNAMES)) + "]"
_HONORIFICS = ("总经理", "先生", "女士", "小姐", "老师", "经理", "主任", "总")

_PHONE_CONTEXT = ("电话", "座机", "手机", "号码", "联系", "回拨", "拨打")
_ACCOUNT_CONTEXT = ("账号", "帐号", "账户", "卡号", "银行卡", "信用卡", "储蓄卡",
                    "户号", "工号", "订单", "订单号", "流水号", "会员号", "宽带",
                    "qq", "QQ", "微信")
_VERIFY_CONTEXT = ("验证码", "校验码", "动态码", "短信码", "确认码", "授权码")
_PASSWORD_CONTEXT = ("密码", "口令")
_CONTEXT_FILLER = set(" 是为：:，,、")
# Same business semantics as the redactor, implemented independently.
_BUSINESS_SUFFIXES = ("元", "块", "折", "GB", "G", "M", "兆", "分钟", "小时", "天",
                      "个月", "月", "年", "岁", "度", "分", "秒", "次", "条", "个",
                      "位", "户", "台", "件", "份", "公里", "kg", "KG", "%")
_BUSINESS_PREFIXES = ("套餐", "月租", "流量", "通话", "优惠", "合约", "期限", "积分",
                      "余额", "费用", "价格", "资费", "赠送", "包含", "每月", "首月",
                      "活动", "办理", "生效", "到期", "标准", "第", "共", "约")

_NAME_HON_RE = re.compile(
    _SURNAME_CLASS + r"[\u4e00-\u9fff]{0,2}(?:" + "|".join(_HONORIFICS) + r")"
)
_XING_RE = re.compile(r"姓(?![名称])" + _SURNAME_CLASS)
_WO_NAME_RE = re.compile(r"(?:我叫|名字叫|姓名是|名字是)[\u4e00-\u9fff]{2,3}")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_IM_RE = re.compile(r"(?:微信号?|QQ号?|qq号?|扣扣)\s*[:：]?\s*[A-Za-z0-9_\-]{5,20}")
_PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z0-9_]{1,23}>")

_ADDRESS_TRIGGERS = ("详细地址", "家庭地址", "收货地址", "我的地址", "住址", "家住",
                     "住在", "搬到", "送到", "寄到", "我家", "地址")
_ADDRESS_MARKERS = ("省", "市", "区", "县", "镇", "乡", "街道", "路", "街", "巷",
                    "号", "栋", "幢", "单元", "室", "小区", "村", "组", "楼",
                    "大厦", "公寓", "花园", "家园", "苑", "弄")
_ADDR_REGION_RE = re.compile(
    r"(?:省|自治区)[^。！？]{0,14}(?:市|区|县)[^。！？]{0,14}(?:路|街|巷)[^。！？]{0,10}(?:号|室)"
)
_ADDR_ROAD_RE = re.compile(r"(?:路|街|巷)[^。！？]{0,6}[0-9零〇一二三四五六七八九]{1,4}(?:号|室)")
_ADDR_COMMUNITY_RE = re.compile(
    r"(?:小区|花园|公寓|大厦|家园|苑)[^。！？]{0,12}(?:号楼|栋|幢|单元|室|层)"
)
_ADDR_BUILDING_RE = re.compile(r"(?:号楼|栋|幢)[^。！？]{0,6}(?:单元|室|层|户)")


@dataclass(frozen=True)
class Finding:
    transcript_id: str
    seq: int
    code: str


def _is_digit_char(ch: str) -> str | None:
    if ch in _SCAN_DIGIT:
        return _SCAN_DIGIT[ch]
    if "0" <= ch <= "9":
        return ch
    if "\uff10" <= ch <= "\uff19":
        return chr(ord(ch) - 0xFEE0)
    return None


def _runs(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield (start, end, digits) for every maximal digit entity."""
    start: int | None = None
    values: list[str] = []
    last = 0
    gap = 0
    for index, ch in enumerate(text):
        digit = _is_digit_char(ch)
        if digit is not None:
            if start is None:
                start = index
            values.append(digit)
            last = index + 1
            gap = 0
        elif ch in _SCAN_STOPS:
            if values and start is not None:
                yield start, last, "".join(values)
            start, values, gap = None, [], 0
        elif ch in _SCAN_SEPARATORS:
            if values:
                gap += 1
                if gap > _SCAN_GAP:
                    yield start, last, "".join(values)  # type: ignore[misc]
                    start, values, gap = None, [], 0
        else:
            if values and start is not None:
                yield start, last, "".join(values)
            start, values, gap = None, [], 0
    if values and start is not None:
        yield start, last, "".join(values)


def _phone_windows(digits: str) -> list[int]:
    return [
        index for index in range(len(digits) - 10)
        if digits[index] == "1" and digits[index + 1] in "3456789"
    ]


def _context_before(text: str, start: int, keywords: Sequence[str]) -> bool:
    prefix = text[max(0, start - 16):start]
    for keyword in keywords:
        idx = prefix.rfind(keyword)
        if idx < 0:
            continue
        between = prefix[idx + len(keyword):]
        if len(between) <= 4 and all(ch in _CONTEXT_FILLER for ch in between):
            return True
    return False


def _business_context(text: str, start: int, end: int) -> bool:
    tail = text[end:end + 4]
    head = text[max(0, start - 8):start]
    return (any(tail.startswith(suffix) for suffix in _BUSINESS_SUFFIXES)
            or any(head.endswith(prefix) for prefix in _BUSINESS_PREFIXES))


def _placeholder_boundary(text: str) -> bool:
    for match in _PLACEHOLDER_RE.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if before and _is_digit_char(before) is not None:
            return True
        if after and _is_digit_char(after) is not None:
            return True
    return False


def scan_text(text: str) -> list[str]:
    """Return stable finding codes for one already-masked text field."""
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text)
    codes: set[str] = set()

    for start, _end, digits in _runs(normalized):
        n = len(digits)
        windows = _phone_windows(digits)
        if windows:
            codes.add(PHONE_CODE if n == 11 else PHONE_EMBEDDED_CODE)
            continue
        if n >= 20:
            codes.add(LONG_CODE)
        elif n in (15, 18):
            codes.add(ID_CODE)
        elif 16 <= n <= 19:
            codes.add(CARD_CODE)
        elif 9 <= n <= 14:
            codes.add(PHONE_CODE if n == 11 and digits[0] == "1" else ACCOUNT_CODE)
        elif n in (7, 8) and _context_before(normalized, start, _PHONE_CONTEXT):
            codes.add(PHONE_CONTEXT_CODE)
        elif 6 <= n <= 8 and _context_before(normalized, start, _ACCOUNT_CONTEXT):
            codes.add(ACCOUNT_CODE)
        elif 4 <= n <= 8 and _context_before(normalized, start, _VERIFY_CONTEXT):
            codes.add(VERIFY_CODE)
        elif 4 <= n <= 20 and _context_before(normalized, start, _PASSWORD_CONTEXT):
            codes.add(PASSWORD_CODE)
        elif 6 <= n <= 8 and not _business_context(normalized, start, _end):
            codes.add(SHORT_DIGIT_CODE)

    if _NAME_HON_RE.search(normalized) or _XING_RE.search(normalized) \
            or _WO_NAME_RE.search(normalized):
        codes.add(NAME_CODE)

    address_hit = (
        _ADDR_REGION_RE.search(normalized)
        or _ADDR_ROAD_RE.search(normalized)
        or _ADDR_COMMUNITY_RE.search(normalized)
        or _ADDR_BUILDING_RE.search(normalized)
    )
    if not address_hit:
        for trigger in _ADDRESS_TRIGGERS:
            pos = normalized.find(trigger)
            while pos >= 0:
                window = normalized[pos + len(trigger):pos + len(trigger) + 100]
                window = window.split("。")[0].split("！")[0].split("？")[0]
                marker_hits = sum(1 for marker in _ADDRESS_MARKERS if marker in window)
                if len(window) >= 3 and marker_hits >= 1:
                    address_hit = True
                    break
                pos = normalized.find(trigger, pos + 1)
            if address_hit:
                break
    if address_hit:
        codes.add(ADDRESS_CODE)

    if _EMAIL_RE.search(normalized) or _URL_RE.search(normalized) or _IM_RE.search(normalized):
        codes.add(CONTACT_CODE)
    if _placeholder_boundary(normalized):
        codes.add(PLACEHOLDER_BOUNDARY_CODE)
    return sorted(codes)


def _boundary_run(text: str, leading: bool) -> str | None:
    runs = list(_runs(text))
    if not runs:
        return None
    run = runs[0] if leading else runs[-1]
    start, end, digits = run
    surrounding = text[:start] if leading else text[end:]
    if surrounding.strip(" \t，,、；;：:。.！!？?）)】]\"'“”《》"):
        return None
    return digits


def _whole_digits(text: str) -> str | None:
    runs = list(_runs(text))
    if len(runs) != 1:
        return None
    start, end, digits = runs[0]
    surrounding = text[:start] + text[end:]
    if surrounding.strip(" \t，,、；;：:。.！!？?）)】]\"'“”《》"):
        return None
    return digits


def _composes_phone(fragments: Sequence[str]) -> bool:
    if not all(3 <= len(fragment) <= 8 for fragment in fragments):
        return False
    if sum(len(fragment) for fragment in fragments) != 11:
        return False
    combined = "".join(fragments)
    return combined[0] == "1" and combined[1] in "3456789"


def _split_groups(records: Sequence[dict]) -> list[tuple[int, ...]]:
    """Return seq groups whose boundary fragments compose a phone (2 or 3)."""
    speech = [
        record for record in records
        if record.get("payload_type") == "SPEECH"
        and isinstance(record.get("text_masked"), str)
    ]
    groups: list[tuple[int, ...]] = []
    for index in range(len(speech) - 1):
        first, second = speech[index], speech[index + 1]
        if second.get("seq") != first.get("seq", -1) + 1:
            continue
        tail = _boundary_run(first["text_masked"], leading=False)
        head = _boundary_run(second["text_masked"], leading=True)
        if tail and head and _composes_phone((tail, head)):
            groups.append((first.get("seq", -1), second.get("seq", -1)))
    for index in range(len(speech) - 2):
        first, middle, last = speech[index], speech[index + 1], speech[index + 2]
        if not (middle.get("seq") == first.get("seq", -1) + 1
                and last.get("seq") == middle.get("seq", -1) + 1):
            continue
        tail = _boundary_run(first["text_masked"], leading=False)
        body = _whole_digits(middle["text_masked"])
        head = _boundary_run(last["text_masked"], leading=True)
        if tail and body and head and _composes_phone((tail, body, head)):
            groups.append((first.get("seq", -1), middle.get("seq", -1),
                           last.get("seq", -1)))
    return groups


def _split_codes(records: Sequence[dict]) -> set[str]:
    return {PHONE_SPLIT_CODE} if _split_groups(records) else set()


def scan_call(call: dict) -> list[str]:
    """Return sorted, de-duplicated finding codes for one masked call."""
    codes: set[str] = set()
    records = call.get("records") or []
    for record in records:
        if record.get("payload_type") != "SPEECH":
            continue
        text = record.get("text_masked")
        if isinstance(text, str):
            codes.update(scan_text(text))
    codes.update(_split_codes(records))
    return sorted(codes)


def scan_call_findings(call: dict) -> list[Finding]:
    """Per-record findings; only id / seq / code are returned (no text)."""
    transcript_id = call.get("transcript_id", "")
    findings: list[Finding] = []
    records = call.get("records") or []
    for record in records:
        if record.get("payload_type") != "SPEECH":
            continue
        seq = record.get("seq")
        if not isinstance(seq, int):
            continue
        text = record.get("text_masked")
        if isinstance(text, str):
            for code in scan_text(text):
                findings.append(Finding(transcript_id, seq, code))
    split = _split_codes(records)
    if split:
        for group in _split_groups(records):
            for seq in group:
                findings.append(Finding(transcript_id, seq, PHONE_SPLIT_CODE))
    return findings


def _scan_records_for_call(call: dict) -> list[str]:
    """Backward-compatible alias used by older callers."""
    return scan_call(call)


def scan_jsonl(path: Path, include_findings: bool = False) -> dict:
    calls = 0
    findings = 0
    affected = 0
    by_code: Counter = Counter()
    details: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            calls += 1
            call = json.loads(line)
            codes = scan_call(call)
            if not codes:
                continue
            affected += 1
            findings += len(codes)
            by_code.update(codes)
            if include_findings:
                for finding in scan_call_findings(call):
                    details.append({
                        "code": finding.code,
                        "transcript_id": finding.transcript_id,
                        "seq": finding.seq,
                    })
    result = {
        "calls": calls,
        "findings": findings,
        "affected_calls": affected,
        "finding_codes": dict(sorted(by_code.items())),
    }
    if include_findings:
        result["findings_detail"] = details
        result["findings_detail_count"] = len(details)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent privacy rescan of calls.jsonl")
    parser.add_argument("--calls", required=True, help="path to calls.jsonl")
    parser.add_argument("--findings-json", default=None,
                        help="optional path for code/transcript_id/seq findings only")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = scan_jsonl(Path(args.calls), include_findings=bool(args.findings_json))
    if args.findings_json:
        with open(args.findings_json, "w", encoding="utf-8") as handle:
            json.dump({
                "summary": {key: value for key, value in result.items()
                            if key != "findings_detail"},
                "findings": result.get("findings_detail", []),
            }, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        result.pop("findings_detail", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["findings"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
