"""Ingestion tests: the real de-identified JSONL layout, read strictly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.transcripts.ingestion import (
    TranscriptCorpusError,
    dataset_version,
    iter_units,
    read_dataset_manifest,
)
from decision_support import dialogue, unit, write_corpus


def test_reads_units_in_order_with_original_fields(tmp_path: Path) -> None:
    corpus = write_corpus(
        tmp_path / "corpus",
        [
            unit("CALL_FIXTURE_A", dialogue([(1, "占位甲"), (2, "占位乙")])),
            unit("CALL_FIXTURE_B", dialogue([(2, "占位丙")])),
        ],
    )
    units = list(iter_units(corpus))
    assert [item.transcript_id for item in units] == ["CALL_FIXTURE_A", "CALL_FIXTURE_B"]
    first = units[0]
    assert first.schema_version == "1.0"
    assert [record.seq for record in first.records] == [0, 1]
    assert [record.channel_id for record in first.records] == [1, 2]
    assert [record.text for record in first.records] == ["占位甲", "占位乙"]
    assert first.records[0].payload_type == "SPEECH"
    assert first.records[0].is_truncated is False


def test_event_records_keep_null_text_as_empty(tmp_path: Path) -> None:
    records = [
        {
            "seq": 0,
            "start_ms": 0,
            "end_ms": 100,
            "channel_id": 1,
            "role": "UNKNOWN",
            "payload_type": "EVENT",
            "event": "SYSTEM",
            "text_masked": None,
            "is_truncated": False,
            "asr_flags": ["empty_after_mask"],
            "redaction_flags": [],
        }
    ]
    corpus = write_corpus(tmp_path / "corpus", [unit("CALL_FIXTURE_EVENT", records)])
    (parsed,) = list(iter_units(corpus))
    assert parsed.records[0].text == ""
    assert parsed.records[0].event == "SYSTEM"
    assert parsed.records[0].asr_flags == ("empty_after_mask",)


def test_manifest_metadata_is_read(tmp_path: Path) -> None:
    corpus = write_corpus(tmp_path / "corpus", [unit("CALL_FIXTURE_A", dialogue([(1, "占位")]))])
    assert dataset_version(corpus) == "neutral_test_fixture"
    assert read_dataset_manifest(corpus)["pipeline_version"] == "test"


def test_missing_corpus_is_a_data_error(tmp_path: Path) -> None:
    with pytest.raises(TranscriptCorpusError) as caught:
        list(iter_units(tmp_path / "absent"))
    assert caught.value.code == "corpus_not_found"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ("{not json}", "invalid_json"),
        (json.dumps({"duration_ms": 1, "records": []}), "missing_transcript_id"),
        (
            json.dumps(
                {"transcript_id": "C1", "duration_ms": 1, "records": [], "schema_version": ""}
            ),
            "missing_schema_version",
        ),
        (
            json.dumps(
                {"transcript_id": "C1", "duration_ms": 1, "schema_version": "1.0"}
            ),
            "missing_records",
        ),
    ],
)
def test_malformed_unit_is_rejected(
    tmp_path: Path, payload: str, code: str
) -> None:
    corpus = write_corpus(tmp_path / "corpus", [])
    (corpus / "calls.jsonl").write_text(payload + "\n", encoding="utf-8")
    with pytest.raises(TranscriptCorpusError) as caught:
        list(iter_units(corpus))
    assert caught.value.code == code


def test_malformed_record_is_rejected(tmp_path: Path) -> None:
    records = [
        {
            "seq": 0,
            "start_ms": 0,
            "end_ms": 100,
            "channel_id": 1,
            "role": "UNKNOWN",
            "payload_type": "NOISE",
            "event": None,
            "text_masked": "占位",
            "is_truncated": False,
            "asr_flags": [],
            "redaction_flags": [],
        }
    ]
    corpus = write_corpus(tmp_path / "corpus", [unit("CALL_FIXTURE_BAD", records)])
    with pytest.raises(TranscriptCorpusError) as caught:
        list(iter_units(corpus))
    assert caught.value.code == "unknown_payload_type"


def test_missing_record_field_is_rejected(tmp_path: Path) -> None:
    record = {
        "seq": 0,
        "start_ms": 0,
        "end_ms": 100,
        "channel_id": 1,
        "role": "UNKNOWN",
        "payload_type": "SPEECH",
        "event": None,
        "text_masked": "占位",
        "is_truncated": False,
        "asr_flags": [],
    }
    corpus = write_corpus(tmp_path / "corpus", [unit("CALL_FIXTURE_MISSING", [record])])
    with pytest.raises(TranscriptCorpusError) as caught:
        list(iter_units(corpus))
    assert caught.value.code == "missing_record_field"
