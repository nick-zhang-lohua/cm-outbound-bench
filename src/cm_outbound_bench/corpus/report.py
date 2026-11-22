"""Aggregate domain-coverage reporting.

Reports are aggregate only. They never contain transcript text, and they
explicitly separate candidate labels from human-reviewed labels so that keyword
coverage can never be mistaken for verified business-intent prevalence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from cm_outbound_bench.corpus.database import CorpusDatabase
from cm_outbound_bench.corpus.paths import resolve_database_path
from cm_outbound_bench.corpus.schema import TAXONOMY_VERSION
from cm_outbound_bench.corpus.taxonomy import TAXONOMY, UNCLASSIFIED

CONFIDENCE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("lt_0.50", 0.0, 0.5),
    ("0.50_0.64", 0.5, 0.65),
    ("0.65_0.79", 0.65, 0.8),
    ("0.80_0.94", 0.8, 0.95),
    ("ge_0.95", 0.95, 1.01),
)


class DomainCoverage(BaseModel):
    domain_id: str
    name: str
    domain_class: str
    taxonomy_status: str
    record_assignments: int = 0
    unit_assignments: int = 0
    primary_unit_assignments: int = 0
    record_coverage_pct: float = 0.0
    unit_coverage_pct: float = 0.0
    mean_confidence: float | None = None
    confidence_buckets: dict[str, int] = Field(default_factory=dict)


class CooccurrenceCell(BaseModel):
    domain_a: str
    domain_b: str
    unit_count: int


class DomainCoverageReport(BaseModel):
    generated_at: str
    database_path: str
    taxonomy_version: str
    assignment_version: str
    label_method: str
    review_status_mix: dict[str, int] = Field(default_factory=dict)
    records_total: int = 0
    speech_records_total: int = 0
    eligible_speech_records: int = 0
    assigned_records: int = 0
    unclassified_eligible_records: int = 0
    multi_label_records: int = 0
    ambiguous_records: int = 0
    units_total: int = 0
    units_assigned: int = 0
    unclassified_units: int = 0
    multi_domain_units: int = 0
    payload_type_coverage: dict[str, dict[str, int]] = Field(default_factory=dict)
    domains: list[DomainCoverage] = Field(default_factory=list)
    cooccurrence: list[CooccurrenceCell] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_coverage_report(
    database_path: str | Path | None = None,
) -> DomainCoverageReport:
    """Build an aggregate coverage report from stored assignments."""
    path = resolve_database_path(database_path)
    with CorpusDatabase.open_read_only(path) as corpus:
        records_total = corpus.count_records()
        speech_records = corpus.scalar(
            "SELECT COUNT(*) FROM transcript_records WHERE payload_type = 'SPEECH'"
        )
        eligible_speech = corpus.scalar(
            """
            SELECT COUNT(*) FROM transcript_records
             WHERE payload_type = 'SPEECH'
               AND text_masked IS NOT NULL
               AND length(trim(text_masked)) >= 4
            """
        )
        units_total = corpus.count_transcript_units()
        assignment_version = _assignment_version(corpus)

        assigned_records = corpus.scalar(
            """
            SELECT COUNT(DISTINCT target_id) FROM domain_assignments
             WHERE target_type = 'RECORD' AND assignment_version = ?
            """,
            (assignment_version,),
        )
        multi_label_records = corpus.scalar(
            """
            SELECT COUNT(*) FROM (
                SELECT target_id FROM domain_assignments
                 WHERE target_type = 'RECORD' AND assignment_version = ?
                 GROUP BY target_id HAVING COUNT(*) > 1
            )
            """,
            (assignment_version,),
        )
        units_assigned = corpus.scalar(
            """
            SELECT COUNT(DISTINCT target_id) FROM domain_assignments
             WHERE target_type = 'TRANSCRIPT_UNIT' AND assignment_version = ?
            """,
            (assignment_version,),
        )
        multi_domain_units = corpus.scalar(
            """
            SELECT COUNT(*) FROM (
                SELECT target_id FROM domain_assignments
                 WHERE target_type = 'TRANSCRIPT_UNIT'
                   AND assignment_version = ?
                 GROUP BY target_id HAVING COUNT(DISTINCT domain_id) > 1
            )
            """,
            (assignment_version,),
        )

        domains: list[DomainCoverage] = []
        for definition in TAXONOMY:
            record_assignments = corpus.scalar(
                """
                SELECT COUNT(*) FROM domain_assignments
                 WHERE target_type = 'RECORD' AND assignment_version = ?
                   AND domain_id = ?
                """,
                (assignment_version, definition.domain_id),
            )
            unit_assignments = corpus.scalar(
                """
                SELECT COUNT(*) FROM domain_assignments
                 WHERE target_type = 'TRANSCRIPT_UNIT'
                   AND assignment_version = ? AND domain_id = ?
                """,
                (assignment_version, definition.domain_id),
            )
            primary_units = corpus.scalar(
                """
                SELECT COUNT(*) FROM domain_assignments
                 WHERE target_type = 'TRANSCRIPT_UNIT'
                   AND assignment_version = ? AND domain_id = ?
                   AND is_primary = 1
                """,
                (assignment_version, definition.domain_id),
            )
            raw_mean = corpus.scalar_float(
                """
                SELECT AVG(confidence) FROM domain_assignments
                 WHERE target_type = 'RECORD' AND assignment_version = ?
                   AND domain_id = ?
                """,
                (assignment_version, definition.domain_id),
            )
            mean_confidence = (
                round(raw_mean, 3) if raw_mean is not None else None
            )
            buckets = {name: 0 for name, _, _ in CONFIDENCE_BUCKETS}
            for name, low, high in CONFIDENCE_BUCKETS:
                buckets[name] = corpus.scalar(
                    """
                    SELECT COUNT(*) FROM domain_assignments
                     WHERE target_type = 'RECORD' AND assignment_version = ?
                       AND domain_id = ? AND confidence >= ? AND confidence < ?
                    """,
                    (assignment_version, definition.domain_id, low, high),
                )
            domains.append(
                DomainCoverage(
                    domain_id=definition.domain_id,
                    name=definition.name,
                    domain_class=definition.domain_class,
                    taxonomy_status=definition.status,
                    record_assignments=record_assignments,
                    unit_assignments=unit_assignments,
                    primary_unit_assignments=primary_units,
                    record_coverage_pct=_pct(record_assignments, eligible_speech),
                    unit_coverage_pct=_pct(unit_assignments, units_total),
                    mean_confidence=mean_confidence,
                    confidence_buckets=buckets,
                )
            )

        cooccurrence: list[CooccurrenceCell] = []
        for row in corpus.rows(
            """
            SELECT a.domain_id AS a, b.domain_id AS b, COUNT(*) AS total
              FROM domain_assignments AS a
              JOIN domain_assignments AS b
                ON a.target_id = b.target_id
               AND a.target_type = 'TRANSCRIPT_UNIT'
               AND b.target_type = 'TRANSCRIPT_UNIT'
               AND a.assignment_version = b.assignment_version
               AND a.domain_id < b.domain_id
             WHERE a.assignment_version = ?
             GROUP BY a.domain_id, b.domain_id
             ORDER BY total DESC, a.domain_id, b.domain_id
            """,
            (assignment_version,),
        ):
            cooccurrence.append(
                CooccurrenceCell(
                    domain_a=row["a"],
                    domain_b=row["b"],
                    unit_count=int(row["total"]),
                )
            )

        review_mix = dict(
            corpus.count_by("domain_assignments", "review_status")
        )
        ambiguous_records = corpus.scalar(
            """
            SELECT COUNT(*) FROM (
                SELECT target_id
                  FROM domain_assignments
                 WHERE target_type = 'RECORD' AND assignment_version = ?
                 GROUP BY target_id
                HAVING COUNT(*) > 1 AND (MAX(confidence) - MIN(confidence)) < 0.10
            )
            """,
            (assignment_version,),
        )

    return DomainCoverageReport(
        generated_at=_utc_now(),
        database_path=str(path),
        taxonomy_version=TAXONOMY_VERSION,
        assignment_version=assignment_version,
        label_method="RULE_CANDIDATE",
        review_status_mix=review_mix,
        records_total=records_total,
        speech_records_total=speech_records,
        eligible_speech_records=eligible_speech,
        assigned_records=assigned_records,
        unclassified_eligible_records=max(eligible_speech - assigned_records, 0),
        multi_label_records=multi_label_records,
        ambiguous_records=ambiguous_records,
        units_total=units_total,
        units_assigned=units_assigned,
        unclassified_units=max(units_total - units_assigned, 0),
        multi_domain_units=multi_domain_units,
        payload_type_coverage={
            "SPEECH": {
                "total": speech_records,
                "eligible": eligible_speech,
                "assigned": assigned_records,
            },
            "EVENT": {
                "total": records_total - speech_records,
                "eligible": 0,
                "assigned": 0,
            },
        },
        domains=domains,
        cooccurrence=cooccurrence,
        notes=[
            "All labels are RULE_CANDIDATE candidates, not ground truth.",
            "Coverage measures lexical evidence, not business-intent prevalence.",
            (
                "Event records carry no text and are never classified by lexical "
                "rules; this is a method limitation, not an absence of business "
                "content."
            ),
            (
                "Speaker roles are UNKNOWN for the whole corpus; no result here "
                "depends on speaker attribution."
            ),
        ],
    )


def _assignment_version(corpus: CorpusDatabase) -> str:
    rows = corpus.rows(
        """
        SELECT assignment_version FROM domain_assignments
         ORDER BY assignment_id DESC LIMIT 1
        """
    )
    if not rows:
        return "none"
    return str(rows[0]["assignment_version"])


def _pct(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * value / total, 2)


def render_markdown(report: DomainCoverageReport) -> str:
    """Render the aggregate report as Markdown with no transcript text."""
    lines: list[str] = [
        "# Domain Coverage Report",
        "",
        f"Generated: {report.generated_at}",
        f"Database: `{report.database_path}`",
        f"Assignment version: {report.assignment_version}",
        f"Label method: {report.label_method}",
        "",
        "> All labels are rule-based candidates. Coverage measures lexical",
        "> evidence, not business-intent prevalence, and nothing here is",
        "> human-reviewed ground truth.",
        "",
        "## Corpus totals",
        "",
        "| Item | Count |",
        "|---|---:|",
        f"| Records | {report.records_total} |",
        f"| Speech records | {report.speech_records_total} |",
        f"| Eligible speech records (text ≥ 4 chars) |"
        f" {report.eligible_speech_records} |",
        f"| Records with at least one domain label | {report.assigned_records} |",
        f"| Eligible records left unclassified |"
        f" {report.unclassified_eligible_records} |",
        f"| Multi-label records | {report.multi_label_records} |",
        f"| Ambiguous records | {report.ambiguous_records} |",
        f"| Transcript units | {report.units_total} |",
        f"| Units with at least one domain label | {report.units_assigned} |",
        f"| Units left unclassified | {report.unclassified_units} |",
        f"| Units with two or more domains | {report.multi_domain_units} |",
        "",
        "## Per-domain coverage",
        "",
        "| Domain | Class | Status | Record labels | Record % of eligible |"
        " Unit labels | Unit % | Primary units | Mean confidence |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for domain in report.domains:
        lines.append(
            f"| {domain.domain_id} {domain.name} | {domain.domain_class} |"
            f" {domain.taxonomy_status} | {domain.record_assignments} |"
            f" {domain.record_coverage_pct} | {domain.unit_assignments} |"
            f" {domain.unit_coverage_pct} | {domain.primary_unit_assignments} |"
            f" {domain.mean_confidence if domain.mean_confidence is not None else '-'} |"
        )

    lines.extend(["", "## Confidence distribution (record labels)", ""])
    bucket_names = [name for name, _, _ in CONFIDENCE_BUCKETS]
    lines.append("| Domain | " + " | ".join(bucket_names) + " |")
    lines.append("|---|" + "---:|" * len(bucket_names))
    for domain in report.domains:
        cells = " | ".join(str(domain.confidence_buckets.get(name, 0)) for name in bucket_names)
        lines.append(f"| {domain.domain_id} | {cells} |")

    lines.extend(["", "## Domain co-occurrence (transcript units)", ""])
    if report.cooccurrence:
        lines.append("| Domain A | Domain B | Units |")
        lines.append("|---|---|---:|")
        for cell in report.cooccurrence:
            lines.append(
                f"| {cell.domain_a} | {cell.domain_b} | {cell.unit_count} |"
            )
    else:
        lines.append("No co-occurring domain pairs were observed.")

    lines.extend(["", "## Review status mix", ""])
    if report.review_status_mix:
        for status, count in sorted(report.review_status_mix.items()):
            lines.append(f"- {status}: {count}")
    else:
        lines.append("- no assignments recorded")

    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in report.notes)
    lines.append("")
    lines.append(f"Unclassified marker: `{UNCLASSIFIED}`")
    return "\n".join(lines) + "\n"


def write_report(
    report: DomainCoverageReport,
    *,
    markdown_path: Path | None = None,
    json_path: Path | None = None,
) -> None:
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
