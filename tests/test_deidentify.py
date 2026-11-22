# -*- coding: utf-8 -*-
"""Unit tests for the de-identification pipeline (v2).

All fixtures are hand-written, entirely fictional Chinese utterances. No
sample is copied from the real transcript corpus, and no real personal data
is used anywhere in this file.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "data_pipeline"))

import deidentify  # noqa: E402
import privacy_scan  # noqa: E402


def transcript(*records) -> bytes:
    """Build CRLF transcript bytes from (start, end, channel, profile, payload)."""
    lines = []
    for start, end, channel, profile, payload in records:
        lines.append(f"[{start},{end}]\t{channel}\t{profile}\t{payload}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


_DIGIT_SPELL = {
    "0": "零", "1": "幺", "2": "两", "3": "三", "4": "四",
    "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
}


def spell(digits: str) -> str:
    """Spell a digit string with Chinese numerals (synthetic test data only)."""
    return "".join(_DIGIT_SPELL[d] for d in digits)


def run_unit(raw: bytes, tid: str = "CALL_" + "0" * 24):
    return deidentify.process_transcript(raw, tid)


def speech_texts(call) -> list[str]:
    return [r["text_masked"] for r in call["records"] if r["payload_type"] == "SPEECH"]


def has_ascii_digit(text: str) -> bool:
    return bool(re.search(r"[0-9]", text))


class ParsingTests(unittest.TestCase):
    def test_01_strict_utf8_decode(self):
        raw = b"[\xd0\x00,1.0]\t1\t\xc4\xd0,\xc6\xd5\xcd\xa8\xbb\xb0\tx\r\n"
        with self.assertRaises(deidentify.ParseError) as ctx:
            deidentify.parse_transcript(raw)
        self.assertEqual(ctx.exception.code, "PARSE_DECODE")

    def test_02_tab_four_fields(self):
        raw = transcript((0, 1.2, 1, "女,普通话", "你好"))
        records = deidentify.parse_transcript(raw)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].payload, "你好")
        self.assertEqual(records[0].channel, 1)
        self.assertEqual(records[0].seq, 0)

    def test_03_timestamp_to_ms(self):
        self.assertEqual(deidentify.to_ms("0.0969797"), 97)
        self.assertEqual(deidentify.to_ms("191.752"), 191752)
        self.assertEqual(deidentify.to_ms("1.2345"), 1235)
        self.assertEqual(deidentify.to_ms("0"), 0)

    def test_04_timestamp_anomaly(self):
        raw = transcript((2.0, 1.0, 1, "女,普通话", "你好"))
        with self.assertRaises(deidentify.ParseError) as ctx:
            deidentify.parse_transcript(raw)
        self.assertEqual(ctx.exception.code, "TIMESTAMP_INVALID")

    def test_05_channel_order_preserved(self):
        raw = transcript(
            (0, 1, 2, "女,普通话", "喂"),
            (1, 2, 1, "男,普通话", "您好"),
            (2, 3, 2, "女,普通话", "你好"),
        )
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertEqual([r["channel_id"] for r in call["records"]], [2, 1, 2])
        self.assertEqual([r["seq"] for r in call["records"]], [0, 1, 2])
        self.assertEqual({r["role"] for r in call["records"]}, {"UNKNOWN"})

    def test_field_count_error(self):
        with self.assertRaises(deidentify.ParseError) as ctx:
            deidentify.parse_transcript("broken line\r\n".encode("utf-8"))
        self.assertEqual(ctx.exception.code, "PARSE_FIELD_COUNT")


class DigitAndPhoneTests(unittest.TestCase):
    def test_06_chinese_digit_phone(self):
        raw = transcript((0, 1, 1, "女,普通话", "我的号码是" + spell("13800000000")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        texts = speech_texts(call)
        self.assertIn("<PHONE>", texts[0])
        self.assertNotIn("幺三八", texts[0])

    def test_07_digit_variants(self):
        self.assertEqual(deidentify.cn_digit_value("幺"), "1")
        self.assertEqual(deidentify.cn_digit_value("一"), "1")
        self.assertEqual(deidentify.cn_digit_value("两"), "2")
        self.assertEqual(deidentify.cn_digit_value("〇"), "0")
        self.assertEqual(deidentify.cn_digit_value("洞"), "0")
        # "两" as second digit is not a valid mobile prefix -> fail closed.
        raw = transcript((0, 1, 1, "女,普通话", "号码" + spell("12345678901")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(call)
        self.assertIn("AMBIGUOUS_PHONE", quarantine["reason_codes"])

    def test_08_phone_split_by_punctuation(self):
        raw = transcript((0, 1, 1, "女,普通话",
                          spell("138") + "，" + spell("000") + "，" + spell("00000")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<PHONE>", speech_texts(call)[0])

    def test_09_phone_split_across_records(self):
        raw = transcript(
            (0, 1, 1, "女,普通话", "我的号码是" + spell("138000")),
            (1, 2, 1, "女,普通话", spell("00000") + "，对吗"),
        )
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        texts = speech_texts(call)
        self.assertTrue(all("<PHONE>" in t for t in texts))
        flags = [r["asr_flags"] for r in call["records"]]
        self.assertTrue(all("phone_split_merged" in f for f in flags))

    def test_business_amount_not_over_masked(self):
        raw = transcript((0, 1, 1, "女,普通话", "套餐一百二十八元，月租五十九元"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("一百二十八元", speech_texts(call)[0])
        self.assertEqual(call["records"][0]["redaction_flags"], [])

    def test_ascii_business_numbers_preserved(self):
        raw = transcript((0, 1, 1, "女,普通话", "套餐59元，月租128元，合约期12个月"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("59元", text)
        self.assertIn("128元", text)
        self.assertIn("12个月", text)
        self.assertEqual(call["records"][0]["redaction_flags"], [])

    # -- F1: phones embedded in longer digit entities -------------------
    def test_phone_embedded_in_13_digit_run(self):
        raw = transcript((0, 1, 1, "女,普通话", "号码" + spell("1380000000012")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertEqual(text, "号码<PHONE>")
        self.assertFalse(has_ascii_digit(text))
        self.assertNotIn(spell("138"), text)

    def test_phone_embedded_in_14_digit_run(self):
        raw = transcript((0, 1, 1, "女,普通话", "号码" + spell("13800000000012")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertEqual(text, "号码<PHONE>")
        self.assertFalse(has_ascii_digit(text))

    def test_phone_at_offset_in_12_digit_run(self):
        raw = transcript((0, 1, 1, "女,普通话", "号码" + spell("013800000000")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertEqual(speech_texts(call)[0], "号码<PHONE>")

    def test_phone_connected_by_comma_to_extra_digits(self):
        raw = transcript((0, 1, 1, "女,普通话",
                          "号码" + spell("13800000000") + "，12"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertEqual(text, "号码<PHONE>")
        self.assertNotIn("12", text)

    def test_phone_in_parentheses(self):
        raw = transcript((0, 1, 1, "女,普通话", "（" + spell("13800000000") + "）"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("<PHONE>", text)
        self.assertFalse(has_ascii_digit(text))

    def test_phone_split_by_spaces(self):
        raw = transcript((0, 1, 1, "女,普通话",
                          spell("138") + " " + spell("0000") + " " + spell("0000")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertEqual(speech_texts(call)[0], "<PHONE>")

    def test_mixed_chinese_and_ascii_digits(self):
        raw = transcript((0, 1, 1, "女,普通话", "号码1三八" + "00000000"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertEqual(speech_texts(call)[0], "号码<PHONE>")

    def test_two_numbers_in_one_sentence(self):
        raw = transcript((0, 1, 1, "女,普通话",
                          "号码" + spell("13800000000") + "和"
                          + spell("1390000000012")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertEqual(text.count("<PHONE>"), 2)
        self.assertFalse(has_ascii_digit(text))

    def test_13_digit_account_without_phone(self):
        raw = transcript((0, 1, 1, "女,普通话", "编号" + spell("1234567890123")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("<ACCOUNT>", text)
        self.assertFalse(has_ascii_digit(text))

    def test_10_digit_zero_prefix_is_quarantined(self):
        raw = transcript((0, 1, 1, "女,普通话", "号码" + spell("0123456789")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(call)
        self.assertIn("AMBIGUOUS_PHONE", quarantine["reason_codes"])


class SplitPhoneTests(unittest.TestCase):
    def test_different_channels_not_merged_and_quarantined(self):
        raw = transcript(
            (0, 1, 1, "女,普通话", "我的号码是" + spell("138000")),
            (1, 2, 2, "男,普通话", spell("00000")),
        )
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(call)
        self.assertIn("SPLIT_PHONE_AMBIGUOUS", quarantine["reason_codes"])

    def test_event_between_records_not_merged(self):
        raw = transcript(
            (0, 1, 1, "女,普通话", "我的号码是" + spell("138000")),
            (1, 2, 1, "女,普通话", "[SYSTEM]"),
            (2, 3, 1, "女,普通话", spell("00000")),
        )
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        texts = speech_texts(call)
        # The 6-digit fragment after "号码" is context-masked on its own; the
        # point is that no cross-record phone merge happened across the EVENT.
        self.assertNotIn("<PHONE>", "".join(texts))
        self.assertEqual(texts[0], "我的号码是<ACCOUNT>")
        self.assertEqual(texts[1], spell("00000"))

    def test_gap_beyond_ambiguous_window_not_merged(self):
        raw = transcript(
            (0, 1, 1, "女,普通话", "我的号码是" + spell("138000")),
            (6, 7, 1, "女,普通话", spell("00000")),
        )
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertNotIn("<PHONE>", "".join(speech_texts(call)))

    def test_gap_in_ambiguous_window_quarantined(self):
        raw = transcript(
            (0, 1, 1, "女,普通话", "我的号码是" + spell("138000")),
            (3, 4, 1, "女,普通话", spell("00000")),
        )
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(call)
        self.assertIn("SPLIT_PHONE_AMBIGUOUS", quarantine["reason_codes"])

    def test_three_record_split_merged(self):
        raw = transcript(
            (0, 1, 1, "女,普通话", "号码是" + spell("138")),
            (1, 2, 1, "女,普通话", spell("0000")),
            (2, 3, 1, "女,普通话", spell("0000") + "，对吗"),
        )
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        texts = speech_texts(call)
        self.assertEqual("".join(texts).count("<PHONE>"), 3)
        self.assertFalse(any(has_ascii_digit(text) for text in texts))


class AccountAndIdentityTests(unittest.TestCase):
    def test_9_digit_account_default(self):
        raw = transcript((0, 1, 1, "女,普通话", "编号" + spell("123456789")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<ACCOUNT>", speech_texts(call)[0])

    def test_8_digit_without_context_masked(self):
        raw = transcript((0, 1, 1, "女,普通话", "编号12345678"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("<ACCOUNT>", text)
        self.assertIn("short_unclassified_digit_masked",
                      call["records"][0]["asr_flags"])

    def test_6_digit_without_context_masked(self):
        raw = transcript((0, 1, 1, "女,普通话", "编号123456"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<ACCOUNT>", speech_texts(call)[0])

    def test_6_digit_business_context_preserved(self):
        raw = transcript((0, 1, 1, "女,普通话", "流量123456兆"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("123456兆", speech_texts(call)[0])
        self.assertEqual(call["records"][0]["redaction_flags"], [])

    def test_7_digit_business_prefix_preserved(self):
        raw = transcript((0, 1, 1, "女,普通话", "套餐1234567"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("1234567", speech_texts(call)[0])

    def test_8_digit_with_account_context(self):
        raw = transcript((0, 1, 1, "女,普通话", "账号是" + spell("12345678")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<ACCOUNT>", speech_texts(call)[0])

    def test_15_digit_id(self):
        raw = transcript((0, 1, 1, "女,普通话", "身份证" + spell("360102199001011")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<ID_NO>", speech_texts(call)[0])

    def test_18_digit_id(self):
        raw = transcript((0, 1, 1, "女,普通话", "身份证" + spell("360102199001011234")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<ID_NO>", speech_texts(call)[0])

    def test_16_digit_card(self):
        raw = transcript((0, 1, 1, "女,普通话", "卡号" + spell("6222000000000000")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<CARD_NO>", speech_texts(call)[0])

    def test_20_digit_card(self):
        raw = transcript((0, 1, 1, "女,普通话", "账号" + "1" * 20))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        record = call["records"][0]
        self.assertIn("<CARD_NO>", record["text_masked"])
        self.assertIn("long_digit_run_masked", record["asr_flags"])


class NameTests(unittest.TestCase):
    def test_10_name_replacement(self):
        raw = transcript((0, 1, 1, "女,普通话", "请问张先生在家吗"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("<NAME>先生", text)
        self.assertNotIn("张", text)

    def test_xing_pattern(self):
        raw = transcript((0, 1, 1, "女,普通话", "他姓李"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("姓<NAME>", speech_texts(call)[0])

    def test_wojiao_pattern(self):
        raw = transcript((0, 1, 1, "女,普通话", "我叫王小明"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertIn("<NAME>", speech_texts(call)[0])
        self.assertNotIn("王小明", speech_texts(call)[0])

    def test_teacher_manager_director(self):
        for payload in ("李老师您好", "王经理在吗", "赵主任说过"):
            with self.subTest(payload=payload):
                call, quarantine, _ = run_unit(
                    transcript((0, 1, 1, "女,普通话", payload)))
                self.assertIsNone(quarantine)
                self.assertIn("<NAME>", speech_texts(call)[0])

    def test_boss_honorific(self):
        call, quarantine, _ = run_unit(
            transcript((0, 1, 1, "女,普通话", "王总您好")))
        self.assertIsNone(quarantine)
        self.assertIn("<NAME>", speech_texts(call)[0])

    def test_non_name_honorific_words_not_masked(self):
        for payload in ("这里是小区", "总结一下", "位先生请稍等", "是先生吗"):
            with self.subTest(payload=payload):
                call, quarantine, _ = run_unit(
                    transcript((0, 1, 1, "女,普通话", payload)))
                self.assertIsNone(quarantine)
                self.assertNotIn("<NAME>", speech_texts(call)[0])


class AddressTests(unittest.TestCase):
    def test_11_address_replacement(self):
        raw = transcript((0, 1, 1, "女,普通话", "地址是幸福省安康市朝阳区幸福路八十八号"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("<ADDRESS>", text)
        self.assertNotIn("幸福", text)

    def test_address_with_trigger(self):
        raw = transcript((0, 1, 1, "女,普通话", "我家住在幸福小区三号楼二单元"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("<ADDRESS>", text)
        self.assertNotIn("幸福", text)

    def test_long_address_reaches_room_number(self):
        payload = "地址是幸福省安康市朝阳区幸福路88号3号楼2单元301室"
        call, quarantine, _ = run_unit(
            transcript((0, 1, 1, "女,普通话", payload)))
        self.assertIsNone(quarantine)
        text = speech_texts(call)[0]
        self.assertIn("<ADDRESS>", text)
        self.assertNotIn("301", text)
        self.assertNotIn("88", text)

    def test_address_boundary_beyond_80_quarantined(self):
        payload = "我家住在幸福小区" + "A" * 70 + "3号楼" + "B" * 20 + "。"
        call, quarantine, _ = run_unit(
            transcript((0, 1, 1, "女,普通话", payload)))
        self.assertIsNone(call)
        self.assertIn("ADDRESS_BOUNDARY", quarantine["reason_codes"])

    def test_structured_address_without_trigger(self):
        payload = "幸福省安康市朝阳区幸福路88号"
        call, quarantine, _ = run_unit(
            transcript((0, 1, 1, "女,普通话", payload)))
        self.assertIsNone(quarantine)
        self.assertIn("<ADDRESS>", speech_texts(call)[0])

    def test_personal_trigger_without_address_quarantined(self):
        call, quarantine, _ = run_unit(
            transcript((0, 1, 1, "女,普通话", "我家住在很远的地方")))
        self.assertIsNone(call)
        self.assertIn("ADDRESS_AMBIGUOUS", quarantine["reason_codes"])


class EventTests(unittest.TestCase):
    def test_15_sstem_normalized(self):
        raw = transcript((0, 1, 1, "女,普通话", "[SSTEM]"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        record = call["records"][0]
        self.assertEqual(record["payload_type"], "EVENT")
        self.assertEqual(record["event"], "SYSTEM")
        self.assertIn("event_spelling_variant", record["asr_flags"])
        self.assertIsNone(record["text_masked"])

    def test_16_star_event(self):
        raw = transcript((0, 1, 1, "女,普通话", "[*]"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        self.assertEqual(call["records"][0]["event"], "UNCLASSIFIED")

    def test_known_event_vocabulary(self):
        for label, expected in (("SYSTEM", "SYSTEM"), ("ENS", "ENS"), ("DTMF", "DTMF"),
                                ("MUSIC", "MUSIC"), ("SONANT", "SONANT"),
                                ("LAUGH", "LAUGH"), ("UNKNOWN", "UNKNOWN")):
            with self.subTest(label=label):
                call, quarantine, _ = run_unit(
                    transcript((0, 1, 1, "女,普通话", f"[{label}]")))
                self.assertIsNone(quarantine)
                self.assertEqual(call["records"][0]["event"], expected)
                self.assertIsNone(call["records"][0]["text_masked"])

    def test_strict_unknown_tag(self):
        call, quarantine, _ = run_unit(
            transcript((0, 1, 1, "女,普通话", "[FOO_BAR]")))
        self.assertIsNone(quarantine)
        record = call["records"][0]
        self.assertEqual(record["payload_type"], "EVENT")
        self.assertEqual(record["event"], "UNKNOWN")
        self.assertIn("malformed_event_tag", record["asr_flags"])

    def test_quoted_speech_is_preserved(self):
        for payload in ("[MU SIC]", "[你好]", "[system]"):
            with self.subTest(payload=payload):
                call, quarantine, _ = run_unit(
                    transcript((0, 1, 1, "女,普通话", payload)))
                self.assertIsNone(quarantine)
                record = call["records"][0]
                self.assertEqual(record["payload_type"], "SPEECH")
                self.assertEqual(record["text_masked"], payload)


class MarkerTests(unittest.TestCase):
    def test_14_plus_to_truncation_flag(self):
        raw = transcript((0, 1, 1, "女,普通话", "好的我想想+"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        record = call["records"][0]
        self.assertTrue(record["is_truncated"])
        self.assertEqual(record["text_masked"], "好的我想想")
        self.assertNotIn("+", record["text_masked"])

    def test_plus_in_middle_fail_closed(self):
        raw = transcript((0, 1, 1, "女,普通话", "好的+我想想"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(call)
        self.assertIn("AMBIGUOUS_PLUS", quarantine["reason_codes"])

    def test_yen_token_flagged(self):
        raw = transcript((0, 1, 1, "女,普通话", "￥好的"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        record = call["records"][0]
        self.assertIn("<ASR_TOKEN>", record["text_masked"])
        self.assertIn("yen_token", record["asr_flags"])

    def test_empty_after_mask_flagged(self):
        raw = transcript((0, 1, 1, "女,普通话", spell("13800000000")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        record = call["records"][0]
        self.assertEqual(record["text_masked"], "<PHONE>")
        self.assertNotIn("empty_after_mask", record["asr_flags"])

    def test_plus_only_record(self):
        raw = transcript((0, 1, 1, "女,普通话", "+"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(quarantine)
        record = call["records"][0]
        self.assertTrue(record["is_truncated"])
        self.assertEqual(record["text_masked"], "")
        self.assertIn("empty_after_mask", record["asr_flags"])


class QuarantineTests(unittest.TestCase):
    def test_18_child_channel_quarantined(self):
        raw = transcript((0, 1, 2, "儿童,普通话", "你好"))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(call)
        self.assertIn("CHILD_CHANNEL", quarantine["reason_codes"])
        self.assertNotIn("儿童", json.dumps(quarantine, ensure_ascii=False))

    def test_19_ambiguous_phone_fail_closed(self):
        raw = transcript((0, 1, 1, "女,普通话", "号码是" + spell("12345678901")))
        call, quarantine, _ = run_unit(raw)
        self.assertIsNone(call)
        self.assertIn("AMBIGUOUS_PHONE", quarantine["reason_codes"])

    def test_parse_error_quarantined_without_text(self):
        call, quarantine, stats = run_unit(b"not a transcript\r\n")
        self.assertIsNone(call)
        self.assertEqual(quarantine["reason_codes"], ["PARSE_FIELD_COUNT"])
        self.assertEqual(quarantine["affected_seq"], [])
        self.assertEqual(stats["records"], 0)


class OutputValidationTests(unittest.TestCase):
    def _valid_call(self):
        call, quarantine, _ = run_unit(
            transcript((0, 1, 1, "女,普通话", "张先生，号码" + spell("13800000000"))))
        self.assertIsNone(quarantine)
        self.assertEqual(deidentify.validate_call_schema(call), [])
        return call

    def test_valid_call_passes(self):
        self._valid_call()

    def test_bad_seq(self):
        call = copy.deepcopy(self._valid_call())
        call["records"][0]["seq"] = 3
        self.assertIn("bad_seq", deidentify.validate_call_schema(call))

    def test_bad_duration(self):
        call = copy.deepcopy(self._valid_call())
        call["duration_ms"] += 1
        self.assertIn("duration_mismatch", deidentify.validate_call_schema(call))

    def test_bad_event_and_channel(self):
        call, _, _ = run_unit(transcript((0, 1, 1, "女,普通话", "[SYSTEM]")))
        call = copy.deepcopy(call)
        call["records"][0]["event"] = "BOGUS"
        call["records"][0]["channel_id"] = 9
        problems = deidentify.validate_call_schema(call)
        self.assertIn("bad_event", problems)
        self.assertIn("bad_channel", problems)

    def test_bad_placeholder(self):
        call = copy.deepcopy(self._valid_call())
        call["records"][0]["text_masked"] = "<FOO>"
        self.assertIn("bad_placeholder", deidentify.validate_call_schema(call))

    def test_forbidden_key(self):
        call = copy.deepcopy(self._valid_call())
        call["records"][0]["raw_text"] = "x"
        problems = deidentify.validate_call_schema(call)
        self.assertIn("record_key_set", problems)
        self.assertTrue(any(problem.startswith("forbidden_key") for problem in problems))

    def test_empty_text_invariant(self):
        call = copy.deepcopy(self._valid_call())
        call["records"][0]["text_masked"] = ""
        call["records"][0]["is_truncated"] = False
        problems = deidentify.validate_call_schema(call)
        self.assertIn("empty_text_not_truncated", problems)
        self.assertIn("empty_text_without_flag", problems)

    def test_unknown_flag(self):
        call = copy.deepcopy(self._valid_call())
        call["records"][0]["asr_flags"] = ["bogus_flag"]
        self.assertIn("unknown_asr_flag", deidentify.validate_call_schema(call))

    def test_duplicate_transcript_ids(self):
        call = self._valid_call()
        self.assertIn("duplicate_transcript_id",
                      deidentify.validate_dataset([call, copy.deepcopy(call)]))

    def test_quarantine_schema(self):
        obj = deidentify.make_quarantine("CALL_" + "a" * 24, ["CHILD_CHANNEL"], [0])
        self.assertEqual(deidentify.validate_quarantine(obj), [])


class PathSafetyTests(unittest.TestCase):
    def test_output_equals_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            source.mkdir()
            root = base / "deidentified"
            root.mkdir()
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.validate_output_path(source, source, root)
            self.assertEqual(ctx.exception.code, "OUTPUT_IS_INPUT")

    def test_output_inside_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            source.mkdir()
            root = base / "deidentified"
            root.mkdir()
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.validate_output_path(source, source / "sub", root)
            self.assertEqual(ctx.exception.code, "OUTPUT_INSIDE_INPUT")

    def test_symlink_into_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            (source / "sub").mkdir(parents=True)
            root = base / "deidentified"
            root.mkdir()
            (root / "link").symlink_to(source / "sub", target_is_directory=True)
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.validate_output_path(source, root / "link" / "v2", root)
            self.assertEqual(ctx.exception.code, "OUTPUT_INSIDE_INPUT")

    def test_output_outside_approved_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            source.mkdir()
            root = base / "deidentified"
            root.mkdir()
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.validate_output_path(source, base / "elsewhere" / "v2", root)
            self.assertEqual(ctx.exception.code, "OUTPUT_OUTSIDE_APPROVED_ROOT")

    def test_existing_output_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            source.mkdir()
            root = base / "deidentified"
            root.mkdir()
            (root / "v2").mkdir()
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.validate_output_path(source, root / "v2", root)
            self.assertEqual(ctx.exception.code, "OUTPUT_EXISTS")

    def test_valid_output_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            source.mkdir()
            root = base / "deidentified"
            root.mkdir()
            resolved = deidentify.validate_output_path(source, root / "v2", root)
            self.assertEqual(resolved, (root / "v2").resolve())


class KeyTests(unittest.TestCase):
    def test_new_key_permissions_and_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "keys"
            path = root / "nested" / "transcript.key"
            key = deidentify.ensure_key(path, [], root)
            self.assertEqual(len(key), 32)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(root).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(root / "nested").st_mode), 0o700)

    def test_existing_key_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "keys"
            root.mkdir(mode=0o700)
            path = root / "transcript.key"
            path.write_bytes(b"K" * 32)
            os.chmod(path, 0o600)
            before = os.stat(path)
            first = deidentify.ensure_key(path, [], root)
            second = deidentify.ensure_key(path, [], root)
            after = os.stat(path)
            self.assertEqual(first, second)
            self.assertEqual(first, b"K" * 32)
            self.assertEqual((before.st_mtime_ns, before.st_size),
                             (after.st_mtime_ns, after.st_size))

    def test_existing_parent_mode_not_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            parent = base / "home"
            parent.mkdir()
            os.chmod(parent, 0o755)
            root = parent / "keys"
            root.mkdir()
            os.chmod(root, 0o755)
            deidentify.ensure_key(root / "k.key", [], root)
            self.assertEqual(stat.S_IMODE(os.stat(parent).st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(os.stat(root).st_mode), 0o755)

    def test_symlink_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "keys"
            root.mkdir()
            real = root / "real.key"
            real.write_bytes(b"K" * 32)
            os.chmod(real, 0o600)
            link = root / "link.key"
            link.symlink_to(real)
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.ensure_key(link, [], root)
            self.assertEqual(ctx.exception.code, "KEY_SYMLINK")

    def test_key_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "keys"
            root.mkdir()
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.ensure_key(Path(tmp) / "other.key", [], root)
            self.assertEqual(ctx.exception.code, "KEY_OUTSIDE_DEDICATED_ROOT")

    def test_too_open_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "keys"
            root.mkdir(mode=0o700)
            path = root / "k.key"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
            os.write(fd, b"K" * 32)
            os.close(fd)
            os.chmod(path, 0o644)
            with self.assertRaises(deidentify.PathSafetyError) as ctx:
                deidentify.ensure_key(path, [], root)
            self.assertEqual(ctx.exception.code, "KEY_PERMISSIONS_TOO_OPEN")

    def test_transcript_id_stable(self):
        key = b"K" * 32
        self.assertEqual(deidentify.make_transcript_id(key, "a.txt"),
                         deidentify.make_transcript_id(key, "a.txt"))
        self.assertNotEqual(deidentify.make_transcript_id(key, "a.txt"),
                            deidentify.make_transcript_id(key, "b.txt"))


class ScannerTests(unittest.TestCase):
    def test_placeholders_are_clean(self):
        self.assertEqual(privacy_scan.scan_text("<PHONE>先生，您好"), [])
        self.assertEqual(privacy_scan.scan_text("<ADDRESS>，59元"), [])

    def test_phone_residual(self):
        self.assertIn(privacy_scan.PHONE_CODE,
                      privacy_scan.scan_text(spell("13800000000")))

    def test_embedded_phone_residual(self):
        self.assertIn(privacy_scan.PHONE_EMBEDDED_CODE,
                      privacy_scan.scan_text(spell("1380000000012")))

    def test_account_residual(self):
        self.assertIn(privacy_scan.ACCOUNT_CODE,
                      privacy_scan.scan_text("123456789"))

    def test_short_digit_residual(self):
        self.assertIn(privacy_scan.SHORT_DIGIT_CODE,
                      privacy_scan.scan_text("12345678"))

    def test_business_short_digits_not_flagged(self):
        self.assertEqual(privacy_scan.scan_text("流量123456兆"), [])
        self.assertEqual(privacy_scan.scan_text("套餐1234567"), [])

    def test_phone_context_residual(self):
        self.assertIn(privacy_scan.PHONE_CONTEXT_CODE,
                      privacy_scan.scan_text("电话是8888888"))

    def test_name_residuals(self):
        for payload in ("张先生", "姓李", "我叫王小明", "李老师", "王经理", "赵主任"):
            with self.subTest(payload=payload):
                self.assertIn(privacy_scan.NAME_CODE, privacy_scan.scan_text(payload))

    def test_address_residual(self):
        self.assertIn(privacy_scan.ADDRESS_CODE,
                      privacy_scan.scan_text("地址是幸福省安康市朝阳区幸福路88号"))

    def test_placeholder_boundary_residual(self):
        for payload in ("<PHONE>1234", "1234<PHONE>"):
            with self.subTest(payload=payload):
                self.assertIn(privacy_scan.PLACEHOLDER_BOUNDARY_CODE,
                              privacy_scan.scan_text(payload))

    def test_split_residual(self):
        call = {
            "transcript_id": "CALL_" + "a" * 24,
            "records": [
                {"seq": 0, "payload_type": "SPEECH", "text_masked": "号码138000"},
                {"seq": 1, "payload_type": "SPEECH", "text_masked": "00000，对吗"},
            ],
        }
        self.assertIn(privacy_scan.PHONE_SPLIT_CODE, privacy_scan.scan_call(call))

    def test_three_record_split_residual(self):
        call = {
            "transcript_id": "CALL_" + "a" * 24,
            "records": [
                {"seq": 0, "payload_type": "SPEECH", "text_masked": "号码138"},
                {"seq": 1, "payload_type": "SPEECH", "text_masked": "0000"},
                {"seq": 2, "payload_type": "SPEECH", "text_masked": "0000，对吗"},
            ],
        }
        self.assertIn(privacy_scan.PHONE_SPLIT_CODE, privacy_scan.scan_call(call))

    def test_findings_contain_no_text(self):
        call = {
            "transcript_id": "CALL_" + "a" * 24,
            "records": [
                {"seq": 0, "payload_type": "SPEECH",
                 "text_masked": spell("13800000000")},
            ],
        }
        findings = privacy_scan.scan_call_findings(call)
        self.assertTrue(findings)
        for finding in findings:
            self.assertNotIn("138", repr(finding))
            self.assertNotIn("幺", repr(finding))

    def test_business_numbers_not_flagged(self):
        self.assertEqual(privacy_scan.scan_text("套餐59元，月租128元"), [])


class EndToEndTests(unittest.TestCase):
    def _args(self, source: Path, output: Path, root: Path, keys: Path):
        return deidentify.build_parser().parse_args([
            "--input", str(source),
            "--output", str(output),
            "--output-root", str(root),
            "--key-path", str(keys / "transcript.key"),
            "--key-root", str(keys),
        ])

    def test_run_publishes_valid_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            source.mkdir()
            (source / "a.txt").write_bytes(transcript(
                (0, 1, 1, "女,普通话", "张先生，电话13800138000")))
            (source / "b.txt").write_bytes(transcript(
                (0, 1, 1, "女,普通话", "套餐59元，月租128元")))
            root = base / "deidentified"
            root.mkdir()
            output = root / "cm_outbound_v2"
            keys = base / "keys"
            args = self._args(source, output, root, keys)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = deidentify.run(args)
            self.assertEqual(rc, 0)
            for name in ("calls.jsonl", "quarantine.jsonl", "manifest.json", "README.md"):
                self.assertTrue((output / name).is_file(), name)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["runtime"]["python_version"], "3.12.16")
            self.assertEqual(manifest["input"]["files"], 2)
            self.assertEqual(manifest["output"]["calls"], 2)
            self.assertEqual(manifest["privacy_scan"]["second_pass_findings"], 0)
            self.assertTrue(manifest["integrity"]["unchanged"])
            self.assertGreaterEqual(
                manifest["processing"]["entities"]["PHONE"]["occurrences"], 1)
            result = privacy_scan.scan_jsonl(output / "calls.jsonl")
            self.assertEqual(result["findings"], 0)

    def test_path_rejection_happens_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "TXT"
            source.mkdir()
            (source / "a.txt").write_bytes(transcript(
                (0, 1, 1, "女,普通话", "你好")))
            root = base / "deidentified"
            root.mkdir()
            output = source / "sub"
            keys = base / "keys"
            args = self._args(source, output, root, keys)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = deidentify.run(args)
            self.assertEqual(rc, 7)
            self.assertFalse(output.exists())
            self.assertFalse(keys.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
