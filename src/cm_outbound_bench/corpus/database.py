"""Read-only query interface over the corpus database.

The corpus is confidential even after de-identification, so this API makes text
access explicit and off by default:

* every record view reports only structural metadata (``has_text``,
  ``text_length``) unless ``include_text=True`` is passed;
* the connection is opened with SQLite ``mode=ro`` and ``PRAGMA query_only``;
* no method prints or logs transcript text.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

from cm_outbound_bench.corpus.paths import resolve_database_path


class RecordView(BaseModel):
    """Structural view of one corpus record. Text is opt-in."""

    transcript_id: str
    seq: int
    start_ms: int
    end_ms: int
    channel_id: int
    role: str
    payload_type: str
    event: str | None = None
    is_truncated: bool = False
    asr_flags: list[str] = Field(default_factory=list)
    redaction_flags: list[str] = Field(default_factory=list)
    has_text: bool = False
    text_length: int = 0
    text_masked: str | None = None


class UnitView(BaseModel):
    transcript_id: str
    schema_version: str
    duration_ms: int
    record_count: int


class QuarantineUnitView(BaseModel):
    transcript_id: str
    reason_codes: list[str] = Field(default_factory=list)
    affected_seq: list[int] = Field(default_factory=list)
    status: str


class DomainView(BaseModel):
    domain_id: str
    name: str
    domain_class: str
    description: str
    taxonomy_version: str
    status: str


class DomainAssignmentView(BaseModel):
    assignment_id: int
    target_type: str
    target_id: str
    domain_id: str
    label_method: str
    confidence: float | None = None
    review_status: str
    is_primary: bool
    assignment_version: str
    reviewer_or_model: str | None = None
    created_at: str


class ScenarioView(BaseModel):
    scenario_id: str
    primary_domain_id: str
    secondary_domain_ids: list[str] = Field(default_factory=list)
    pattern_type: str
    observed_pattern_summary: str
    intent_labels: list[str] = Field(default_factory=list)
    speech_act_labels: list[str] = Field(default_factory=list)
    workflow_stage: str | None = None
    compliance_conditions: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    validation_status: str
    rule_provenance_status: str
    version: str
    created_at: str


class DataQualityIssue(BaseModel):
    issue_id: int
    target_type: str
    target_id: str
    issue_code: str
    severity: str
    resolution_status: str
    detail: dict[str, Any] = Field(default_factory=dict)


class CorpusStatistics(BaseModel):
    schema_version: str
    corpus_version: str
    imported_at: str
    transcript_units: int
    records: int
    speech_records: int
    event_records: int
    quarantine_units: int
    domains: int
    domain_assignments: int
    scenario_patterns: int
    data_quality_issues: int
    channels: dict[str, int] = Field(default_factory=dict)
    events: dict[str, int] = Field(default_factory=dict)
    payload_types: dict[str, int] = Field(default_factory=dict)
    roles: dict[str, int] = Field(default_factory=dict)
    review_statuses: dict[str, int] = Field(default_factory=dict)
    severity_counts: dict[str, int] = Field(default_factory=dict)


class QuarantineSummary(BaseModel):
    total_units: int
    statuses: dict[str, int] = Field(default_factory=dict)
    reason_counts: dict[str, int] = Field(default_factory=dict)
    affected_seq_total: int
    note: str = (
        "Quarantined transcript text is not present in this database. Only "
        "quarantine metadata is stored."
    )


class IntegrityReport(BaseModel):
    ok: bool
    database_path: str
    integrity_check: str
    foreign_key_errors: int
    declared_units: int
    actual_units: int
    declared_records: int
    actual_records: int
    declared_quarantine_units: int
    actual_quarantine_units: int
    unit_record_count_mismatches: int
    metadata_present: bool
    errors: list[str] = Field(default_factory=list)


def _decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class CorpusDatabase(AbstractContextManager["CorpusDatabase"]):
    """A bounded, read-only handle on a corpus database."""

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self._connection = connection
        self._path = path

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open_read_only(
        cls,
        database_path: str | Path | None = None,
    ) -> "CorpusDatabase":
        """Open an existing corpus database strictly read-only."""
        path = resolve_database_path(database_path)
        if not path.is_file():
            raise FileNotFoundError(f"Corpus database not found: {path}")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        return cls(connection, path)

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._connection.close()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- metadata ----------------------------------------------------------

    def get_metadata(self) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM corpus_metadata WHERE metadata_id = 1"
        ).fetchone()
        return dict(row) if row is not None else {}

    def get_statistics(self) -> CorpusStatistics:
        metadata = self.get_metadata()
        return CorpusStatistics(
            schema_version=str(metadata.get("schema_version", "")),
            corpus_version=str(metadata.get("corpus_version", "")),
            imported_at=str(metadata.get("imported_at", "")),
            transcript_units=self._scalar("SELECT COUNT(*) FROM transcript_units"),
            records=self._scalar("SELECT COUNT(*) FROM transcript_records"),
            speech_records=self._payload_type_count("SPEECH"),
            event_records=self._payload_type_count("EVENT"),
            quarantine_units=self._scalar("SELECT COUNT(*) FROM quarantine_units"),
            domains=self._scalar("SELECT COUNT(*) FROM domain_taxonomy"),
            domain_assignments=self._scalar(
                "SELECT COUNT(*) FROM domain_assignments"
            ),
            scenario_patterns=self._scalar("SELECT COUNT(*) FROM scenario_patterns"),
            data_quality_issues=self._scalar(
                "SELECT COUNT(*) FROM data_quality_issues"
            ),
            channels=self.count_channels(),
            events=self.count_events(),
            payload_types=self.count_payload_types(),
            roles=self.count_roles(),
            review_statuses=self.count_by(
                "domain_assignments", "review_status"
            ),
            severity_counts=self.count_by("data_quality_issues", "severity"),
        )

    # -- transcript units --------------------------------------------------

    def count_transcript_units(self) -> int:
        return self._scalar("SELECT COUNT(*) FROM transcript_units")

    def list_transcript_units(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[UnitView]:
        rows = self._connection.execute(
            """
            SELECT transcript_id, schema_version, duration_ms, record_count
              FROM transcript_units
             ORDER BY transcript_id
             LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [UnitView(**dict(row)) for row in rows]

    def get_transcript_unit(self, transcript_id: str) -> UnitView | None:
        row = self._connection.execute(
            """
            SELECT transcript_id, schema_version, duration_ms, record_count
              FROM transcript_units
             WHERE transcript_id = ?
            """,
            (transcript_id,),
        ).fetchone()
        return UnitView(**dict(row)) if row is not None else None

    def list_units_by_duration(
        self,
        *,
        min_duration_ms: int | None = None,
        max_duration_ms: int | None = None,
        limit: int = 20,
    ) -> list[UnitView]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if min_duration_ms is not None:
            clauses.append("duration_ms >= ?")
            parameters.append(min_duration_ms)
        if max_duration_ms is not None:
            clauses.append("duration_ms <= ?")
            parameters.append(max_duration_ms)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT transcript_id, schema_version, duration_ms, record_count
              FROM transcript_units
              {where}
             ORDER BY duration_ms DESC, transcript_id
             LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [UnitView(**dict(row)) for row in rows]

    # -- records -----------------------------------------------------------

    def count_records(self, transcript_id: str | None = None) -> int:
        if transcript_id is None:
            return self._scalar("SELECT COUNT(*) FROM transcript_records")
        return self._scalar(
            "SELECT COUNT(*) FROM transcript_records WHERE transcript_id = ?",
            (transcript_id,),
        )

    def list_transcript_records(
        self,
        transcript_id: str,
        *,
        payload_type: str | None = None,
        include_text: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RecordView]:
        clauses = ["transcript_id = ?"]
        parameters: list[Any] = [transcript_id]
        if payload_type is not None:
            clauses.append("payload_type = ?")
            parameters.append(payload_type)
        sql = (
            "SELECT * FROM transcript_records WHERE "
            + " AND ".join(clauses)
            + " ORDER BY seq"
        )
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            parameters.extend([limit, offset])
        rows = self._connection.execute(sql, parameters).fetchall()
        return [self._record_view(row, include_text) for row in rows]

    def iter_records(
        self,
        transcript_id: str | None = None,
        *,
        payload_type: str | None = None,
        include_text: bool = False,
        batch_size: int = 5000,
    ) -> Iterator[RecordView]:
        """Stream records with bounded memory."""
        clauses: list[str] = []
        parameters: list[Any] = []
        if transcript_id is not None:
            clauses.append("transcript_id = ?")
            parameters.append(transcript_id)
        if payload_type is not None:
            clauses.append("payload_type = ?")
            parameters.append(payload_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = self._connection.execute(
            f"""
            SELECT * FROM transcript_records
            {where}
             ORDER BY transcript_id, seq
            """,
            parameters,
        )
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                return
            for row in rows:
                yield self._record_view(row, include_text)

    def get_record(
        self,
        transcript_id: str,
        seq: int,
        *,
        include_text: bool = False,
    ) -> RecordView | None:
        row = self._connection.execute(
            """
            SELECT * FROM transcript_records
             WHERE transcript_id = ? AND seq = ?
            """,
            (transcript_id, seq),
        ).fetchone()
        return self._record_view(row, include_text) if row is not None else None

    def find_records_in_window(
        self,
        *,
        start_ms: int,
        end_ms: int,
        transcript_id: str | None = None,
        payload_type: str | None = None,
        include_text: bool = False,
        limit: int = 100,
    ) -> list[RecordView]:
        """Return records overlapping the inclusive ``[start_ms, end_ms]`` window."""
        if end_ms < start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        clauses = ["start_ms <= ?", "end_ms >= ?"]
        parameters: list[Any] = [end_ms, start_ms]
        if transcript_id is not None:
            clauses.append("transcript_id = ?")
            parameters.append(transcript_id)
        if payload_type is not None:
            clauses.append("payload_type = ?")
            parameters.append(payload_type)
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT * FROM transcript_records
             WHERE {' AND '.join(clauses)}
             ORDER BY transcript_id, start_ms, seq
             LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [self._record_view(row, include_text) for row in rows]

    # -- distributions -----------------------------------------------------

    def count_payload_types(self) -> dict[str, int]:
        return self._grouped("SELECT payload_type, COUNT(*) FROM transcript_records"
                             " GROUP BY payload_type")

    def count_events(self) -> dict[str, int]:
        return self._grouped(
            "SELECT event, COUNT(*) FROM transcript_records"
            " WHERE event IS NOT NULL GROUP BY event"
        )

    def count_channels(self) -> dict[str, int]:
        return self._grouped(
            "SELECT channel_id, COUNT(*) FROM transcript_records"
            " GROUP BY channel_id"
        )

    def count_roles(self) -> dict[str, int]:
        return self._grouped(
            "SELECT role, COUNT(*) FROM transcript_records GROUP BY role"
        )

    # -- quarantine --------------------------------------------------------

    def get_quarantine_summary(self) -> QuarantineSummary:
        statuses = self._grouped(
            "SELECT status, COUNT(*) FROM quarantine_units GROUP BY status",
            allow_null=False,
        )
        reason_counts: dict[str, int] = {}
        affected_total = 0
        rows = self._connection.execute(
            "SELECT reason_codes_json, affected_seq_json FROM quarantine_units"
        ).fetchall()
        for row in rows:
            for reason in _decode_json(row["reason_codes_json"], []):
                reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
            affected_total += len(_decode_json(row["affected_seq_json"], []))
        return QuarantineSummary(
            total_units=self._scalar("SELECT COUNT(*) FROM quarantine_units"),
            statuses=statuses,
            reason_counts=dict(sorted(reason_counts.items())),
            affected_seq_total=affected_total,
        )

    def list_quarantine_units(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[QuarantineUnitView]:
        rows = self._connection.execute(
            """
            SELECT transcript_id, reason_codes_json, affected_seq_json, status
              FROM quarantine_units
             ORDER BY transcript_id
             LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [
            QuarantineUnitView(
                transcript_id=row["transcript_id"],
                reason_codes=_decode_json(row["reason_codes_json"], []),
                affected_seq=_decode_json(row["affected_seq_json"], []),
                status=row["status"],
            )
            for row in rows
        ]

    # -- taxonomy and assignments -----------------------------------------

    def list_domains(self, *, status: str | None = None) -> list[DomainView]:
        sql = "SELECT * FROM domain_taxonomy"
        parameters: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            parameters.append(status)
        sql += " ORDER BY domain_id"
        rows = self._connection.execute(sql, parameters).fetchall()
        return [DomainView(**dict(row)) for row in rows]

    def list_domain_assignments(
        self,
        *,
        domain_id: str | None = None,
        target_type: str | None = None,
        review_status: str | None = None,
        limit: int | None = 100,
    ) -> list[DomainAssignmentView]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (
            ("domain_id", domain_id),
            ("target_type", target_type),
            ("review_status", review_status),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT * FROM domain_assignments "
            f"{where} ORDER BY assignment_id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        rows = self._connection.execute(sql, parameters).fetchall()
        return [
            DomainAssignmentView(
                assignment_id=row["assignment_id"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                domain_id=row["domain_id"],
                label_method=row["label_method"],
                confidence=row["confidence"],
                review_status=row["review_status"],
                is_primary=bool(row["is_primary"]),
                assignment_version=row["assignment_version"],
                reviewer_or_model=row["reviewer_or_model"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_scenarios(
        self,
        *,
        domain_id: str | None = None,
        validation_status: str | None = None,
        limit: int | None = 100,
    ) -> list[ScenarioView]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if domain_id is not None:
            clauses.append("primary_domain_id = ?")
            parameters.append(domain_id)
        if validation_status is not None:
            clauses.append("validation_status = ?")
            parameters.append(validation_status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM scenario_patterns {where} ORDER BY scenario_id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        rows = self._connection.execute(sql, parameters).fetchall()
        return [
            ScenarioView(
                scenario_id=row["scenario_id"],
                primary_domain_id=row["primary_domain_id"],
                secondary_domain_ids=_decode_json(
                    row["secondary_domain_ids_json"], []
                ),
                pattern_type=row["pattern_type"],
                observed_pattern_summary=row["observed_pattern_summary"],
                intent_labels=_decode_json(row["intent_labels_json"], []),
                speech_act_labels=_decode_json(row["speech_act_labels_json"], []),
                workflow_stage=row["workflow_stage"],
                compliance_conditions=_decode_json(
                    row["compliance_conditions_json"], []
                ),
                evidence_refs=_decode_json(row["evidence_refs_json"], []),
                validation_status=row["validation_status"],
                rule_provenance_status=row["rule_provenance_status"],
                version=row["version"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_data_quality_issues(self) -> list[DataQualityIssue]:
        rows = self._connection.execute(
            "SELECT * FROM data_quality_issues ORDER BY issue_id"
        ).fetchall()
        return [
            DataQualityIssue(
                issue_id=row["issue_id"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                issue_code=row["issue_code"],
                severity=row["severity"],
                resolution_status=row["resolution_status"],
                detail=_decode_json(row["detail_json"], {}),
            )
            for row in rows
        ]

    def list_import_runs(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM import_runs ORDER BY started_at"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in (
                "input_hashes_json",
                "input_counts_json",
                "output_counts_json",
            ):
                item[key] = _decode_json(item.get(key), {})
            result.append(item)
        return result

    # -- validation --------------------------------------------------------

    def validate_integrity(self) -> IntegrityReport:
        integrity = self._connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = self._connection.execute("PRAGMA foreign_key_check").fetchall()
        metadata = self.get_metadata()
        actual_units = self._scalar("SELECT COUNT(*) FROM transcript_units")
        actual_records = self._scalar("SELECT COUNT(*) FROM transcript_records")
        actual_quarantine = self._scalar("SELECT COUNT(*) FROM quarantine_units")
        declared_units = int(metadata.get("imported_transcript_units", -1))
        declared_records = int(metadata.get("imported_records", -1))
        declared_quarantine = int(metadata.get("imported_quarantine_units", -1))
        mismatches = self._scalar(
            """
            SELECT COUNT(*) FROM (
                SELECT u.transcript_id
                  FROM transcript_units AS u
                  LEFT JOIN transcript_records AS r
                    ON r.transcript_id = u.transcript_id
                 GROUP BY u.transcript_id
                HAVING u.record_count <> COUNT(r.seq)
            )
            """
        )
        errors: list[str] = []
        if integrity != "ok":
            errors.append("sqlite_integrity_check_failed")
        if foreign_keys:
            errors.append("foreign_key_violation")
        if not metadata:
            errors.append("missing_corpus_metadata")
        if declared_units != actual_units:
            errors.append("declared_unit_count_mismatch")
        if declared_records != actual_records:
            errors.append("declared_record_count_mismatch")
        if declared_quarantine != actual_quarantine:
            errors.append("declared_quarantine_count_mismatch")
        if mismatches:
            errors.append("unit_record_count_mismatch")
        return IntegrityReport(
            ok=not errors,
            database_path=str(self._path),
            integrity_check=integrity,
            foreign_key_errors=len(foreign_keys),
            declared_units=declared_units,
            actual_units=actual_units,
            declared_records=declared_records,
            actual_records=actual_records,
            declared_quarantine_units=declared_quarantine,
            actual_quarantine_units=actual_quarantine,
            unit_record_count_mismatches=mismatches,
            metadata_present=bool(metadata),
            errors=errors,
        )

    # -- internals ---------------------------------------------------------

    def scalar(self, sql: str, parameters: tuple[Any, ...] = ()) -> int:
        """Run a bounded aggregate query and return one integer.

        This is the only generic SQL entry point exposed for reporting. It is
        intentionally limited to scalar results so that bulk transcript text
        cannot be pulled through it.
        """
        row = self._connection.execute(sql, parameters).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0

    def scalar_float(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> float | None:
        row = self._connection.execute(sql, parameters).fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def rows(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        """Run a bounded aggregate query and return row mappings.

        Callers are expected to aggregate; transcript text is not exposed here
        because reports never select it.
        """
        return [
            dict(row) for row in self._connection.execute(sql, parameters).fetchall()
        ]

    def _scalar(self, sql: str, parameters: tuple[Any, ...] = ()) -> int:
        return self.scalar(sql, parameters)

    def _grouped(
        self,
        sql: str,
        *,
        allow_null: bool = True,
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for key, value in self._connection.execute(sql).fetchall():
            if key is None and not allow_null:
                key = "UNKNOWN"
            label = "null" if key is None else str(key)
            result[label] = int(value)
        return dict(sorted(result.items()))

    def count_by(self, table: str, column: str) -> dict[str, int]:
        allowed = {
            ("domain_assignments", "review_status"),
            ("domain_assignments", "label_method"),
            ("domain_assignments", "domain_id"),
            ("data_quality_issues", "severity"),
            ("data_quality_issues", "resolution_status"),
            ("scenario_patterns", "validation_status"),
            ("scenario_patterns", "rule_provenance_status"),
        }
        if (table, column) not in allowed:
            raise ValueError(f"Unsupported count column: {table}.{column}")
        return self._grouped(f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}")

    def _payload_type_count(self, payload_type: str) -> int:
        return self._scalar(
            "SELECT COUNT(*) FROM transcript_records WHERE payload_type = ?",
            (payload_type,),
        )

    @staticmethod
    def _record_view(row: sqlite3.Row, include_text: bool) -> RecordView:
        text = row["text_masked"]
        return RecordView(
            transcript_id=row["transcript_id"],
            seq=row["seq"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            channel_id=row["channel_id"],
            role=row["role"],
            payload_type=row["payload_type"],
            event=row["event"],
            is_truncated=bool(row["is_truncated"]),
            asr_flags=_decode_json(row["asr_flags_json"], []),
            redaction_flags=_decode_json(row["redaction_flags_json"], []),
            has_text=bool(text),
            text_length=len(text) if text else 0,
            text_masked=text if include_text else None,
        )
