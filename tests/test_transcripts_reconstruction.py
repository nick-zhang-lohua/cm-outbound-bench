"""Reconstruction tests: ordering, channel mapping, overlaps and quality flags."""

from __future__ import annotations

from pathlib import Path

from cm_outbound_bench.transcripts.ingestion import SourceRecord, TranscriptUnit
from cm_outbound_bench.transcripts.models import (
    CHANNEL_CUSTOMER,
    CHANNEL_HISTORICAL_AGENT,
    format_offset_timestamp,
    role_for_channel,
)
from cm_outbound_bench.transcripts.reconstruction import reconstruct_call
from decision_support import dialogue, load_calls, record, unit, write_corpus


def _unit(transcript_id: str, records: list[dict], duration_ms: int = 10_000) -> TranscriptUnit:
    source = TranscriptUnit(
        transcript_id=transcript_id,
        schema_version="1.0",
        duration_ms=duration_ms,
        records=tuple(
            SourceRecord(
                seq=item["seq"],
                start_ms=item["start_ms"],
                end_ms=item["end_ms"],
                channel_id=item["channel_id"],
                payload_type=item["payload_type"],
                event=item["event"],
                text=item["text_masked"] or "",
                is_truncated=item["is_truncated"],
                asr_flags=tuple(item["asr_flags"]),
                redaction_flags=tuple(item["redaction_flags"]),
            )
            for item in records
        ),
    )
    return source


def _call(records: list[dict], duration_ms: int = 10_000):
    return reconstruct_call(
        _unit("CALL_FIXTURE_X", records, duration_ms),
        corpus="neutral_test_fixture",
        corpus_schema_version="1.0",
    )


def test_channel_mapping_is_the_research_protocol() -> None:
    assert role_for_channel(CHANNEL_HISTORICAL_AGENT) == "historical_agent"
    assert role_for_channel(CHANNEL_CUSTOMER) == "customer"
    assert role_for_channel(3) == "other"
    call = _call(dialogue([(1, "占位甲"), (2, "占位乙"), (3, "占位丙")]))
    assert [turn.speaker_role for turn in call.turns] == [
        "historical_agent",
        "customer",
        "other",
    ]
    assert [turn.channel for turn in call.turns] == [1, 2, 3]


def test_records_are_ordered_by_timestamp_and_ties_keep_source_order() -> None:
    records = [
        record(0, 3000, 4000, 2, "占位后"),
        record(1, 1000, 2000, 1, "占位前"),
        record(2, 1000, 2000, 2, "占位同秒甲"),
        record(3, 1000, 2000, 1, "占位同秒乙"),
    ]
    call = _call(records)
    assert [turn.seq for turn in call.turns] == [1, 2, 3, 0]
    assert [turn.text for turn in call.turns] == ["占位前", "占位同秒甲", "占位同秒乙", "占位后"]
    assert [turn.turn_id for turn in call.turns] == [1, 2, 3, 4]


def test_original_timing_and_segment_reference_are_preserved() -> None:
    call = _call([record(0, 17_359, 18_000, 1, "占位")])
    turn = call.turns[0]
    assert (turn.start_ms, turn.end_ms) == (17_359, 18_000)
    assert turn.timestamp == "00:00:17.359"
    assert turn.source_segment_id == "CALL_FIXTURE_X:0"
    assert format_offset_timestamp(3_661_005) == "01:01:01.005"


def test_event_records_stay_on_the_timeline() -> None:
    records = [
        record(0, 0, 100, 1, None, payload_type="EVENT", event="SYSTEM"),
        record(1, 500, 900, 1, "占位"),
    ]
    call = _call(records)
    assert [turn.record_type for turn in call.turns] == ["event", "speech"]
    assert call.turns[0].event == "SYSTEM"
    assert call.event_record_count == 1
    assert call.speech_record_count == 1
    assert [turn.text for turn in call.speech_turns] == ["占位"]


def test_simultaneous_speech_is_flagged_not_flattened() -> None:
    records = [
        record(0, 1000, 3000, 2, "占位客户"),
        record(1, 2000, 4000, 1, "占位客服"),
    ]
    call = _call(records)
    assert call.turns[1].overlaps_previous is True
    assert "overlapping_records" in call.quality_flags
    assert call.data_status == "valid"
    # Both turns are kept, in timestamp order, with their own timing.
    assert [turn.text for turn in call.turns] == ["占位客户", "占位客服"]


def test_clock_reset_marks_the_call_suspect() -> None:
    records = [
        record(0, 1_000, 2_000, 1, "占位甲"),
        record(1, 90_000, 91_000, 2, "占位乙"),
        record(2, 1_000, 2_000, 1, "占位丙"),
    ]
    call = _call(records)
    assert "timing_reset" in call.quality_flags
    assert call.data_status == "suspect"


def test_no_speech_is_invalid() -> None:
    call = _call([record(0, 0, 100, 1, None, payload_type="EVENT", event="DTMF")])
    assert "no_speech" in call.quality_flags
    assert call.data_status == "invalid"


def test_duration_shorter_than_last_end_is_suspect() -> None:
    call = _call([record(0, 0, 9000, 1, "占位")], duration_ms=1000)
    assert "duration_mismatch" in call.quality_flags
    assert call.data_status == "suspect"


def test_identical_content_under_different_ids_has_one_content_hash(tmp_path: Path) -> None:
    records = dialogue([(1, "占位甲"), (2, "占位乙")])
    corpus = write_corpus(
        tmp_path / "corpus",
        [unit("CALL_FIXTURE_1", records), unit("CALL_FIXTURE_2", records)],
    )
    calls = load_calls(corpus)
    assert calls[0].call_id != calls[1].call_id
    assert calls[0].content_sha256 == calls[1].content_sha256
    assert calls[0].text_simhash == calls[1].text_simhash


def test_different_content_has_a_different_hash() -> None:
    left = _call(dialogue([(1, "占位甲"), (2, "占位乙")]))
    right = _call(dialogue([(1, "另一个占位"), (2, "另一个客户占位")]))
    assert left.content_sha256 != right.content_sha256
    assert left.text_simhash != right.text_simhash


def test_one_sided_call_is_valid_but_not_task_eligible(tmp_path: Path) -> None:
    corpus = write_corpus(
        tmp_path / "corpus", [unit("CALL_FIXTURE_ONE", dialogue([(1, "占位甲"), (1, "占位乙")]))]
    )
    (call,) = load_calls(corpus)
    assert call.data_status == "valid"
    assert "no_customer_speech" in call.quality_flags
