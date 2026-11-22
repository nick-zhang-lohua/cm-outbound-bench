from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cm_outbound_bench.corpus.importer import (
    CorpusImportError,
    ImportStatus,
    import_corpus,
)
from cm_outbound_bench.corpus.manifest import CorpusInputError, sha256_file
from corpus_support import (
    build_corpus,
    read_manifest,
    rewrite_manifest,
)
from corpus_support import (
    sha256_file as support_sha256,
)


def test_import_creates_the_expected_rows(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"

    report = import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    assert report.status is ImportStatus.IMPORTED
    assert report.validation_status == "ok"
    assert report.expected == report.imported
    assert report.imported.transcript_units == 3
    assert report.imported.records == 9
    assert report.imported.quarantine_units == 2
    assert report.imported.speech_records == 6
    assert report.imported.event_records == 3
    assert database.is_file()

    with sqlite3.connect(database) as connection:
        units = connection.execute("SELECT COUNT(*) FROM transcript_units").fetchone()[0]
        records = connection.execute(
            "SELECT COUNT(*) FROM transcript_records"
        ).fetchone()[0]
        quarantine = connection.execute(
            "SELECT COUNT(*) FROM quarantine_units"
        ).fetchone()[0]
        metadata = connection.execute(
            "SELECT * FROM corpus_metadata WHERE metadata_id = 1"
        ).fetchone()
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(transcript_records)"
            ).fetchall()
        ]
        domains = connection.execute(
            "SELECT domain_id, status FROM domain_taxonomy ORDER BY domain_id"
        ).fetchall()

    assert (units, records, quarantine) == (3, 9, 2)
    assert metadata is not None
    assert metadata[1] == "1.0"
    assert metadata[2] == "synthetic_corpus_test"
    assert metadata[3] == report.manifest_hash
    assert metadata[4] == report.calls_hash
    assert metadata[5] == report.quarantine_hash
    assert metadata[7] == 3  # expected_transcript_units
    assert metadata[8] == 3  # imported_transcript_units
    assert metadata[9] == 9  # expected_records
    assert metadata[10] == 9  # imported_records
    assert metadata[11] == 2  # expected_quarantine_units
    assert metadata[12] == 2  # imported_quarantine_units

    for field in (
        "seq",
        "start_ms",
        "end_ms",
        "channel_id",
        "role",
        "payload_type",
        "event",
        "text_masked",
        "is_truncated",
        "asr_flags_json",
        "redaction_flags_json",
    ):
        assert field in columns

    assert [tuple(row) for row in domains] == [
        ("D01", "PROPOSED"),
        ("D02", "PROPOSED"),
        ("D03", "PROPOSED"),
        ("D04", "PROPOSED"),
        ("D05", "PROPOSED"),
        ("D06", "PROPOSED"),
    ]


def test_import_preserves_zero_based_sequence_and_field_values(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM transcript_records
             WHERE transcript_id = 'SYNTH-CALL-0000'
             ORDER BY seq
            """
        ).fetchall()

    assert [row["seq"] for row in rows] == [0, 1, 2]
    assert [row["payload_type"] for row in rows] == ["SPEECH", "EVENT", "SPEECH"]
    assert rows[0]["event"] is None
    assert rows[1]["event"] == "SYSTEM"
    assert rows[1]["text_masked"] is None
    assert rows[0]["text_masked"].startswith("PRIVATE_MARKER_SHOULD_NOT_LEAK")
    assert rows[0]["is_truncated"] == 1
    assert json.loads(rows[0]["asr_flags_json"]) == []
    assert json.loads(rows[2]["redaction_flags_json"]) == ["PHONE"]
    assert rows[1]["channel_id"] == 1
    assert rows[1]["start_ms"] == 1000
    assert rows[1]["end_ms"] == 1900


def test_import_preserves_duplicate_record_content(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    """Identical content at different sequence numbers is not deduplicated."""
    database = tmp_path / "corpus.sqlite"
    import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    with sqlite3.connect(database) as connection:
        duplicated = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT text_masked FROM transcript_records
                 WHERE text_masked IS NOT NULL
                 GROUP BY text_masked HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        total = connection.execute(
            "SELECT COUNT(*) FROM transcript_records WHERE text_masked IS NOT NULL"
        ).fetchone()[0]

    assert duplicated == 0  # fixtures are unique, so nothing was collapsed
    assert total == 6


def test_repeated_import_is_a_verified_no_op(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    first = import_corpus(database_path=database, corpus_dir=synthetic_corpus)
    hash_before = sha256_file(database)

    second = import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    assert second.status is ImportStatus.ALREADY_PRESENT
    assert second.database_sha256 == first.database_sha256
    assert sha256_file(database) == hash_before
    assert second.run_id == ""
    assert second.imported == first.imported


def test_existing_database_with_different_inputs_requires_rebuild(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    other_corpus = build_corpus(tmp_path / "other", units=2, records_per_unit=2)

    with pytest.raises(CorpusImportError) as excinfo:
        import_corpus(database_path=database, corpus_dir=other_corpus)
    assert excinfo.value.code == "database_exists"

    report = import_corpus(
        database_path=database,
        corpus_dir=other_corpus,
        rebuild=True,
    )
    assert report.status is ImportStatus.REBUILT
    assert report.imported.transcript_units == 2
    assert report.imported.records == 4


def test_rebuild_flag_still_refuses_corrupt_inputs(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    import_corpus(database_path=database, corpus_dir=synthetic_corpus)
    original_hash = sha256_file(database)

    calls = synthetic_corpus / "calls.jsonl"
    calls.write_text(calls.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(CorpusImportError) as excinfo:
        import_corpus(database_path=database, corpus_dir=synthetic_corpus, rebuild=True)
    assert excinfo.value.code == "corpus_input_mismatch"
    assert sha256_file(database) == original_hash


def test_manifest_hash_mismatch_aborts_before_writing(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    calls = synthetic_corpus / "calls.jsonl"
    calls.write_text(calls.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(CorpusImportError) as excinfo:
        import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    assert excinfo.value.code == "corpus_input_mismatch"
    assert "calls_hash_mismatch" in str(excinfo.value)
    assert not database.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_declared_count_mismatch_is_detected(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    rewrite_manifest(synthetic_corpus, records=999)

    with pytest.raises(CorpusImportError) as excinfo:
        import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    assert excinfo.value.code == "count_mismatch"
    assert not database.exists()


def test_invalid_json_leaves_no_database_behind(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    calls = synthetic_corpus / "calls.jsonl"
    lines = calls.read_text(encoding="utf-8").splitlines()
    lines[1] = "{not valid json"
    calls.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = read_manifest(synthetic_corpus)
    manifest["output"]["files"]["calls.jsonl"]["sha256"] = support_sha256(calls)
    (synthetic_corpus / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(CorpusInputError):
        import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    assert not database.exists()
    leftovers = [path.name for path in tmp_path.iterdir() if path.suffix == ".tmp"]
    assert leftovers == []


def test_missing_record_field_is_rejected(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    calls = synthetic_corpus / "calls.jsonl"
    units = [
        json.loads(line)
        for line in calls.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    del units[0]["records"][0]["redaction_flags"]
    calls.write_text(
        "\n".join(json.dumps(unit, separators=(",", ":")) for unit in units) + "\n",
        encoding="utf-8",
    )
    manifest = read_manifest(synthetic_corpus)
    manifest["output"]["files"]["calls.jsonl"]["sha256"] = support_sha256(calls)
    (synthetic_corpus / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(CorpusInputError) as excinfo:
        import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    assert "redaction_flags" in str(excinfo.value)
    assert not database.exists()


def test_import_report_records_quality_observations(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    report = import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    assert "role_unknown_for_all_records" in report.notes
    assert "channel_semantics_unconfirmed" in report.notes
    assert "source_file_outcomes_unavailable" in report.notes

    with sqlite3.connect(database) as connection:
        issues = connection.execute(
            "SELECT issue_code, severity FROM data_quality_issues ORDER BY issue_id"
        ).fetchall()
        source_rows = connection.execute(
            "SELECT COUNT(*) FROM source_file_outcomes"
        ).fetchone()[0]

    assert source_rows == 0
    codes = {code for code, _ in issues}
    assert "role_unknown_for_all_records" in codes
    assert all(severity in {"info", "warning"} for _, severity in issues)


def test_foreign_keys_are_enforced_on_the_imported_database(
    tmp_path: Path, synthetic_corpus: Path
) -> None:
    database = tmp_path / "corpus.sqlite"
    import_corpus(database_path=database, corpus_dir=synthetic_corpus)

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO transcript_records (
                    transcript_id, seq, start_ms, end_ms, channel_id, role,
                    payload_type, event, text_masked, is_truncated,
                    asr_flags_json, redaction_flags_json
                ) VALUES ('MISSING-UNIT', 0, 0, 1, 1, 'UNKNOWN', 'SPEECH',
                          NULL, NULL, 0, '[]', '[]')
                """
            )
    finally:
        connection.close()
