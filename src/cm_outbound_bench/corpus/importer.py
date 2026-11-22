"""Streaming, validated, atomic import of the corpus into SQLite.

Import guarantees:

* every declared manifest hash and count is recomputed first;
* rows are written inside a single transaction into a temporary database file;
* the temporary database is closed, reopened and integrity-checked;
* only then is it published with an atomic ``os.replace``;
* an existing database is never overwritten without an explicit ``rebuild``;
* repeated imports of identical inputs are a verified no-op.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

from cm_outbound_bench.corpus import schema
from cm_outbound_bench.corpus.manifest import (
    CALLS_FILENAME,
    QUARANTINE_FILENAME,
    CorpusInputError,
    ManifestValidation,
    sha256_file,
    validate_inputs,
)
from cm_outbound_bench.corpus.paths import resolve_corpus_dir, resolve_database_path

_RECORD_FIELDS = (
    "seq",
    "start_ms",
    "end_ms",
    "channel_id",
    "role",
    "payload_type",
    "event",
    "text_masked",
    "is_truncated",
    "asr_flags",
    "redaction_flags",
)


class ImportStatus(str, Enum):
    IMPORTED = "imported"
    ALREADY_PRESENT = "already_present"
    REBUILT = "rebuilt"


class CorpusImportError(RuntimeError):
    """Raised when an import cannot be completed safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class CountSummary(BaseModel):
    transcript_units: int = 0
    records: int = 0
    speech_records: int = 0
    event_records: int = 0
    quarantine_units: int = 0


class ImportReport(BaseModel):
    status: ImportStatus
    database_path: str
    database_sha256: str = ""
    schema_version: str
    importer_version: str
    corpus_version: str
    run_id: str = ""
    manifest_hash: str = ""
    calls_hash: str = ""
    quarantine_hash: str = ""
    expected: CountSummary = Field(default_factory=CountSummary)
    imported: CountSummary = Field(default_factory=CountSummary)
    started_at: str = ""
    completed_at: str = ""
    validation_status: str = "ok"
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def counts_match(self) -> bool:
        return self.expected == self.imported


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reject_text(value: Any, field: str) -> None:
    """Fail loudly when a text field has an unexpected type."""
    if value is not None and not isinstance(value, str):
        raise CorpusInputError(f"Unexpected value type for corpus field {field!r}")


def _reject_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CorpusInputError(f"Unexpected value type for corpus field {field!r}")
    return value


def _flags(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CorpusInputError(f"Unexpected value type for corpus field {field!r}")
    return value


def _flags_json(value: Any, field: str) -> str:
    return json.dumps(_flags(value, field), ensure_ascii=False, separators=(",", ":"))


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise CorpusInputError(
                    f"Invalid JSON on line {line_number} of {path.name}"
                ) from error
            if not isinstance(payload, dict):
                raise CorpusInputError(
                    f"Line {line_number} of {path.name} is not a JSON object"
                )
            yield payload


def open_database(path: Path) -> sqlite3.Connection:
    """Open a writable connection with foreign keys enabled."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def read_metadata(path: Path) -> dict[str, Any] | None:
    """Return the ``corpus_metadata`` row of an existing database, if readable."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM corpus_metadata WHERE metadata_id = 1"
        ).fetchone()
        return dict(row) if row is not None else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _existing_is_equivalent(
    metadata: dict[str, Any] | None,
    validation: ManifestValidation,
) -> bool:
    if metadata is None:
        return False
    return (
        metadata.get("schema_version") == schema.SCHEMA_VERSION
        and metadata.get("corpus_version") == validation.dataset
        and metadata.get("source_calls_hash") == validation.calls_hash
        and metadata.get("source_quarantine_hash") == validation.quarantine_hash
        and metadata.get("source_manifest_hash") == validation.manifest_hash
        and metadata.get("imported_transcript_units") == validation.expected_units
        and metadata.get("imported_records") == validation.expected_records
        and metadata.get("imported_quarantine_units")
        == validation.expected_quarantine_units
    )


def import_corpus(
    database_path: str | Path | None = None,
    corpus_dir: str | Path | None = None,
    *,
    rebuild: bool = False,
    batch_size: int = 2000,
) -> ImportReport:
    """Import the final de-identified corpus into a fresh SQLite database."""
    target = resolve_database_path(database_path)
    source = resolve_corpus_dir(corpus_dir)
    validation = validate_inputs(source)
    if not validation.ok:
        raise CorpusImportError(
            "corpus_input_mismatch",
            "Corpus inputs do not match their manifest: "
            + ", ".join(validation.errors),
        )

    expected = CountSummary(
        transcript_units=validation.expected_units,
        records=validation.expected_records,
        speech_records=validation.expected_speech_records,
        event_records=validation.expected_event_records,
        quarantine_units=validation.expected_quarantine_units,
    )

    if target.exists():
        metadata = read_metadata(target)
        equivalent = _existing_is_equivalent(metadata, validation)
        if equivalent and not rebuild:
            return ImportReport(
                status=ImportStatus.ALREADY_PRESENT,
                database_path=str(target),
                database_sha256=sha256_file(target),
                schema_version=schema.SCHEMA_VERSION,
                importer_version=schema.IMPORTER_VERSION,
                corpus_version=validation.dataset,
                manifest_hash=validation.manifest_hash,
                calls_hash=validation.calls_hash,
                quarantine_hash=validation.quarantine_hash,
                expected=expected,
                imported=expected,
                validation_status="ok",
                notes=["Existing database already matches the inputs exactly."],
            )
        if not rebuild:
            raise CorpusImportError(
                "database_exists",
                f"{target} already exists and does not match the current inputs. "
                "Re-run with rebuild=True to replace it.",
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    status = ImportStatus.REBUILT if target.exists() else ImportStatus.IMPORTED
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    started_at = _utc_now()
    run_id = f"RUN-{uuid.uuid4().hex[:16]}"

    try:
        imported, quality = _populate(
            temporary,
            validation,
            run_id=run_id,
            started_at=started_at,
            batch_size=batch_size,
        )
        _verify_counts(imported, expected)
        _verify_database_file(temporary, imported)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return ImportReport(
        status=status,
        database_path=str(target),
        database_sha256=sha256_file(target),
        schema_version=schema.SCHEMA_VERSION,
        importer_version=schema.IMPORTER_VERSION,
        corpus_version=validation.dataset,
        run_id=run_id,
        manifest_hash=validation.manifest_hash,
        calls_hash=validation.calls_hash,
        quarantine_hash=validation.quarantine_hash,
        expected=expected,
        imported=imported,
        started_at=started_at,
        completed_at=_utc_now(),
        validation_status="ok",
        notes=quality,
    )


def _populate(
    path: Path,
    validation: ManifestValidation,
    *,
    run_id: str,
    started_at: str,
    batch_size: int,
) -> tuple[CountSummary, list[str]]:
    connection = open_database(path)
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")
    try:
        schema.create_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO import_runs (
                run_id, importer_version, schema_version, started_at,
                status, input_hashes_json, input_counts_json,
                output_counts_json, validation_status, error_summary
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, '{}', 'pending', NULL)
            """,
            (
                run_id,
                schema.IMPORTER_VERSION,
                schema.SCHEMA_VERSION,
                started_at,
                json.dumps(
                    {
                        "manifest": validation.manifest_hash,
                        "calls": validation.calls_hash,
                        "quarantine": validation.quarantine_hash,
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "transcript_units": validation.expected_units,
                        "records": validation.expected_records,
                        "quarantine_units": validation.expected_quarantine_units,
                    },
                    sort_keys=True,
                ),
            ),
        )

        imported, anomalies = _import_units(
            connection, Path(validation.calls_path), run_id, batch_size
        )
        imported.quarantine_units = _import_quarantine(
            connection, Path(validation.quarantine_path), run_id
        )
        quality_notes = _record_quality_issues(
            connection, run_id, imported, anomalies
        )
        _seed_taxonomy(connection)
        _write_metadata(
            connection,
            validation,
            run_id=run_id,
            imported=imported,
        )

        connection.execute(
            """
            UPDATE import_runs
               SET completed_at = ?, status = 'completed',
                   output_counts_json = ?, validation_status = 'ok'
             WHERE run_id = ?
            """,
            (
                _utc_now(),
                json.dumps(imported.model_dump(), sort_keys=True),
                run_id,
            ),
        )
        connection.commit()
        return imported, quality_notes
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _import_units(
    connection: sqlite3.Connection,
    calls_path: Path,
    run_id: str,
    batch_size: int,
) -> tuple[CountSummary, dict[str, int]]:
    summary = CountSummary()
    anomalies: dict[str, int] = {
        "units_with_noncontiguous_seq": 0,
        "units_with_timing_inversion": 0,
        "units_with_unexpected_schema_version": 0,
    }
    unit_rows: list[tuple[Any, ...]] = []
    record_rows: list[tuple[Any, ...]] = []

    for unit in _iter_jsonl(calls_path):
        transcript_id = unit.get("transcript_id")
        if not isinstance(transcript_id, str) or not transcript_id:
            raise CorpusInputError("Corpus unit is missing a transcript_id")
        unit_schema_version = unit.get("schema_version")
        _reject_text(unit_schema_version, "schema_version")
        if unit_schema_version != schema.CORPUS_UNIT_SCHEMA_VERSION:
            anomalies["units_with_unexpected_schema_version"] += 1
        duration_ms = _reject_int(unit.get("duration_ms"), "duration_ms")
        records = unit.get("records")
        if not isinstance(records, list):
            raise CorpusInputError("Corpus unit is missing a records list")

        previous_start = -1
        timing_inversion = False
        seq_contiguous = True
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise CorpusInputError("Corpus record is not a JSON object")
            missing = [field for field in _RECORD_FIELDS if field not in record]
            if missing:
                raise CorpusInputError(
                    "Corpus record is missing required fields: "
                    + ", ".join(sorted(missing))
                )
            seq = _reject_int(record["seq"], "seq")
            start_ms = _reject_int(record["start_ms"], "start_ms")
            end_ms = _reject_int(record["end_ms"], "end_ms")
            channel_id = _reject_int(record["channel_id"], "channel_id")
            role = record["role"]
            payload_type = record["payload_type"]
            _reject_text(role, "role")
            _reject_text(payload_type, "payload_type")
            event = record["event"]
            text_masked = record["text_masked"]
            _reject_text(event, "event")
            _reject_text(text_masked, "text_masked")
            is_truncated = record["is_truncated"]
            if not isinstance(is_truncated, bool):
                raise CorpusInputError(
                    "Unexpected value type for corpus field 'is_truncated'"
                )
            if seq != index:
                seq_contiguous = False
            if start_ms < previous_start:
                timing_inversion = True
            previous_start = start_ms

            if payload_type == "SPEECH":
                summary.speech_records += 1
            elif payload_type == "EVENT":
                summary.event_records += 1
            else:
                raise CorpusInputError("Unknown payload_type in corpus record")

            record_rows.append(
                (
                    transcript_id,
                    seq,
                    start_ms,
                    end_ms,
                    channel_id,
                    role,
                    payload_type,
                    event,
                    text_masked,
                    1 if is_truncated else 0,
                    _flags_json(record["asr_flags"], "asr_flags"),
                    _flags_json(record["redaction_flags"], "redaction_flags"),
                )
            )

        if not seq_contiguous:
            anomalies["units_with_noncontiguous_seq"] += 1
        if timing_inversion:
            anomalies["units_with_timing_inversion"] += 1

        unit_rows.append(
            (
                transcript_id,
                unit_schema_version or "",
                duration_ms,
                len(records),
                run_id,
            )
        )
        summary.transcript_units += 1
        summary.records += len(records)

        if len(record_rows) >= batch_size:
            _flush_units(connection, unit_rows, record_rows)
            unit_rows.clear()
            record_rows.clear()

    _flush_units(connection, unit_rows, record_rows)
    return summary, anomalies


def _flush_units(
    connection: sqlite3.Connection,
    unit_rows: list[tuple[Any, ...]],
    record_rows: list[tuple[Any, ...]],
) -> None:
    if unit_rows:
        connection.executemany(
            """
            INSERT INTO transcript_units (
                transcript_id, schema_version, duration_ms, record_count,
                processing_run_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            unit_rows,
        )
    if record_rows:
        connection.executemany(
            """
            INSERT INTO transcript_records (
                transcript_id, seq, start_ms, end_ms, channel_id, role,
                payload_type, event, text_masked, is_truncated,
                asr_flags_json, redaction_flags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            record_rows,
        )


def _import_quarantine(
    connection: sqlite3.Connection,
    quarantine_path: Path,
    run_id: str,
) -> int:
    rows: list[tuple[Any, ...]] = []
    for unit in _iter_jsonl(quarantine_path):
        transcript_id = unit.get("transcript_id")
        if not isinstance(transcript_id, str) or not transcript_id:
            raise CorpusInputError("Quarantine unit is missing a transcript_id")
        status = unit.get("status")
        _reject_text(status, "status")
        affected = unit.get("affected_seq", [])
        if not isinstance(affected, list) or any(
            not isinstance(item, int) or isinstance(item, bool) for item in affected
        ):
            raise CorpusInputError("Unexpected affected_seq type in quarantine unit")
        rows.append(
            (
                transcript_id,
                _flags_json(unit.get("reason_codes", []), "reason_codes"),
                json.dumps(affected, separators=(",", ":")),
                status or "",
                run_id,
            )
        )
    if rows:
        connection.executemany(
            """
            INSERT INTO quarantine_units (
                transcript_id, reason_codes_json, affected_seq_json, status,
                processing_run_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def _record_quality_issues(
    connection: sqlite3.Connection,
    run_id: str,
    imported: CountSummary,
    anomalies: dict[str, int],
) -> list[str]:
    """Record deterministic, aggregate import observations."""
    empty_after_mask = connection.execute(
        """
        SELECT COUNT(*) FROM transcript_records
         WHERE asr_flags_json LIKE '%empty_after_mask%'
        """
    ).fetchone()[0]
    truncated = connection.execute(
        "SELECT COUNT(*) FROM transcript_records WHERE is_truncated = 1"
    ).fetchone()[0]
    sources = connection.execute(
        "SELECT COUNT(*) FROM source_file_outcomes"
    ).fetchone()[0]

    issues: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
        (
            "CORPUS",
            schema.CORPUS_VERSION,
            "role_unknown_for_all_records",
            "warning",
            "accepted",
            {
                "records": imported.records,
                "note": (
                    "Every corpus record has role UNKNOWN. Speaker roles must not "
                    "be inferred from channel identifiers."
                ),
            },
        ),
        (
            "CORPUS",
            schema.CORPUS_VERSION,
            "channel_semantics_unconfirmed",
            "warning",
            "open",
            {
                "note": (
                    "Channel identifiers are preserved verbatim. Their meaning has "
                    "not been verified and must not be treated as a speaker label."
                )
            },
        ),
        (
            "CORPUS",
            schema.CORPUS_VERSION,
            "source_file_outcomes_unavailable",
            "warning",
            "accepted",
            {
                "rows": sources,
                "note": (
                    "The protected HMAC source mapping is not available to this "
                    "importer, so per-file provenance cannot be reconstructed."
                ),
            },
        ),
        (
            "CORPUS",
            schema.CORPUS_VERSION,
            "records_empty_after_masking",
            "info",
            "accepted",
            {"records": empty_after_mask},
        ),
        (
            "CORPUS",
            schema.CORPUS_VERSION,
            "records_truncated",
            "info",
            "accepted",
            {"records": truncated},
        ),
        (
            "CORPUS",
            schema.CORPUS_VERSION,
            "quarantined_units_excluded",
            "info",
            "accepted",
            {
                "units": imported.quarantine_units,
                "note": "Quarantined transcript text is not stored in this database.",
            },
        ),
    ]
    connection.executemany(
        """
        INSERT INTO data_quality_issues (
            target_type, target_id, issue_code, severity, resolution_status,
            processing_run_id, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                target_type,
                target_id,
                issue_code,
                severity,
                resolution,
                run_id,
                json.dumps(detail, sort_keys=True),
            )
            for target_type, target_id, issue_code, severity, resolution, detail
            in issues
        ],
    )
    for code, count in sorted(anomalies.items()):
        if count:
            connection.execute(
                """
                INSERT INTO data_quality_issues (
                    target_type, target_id, issue_code, severity,
                    resolution_status, processing_run_id, detail_json
                ) VALUES ('CORPUS', ?, ?, 'warning', 'open', ?, ?)
                """,
                (
                    schema.CORPUS_VERSION,
                    code,
                    run_id,
                    json.dumps({"units": count}, sort_keys=True),
                ),
            )
    return [issue[2] for issue in issues]


def _seed_taxonomy(connection: sqlite3.Connection) -> None:
    connection.executemany(
        """
        INSERT INTO domain_taxonomy (
            domain_id, name, domain_class, description, taxonomy_version, status
        ) VALUES (?, ?, ?, ?, ?, 'PROPOSED')
        """,
        [
            (domain_id, name, domain_class, description, schema.TAXONOMY_VERSION)
            for domain_id, name, domain_class, description in schema.DOMAIN_SEEDS
        ],
    )


def _write_metadata(
    connection: sqlite3.Connection,
    validation: ManifestValidation,
    *,
    run_id: str,
    imported: CountSummary,
) -> None:
    connection.execute(
        """
        INSERT INTO corpus_metadata (
            metadata_id, schema_version, corpus_version, source_manifest_hash,
            source_calls_hash, source_quarantine_hash, imported_at,
            expected_transcript_units, imported_transcript_units,
            expected_records, imported_records, expected_quarantine_units,
            imported_quarantine_units, importer_version, notes
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            schema.SCHEMA_VERSION,
            validation.dataset or schema.CORPUS_VERSION,
            validation.manifest_hash,
            validation.calls_hash,
            validation.quarantine_hash,
            _utc_now(),
            validation.expected_units,
            imported.transcript_units,
            validation.expected_records,
            imported.records,
            validation.expected_quarantine_units,
            imported.quarantine_units,
            schema.IMPORTER_VERSION,
            f"import_run={run_id}; corpus={CALLS_FILENAME}+{QUARANTINE_FILENAME}",
        ),
    )


def _verify_counts(imported: CountSummary, expected: CountSummary) -> None:
    differences = {
        field: (getattr(expected, field), getattr(imported, field))
        for field in type(expected).model_fields
        if getattr(expected, field) != getattr(imported, field)
    }
    if differences:
        detail = ", ".join(
            f"{field}: expected {pair[0]}, imported {pair[1]}"
            for field, pair in sorted(differences.items())
        )
        raise CorpusImportError("count_mismatch", detail)


def _verify_database_file(path: Path, imported: CountSummary) -> None:
    """Reopen the temporary database and prove it is structurally sound."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise CorpusImportError(
                "integrity_check_failed",
                f"SQLite integrity check returned {integrity!r}",
            )
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise CorpusImportError(
                "foreign_key_check_failed",
                f"{len(foreign_key_errors)} foreign key violations",
            )
        units = connection.execute(
            "SELECT COUNT(*) FROM transcript_units"
        ).fetchone()[0]
        records = connection.execute(
            "SELECT COUNT(*) FROM transcript_records"
        ).fetchone()[0]
        quarantine = connection.execute(
            "SELECT COUNT(*) FROM quarantine_units"
        ).fetchone()[0]
        mismatch = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT u.transcript_id, u.record_count,
                       COUNT(r.seq) AS actual
                  FROM transcript_units AS u
                  LEFT JOIN transcript_records AS r
                    ON r.transcript_id = u.transcript_id
                 GROUP BY u.transcript_id
                HAVING u.record_count <> actual
            )
            """
        ).fetchone()[0]
    finally:
        connection.close()

    if units != imported.transcript_units:
        raise CorpusImportError("unit_count_mismatch", "unit rows changed on reopen")
    if records != imported.records:
        raise CorpusImportError("record_count_mismatch", "record rows changed on reopen")
    if quarantine != imported.quarantine_units:
        raise CorpusImportError(
            "quarantine_count_mismatch", "quarantine rows changed on reopen"
        )
    if mismatch:
        raise CorpusImportError(
            "unit_record_count_mismatch",
            f"{mismatch} transcript units declare a wrong record_count",
        )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - platform dependent
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - platform dependent
        return
    finally:
        os.close(descriptor)
