from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cm_outbound_bench.corpus.database import CorpusDatabase
from corpus_support import PRIVATE_MARKER


def test_statistics_match_the_imported_fixture(corpus_database: Path) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        statistics = corpus.get_statistics()

    assert statistics.schema_version == "1.0"
    assert statistics.corpus_version == "synthetic_corpus_test"
    assert statistics.transcript_units == 3
    assert statistics.records == 9
    assert statistics.speech_records == 6
    assert statistics.event_records == 3
    assert statistics.quarantine_units == 2
    assert statistics.domains == 6
    assert statistics.domain_assignments == 0
    assert statistics.scenario_patterns == 0
    assert statistics.payload_types == {"EVENT": 3, "SPEECH": 6}
    assert statistics.channels == {"1": 6, "2": 3}
    assert statistics.events == {"SYSTEM": 3}
    assert statistics.roles == {"UNKNOWN": 9}


def test_unit_and_record_lookup(corpus_database: Path) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        units = corpus.list_transcript_units(limit=10)
        unit = corpus.get_transcript_unit("SYNTH-CALL-0000")
        missing = corpus.get_transcript_unit("DOES-NOT-EXIST")
        records = corpus.list_transcript_records("SYNTH-CALL-0000")
        speech = corpus.list_transcript_records(
            "SYNTH-CALL-0000", payload_type="SPEECH"
        )
        record = corpus.get_record("SYNTH-CALL-0000", 1)

    assert len(units) == 3
    assert [item.transcript_id for item in units] == [
        "SYNTH-CALL-0000",
        "SYNTH-CALL-0001",
        "SYNTH-CALL-0002",
    ]
    assert unit is not None
    assert unit.record_count == 3
    assert unit.duration_ms == 900 * 3 + 500
    assert missing is None
    assert len(records) == 3
    assert [item.seq for item in records] == [0, 1, 2]
    assert [item.seq for item in speech] == [0, 2]
    assert record is not None
    assert record.payload_type == "EVENT"
    assert record.event == "SYSTEM"


def test_text_is_withheld_unless_explicitly_requested(
    corpus_database: Path,
) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        redacted = corpus.list_transcript_records("SYNTH-CALL-0000")
        explicit = corpus.list_transcript_records(
            "SYNTH-CALL-0000", include_text=True
        )

    assert redacted[0].text_masked is None
    assert redacted[0].has_text is True
    assert redacted[0].text_length > 0
    assert redacted[1].has_text is False
    assert redacted[1].text_length == 0

    assert explicit[0].text_masked is not None
    assert explicit[0].text_masked.startswith(PRIVATE_MARKER)


def test_record_iteration_is_streamed_and_bounded(
    corpus_database: Path,
) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        streamed = list(corpus.iter_records(batch_size=2))
        speech_only = list(corpus.iter_records(payload_type="SPEECH"))

    assert len(streamed) == 9
    assert [item.seq for item in streamed[:3]] == [0, 1, 2]
    assert len(speech_only) == 6
    assert all(item.payload_type == "SPEECH" for item in speech_only)


def test_time_window_query(corpus_database: Path) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        window = corpus.find_records_in_window(start_ms=1000, end_ms=2000)
        bounded = corpus.find_records_in_window(
            start_ms=0,
            end_ms=5000,
            transcript_id="SYNTH-CALL-0001",
        )
        by_duration = corpus.list_units_by_duration(min_duration_ms=3000)

    assert window
    assert all(item.start_ms <= 2000 and item.end_ms >= 1000 for item in window)
    assert all(item.transcript_id == "SYNTH-CALL-0001" for item in bounded)
    assert by_duration
    assert all(item.duration_ms >= 3000 for item in by_duration)

    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        with pytest.raises(ValueError):
            corpus.find_records_in_window(start_ms=10, end_ms=5)


def test_quarantine_summary(corpus_database: Path) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        summary = corpus.get_quarantine_summary()
        units = corpus.list_quarantine_units()

    assert summary.total_units == 2
    assert summary.statuses == {"QUARANTINED": 2}
    assert summary.reason_counts == {
        "ADDRESS_AMBIGUOUS": 1,
        "AMBIGUOUS_PHONE": 1,
        "PRIVACY_RESIDUAL": 1,
    }
    assert summary.affected_seq_total == 4
    assert "not present" in summary.note
    assert len(units) == 2
    assert units[0].affected_seq == [1, 2]


def test_domains_are_seeded_as_proposed(corpus_database: Path) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        domains = corpus.list_domains()
        proposed = corpus.list_domains(status="PROPOSED")

    assert [domain.domain_id for domain in domains] == [
        "D01",
        "D02",
        "D03",
        "D04",
        "D05",
        "D06",
    ]
    assert len(proposed) == 6
    classes = {domain.domain_id: domain.domain_class for domain in domains}
    assert classes["D01"] == "PRIMARY_BUSINESS"
    assert classes["D04"] == "PRIMARY_BUSINESS"
    assert classes["D05"] == "CROSS_CUTTING"
    assert classes["D06"] == "CROSS_CUTTING"
    assert all(domain.taxonomy_version == "1.0" for domain in domains)


def test_integrity_report_is_clean(corpus_database: Path) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        report = corpus.validate_integrity()

    assert report.ok
    assert report.integrity_check == "ok"
    assert report.foreign_key_errors == 0
    assert report.declared_units == report.actual_units == 3
    assert report.declared_records == report.actual_records == 9
    assert report.declared_quarantine_units == report.actual_quarantine_units == 2
    assert report.unit_record_count_mismatches == 0
    assert report.metadata_present is True
    assert report.errors == []


def test_integrity_report_detects_a_tampered_count(corpus_database: Path) -> None:
    connection = sqlite3.connect(corpus_database)
    try:
        connection.execute(
            "UPDATE transcript_units SET record_count = 99 "
            "WHERE transcript_id = 'SYNTH-CALL-0000'"
        )
        connection.commit()
    finally:
        connection.close()

    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        report = corpus.validate_integrity()

    assert not report.ok
    assert "unit_record_count_mismatch" in report.errors


def test_read_only_handle_rejects_writes(corpus_database: Path) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        connection = corpus._connection
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM transcript_records")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "UPDATE transcript_units SET duration_ms = 0"
            )


def test_open_missing_database_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        CorpusDatabase.open_read_only(tmp_path / "absent.sqlite")


def test_import_runs_and_quality_issues_are_queryable(
    corpus_database: Path,
) -> None:
    with CorpusDatabase.open_read_only(corpus_database) as corpus:
        runs = corpus.list_import_runs()
        issues = corpus.list_data_quality_issues()

    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["validation_status"] == "ok"
    assert isinstance(runs[0]["input_hashes_json"], dict)
    assert {issue.issue_code for issue in issues} >= {
        "role_unknown_for_all_records",
        "quarantined_units_excluded",
    }
    assert all(issue.resolution_status in {"open", "accepted"} for issue in issues)
