"""Local-only human review workflow for candidate domain labels.

Nothing in this module transmits data anywhere. Review samples are written to a
local file chosen by the reviewer and are meant to be opened only inside the
authorised local environment.

Privacy rules enforced here:

* the exported sample contains transcript text **only** when the caller passes
  ``include_text=True`` and acknowledges the local-only nature explicitly;
* exported files are created with owner-only permissions (``0o600``) where the
  platform supports it;
* review notes are stored as free text and must not be pasted into external
  tools;
* the review file records only opaque ``target_id`` values, candidate domains and
  the reviewer's decision.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from cm_outbound_bench.corpus.classification import ASSIGNMENT_VERSION
from cm_outbound_bench.corpus.database import CorpusDatabase
from cm_outbound_bench.corpus.paths import resolve_database_path
from cm_outbound_bench.corpus.taxonomy import DOMAIN_IDS

REVIEW_SCHEMA_VERSION = "1.0"
DEFAULT_SEED = 20261120

SAMPLE_STRATA: tuple[str, ...] = (
    "D01",
    "D02",
    "D03",
    "D04",
    "D05",
    "D06",
    "MULTI_LABEL",
    "AMBIGUOUS",
    "UNCLASSIFIED",
)


class ReviewSampleItem(BaseModel):
    sample_id: str
    stratum: str
    target_type: str
    target_id: str
    candidate_domains: list[str] = Field(default_factory=list)
    candidate_confidences: list[float] = Field(default_factory=list)
    is_ambiguous: bool = False
    text_masked: str | None = None
    reviewer_decision: str = "PENDING"
    reviewer_domains: list[str] = Field(default_factory=list)
    reviewer_notes: str = ""


class ReviewSample(BaseModel):
    schema_version: str = REVIEW_SCHEMA_VERSION
    generated_at: str
    database_path: str
    assignment_version: str
    seed: int
    per_stratum: int
    strata: list[str]
    include_text: bool
    items: list[ReviewSampleItem]
    instructions: list[str] = Field(default_factory=list)


class ReviewApplicationReport(BaseModel):
    review_path: str
    reviewer: str
    reviewed_at: str
    items_read: int = 0
    approved: int = 0
    rejected: int = 0
    changed: int = 0
    adjudicated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def export_review_sample(
    *,
    output_path: str | Path,
    database_path: str | Path | None = None,
    per_stratum: int = 10,
    seed: int = DEFAULT_SEED,
    include_text: bool = False,
) -> ReviewSample:
    """Export a stratified, reproducible review sample."""
    path = resolve_database_path(database_path)
    rng = random.Random(seed)
    with CorpusDatabase.open_read_only(path) as corpus:
        rows = corpus.rows(
            """
            SELECT target_id, domain_id, confidence, is_primary
              FROM domain_assignments
             WHERE target_type = 'RECORD' AND assignment_version = ?
             ORDER BY target_id
            """,
            (ASSIGNMENT_VERSION,),
        )

    grouped: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        grouped.setdefault(row["target_id"], []).append(
            (row["domain_id"], float(row["confidence"]))
        )

    reference_ids = _all_eligible_ids(path)
    unclassified = sorted(set(reference_ids) - set(grouped))

    strata_pools: dict[str, list[str]] = {name: [] for name in SAMPLE_STRATA}
    for target_id, domains in grouped.items():
        domain_ids = [item[0] for item in domains]
        if len(domain_ids) > 1:
            strata_pools["MULTI_LABEL"].append(target_id)
            confidences = sorted(
                (item[1] for item in domains), reverse=True
            )
            if len(confidences) > 1 and confidences[0] - confidences[1] < 0.10:
                strata_pools["AMBIGUOUS"].append(target_id)
        for domain_id in domain_ids:
            if domain_id in DOMAIN_IDS:
                strata_pools[domain_id].append(target_id)
    strata_pools["UNCLASSIFIED"] = unclassified

    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for stratum in SAMPLE_STRATA:
        pool = sorted(set(strata_pools.get(stratum, [])))
        rng.shuffle(pool)
        taken = 0
        for target_id in pool:
            if taken >= per_stratum:
                break
            if target_id in seen:
                continue
            seen.add(target_id)
            selected.append((stratum, target_id))
            taken += 1

    texts = _load_texts(path, [target_id for _, target_id in selected]) if include_text else {}

    items: list[ReviewSampleItem] = []
    for index, (stratum, target_id) in enumerate(selected, start=1):
        domains = grouped.get(target_id, [])
        confidences = [round(value, 3) for _, value in domains]
        is_ambiguous = bool(
            len(confidences) > 1 and confidences[0] - confidences[1] < 0.10
        )
        items.append(
            ReviewSampleItem(
                sample_id=f"S{index:04d}",
                stratum=stratum,
                target_type="RECORD",
                target_id=target_id,
                candidate_domains=[domain_id for domain_id, _ in domains],
                candidate_confidences=confidences,
                is_ambiguous=is_ambiguous,
                text_masked=texts.get(target_id),
            )
        )

    sample = ReviewSample(
        generated_at=_utc_now(),
        database_path=str(path),
        assignment_version=ASSIGNMENT_VERSION,
        seed=seed,
        per_stratum=per_stratum,
        strata=list(SAMPLE_STRATA),
        include_text=include_text,
        items=items,
        instructions=[
            "LOCAL ONLY. Do not copy transcript text into external tools or chat.",
            (
                "Set reviewer_decision to APPROVE, REJECT, CHANGE or UNCERTAIN "
                "for every item you review."
            ),
            (
                "APPROVE keeps the candidate domains. REJECT marks the candidate "
                "labels rejected. CHANGE records reviewer_domains as the "
                "adjudicated labels."
            ),
            (
                "Reviewer domains must be drawn from: "
                + ", ".join(DOMAIN_IDS)
                + ", or UNCLASSIFIED."
            ),
            (
                "Candidates are rule-based. Approving a candidate confirms it fits "
                "the domain definition; it does not establish business prevalence."
            ),
        ],
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_private(destination, json.dumps(sample.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return sample


def _all_eligible_ids(path: Path) -> list[str]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT transcript_id, seq FROM transcript_records
             WHERE payload_type = 'SPEECH'
               AND text_masked IS NOT NULL
               AND length(trim(text_masked)) >= 4
            """
        ).fetchall()
    finally:
        connection.close()
    return [f"{row[0]}:{row[1]}" for row in rows]


def _load_texts(path: Path, target_ids: list[str]) -> dict[str, str]:
    if not target_ids:
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    texts: dict[str, str] = {}
    try:
        for target_id in target_ids:
            transcript_id, _, seq = target_id.partition(":")
            row = connection.execute(
                """
                SELECT text_masked FROM transcript_records
                 WHERE transcript_id = ? AND seq = ?
                """,
                (transcript_id, int(seq)),
            ).fetchone()
            if row is not None and row[0]:
                texts[target_id] = row[0]
    finally:
        connection.close()
    return texts


def _write_private(path: Path, payload: str) -> None:
    path.write_text(payload + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        return


def apply_review(
    *,
    review_path: str | Path,
    reviewer: str,
    database_path: str | Path | None = None,
) -> ReviewApplicationReport:
    """Apply a completed review file to the assignment review status."""
    if not reviewer.strip():
        raise ValueError("reviewer must be a non-empty identifier")
    path = resolve_database_path(database_path)
    sample = ReviewSample.model_validate_json(
        Path(review_path).read_text(encoding="utf-8")
    )

    report = ReviewApplicationReport(
        review_path=str(review_path),
        reviewer=reviewer,
        reviewed_at=_utc_now(),
        items_read=len(sample.items),
    )
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for item in sample.items:
            decision = item.reviewer_decision.upper()
            if decision == "PENDING":
                report.skipped += 1
                continue
            if decision not in {"APPROVE", "REJECT", "CHANGE", "UNCERTAIN"}:
                report.errors.append(f"{item.sample_id}: unknown decision")
                report.skipped += 1
                continue
            transcript_id, _, seq = item.target_id.partition(":")
            record = connection.execute(
                """
                SELECT seq FROM transcript_records
                 WHERE transcript_id = ? AND seq = ?
                """,
                (transcript_id, int(seq)),
            ).fetchone()
            if record is None:
                report.errors.append(f"{item.sample_id}: unknown record")
                report.skipped += 1
                continue

            if decision in {"APPROVE", "UNCERTAIN"}:
                connection.execute(
                    """
                    UPDATE domain_assignments
                       SET review_status = 'HUMAN_REVIEWED',
                           label_method = 'HUMAN_REVIEWED',
                           reviewer_or_model = ?
                     WHERE target_type = 'RECORD' AND target_id = ?
                       AND assignment_version = ?
                    """,
                    (reviewer, item.target_id, sample.assignment_version),
                )
                report.approved += 1
            elif decision == "REJECT":
                connection.execute(
                    """
                    UPDATE domain_assignments
                       SET review_status = 'REJECTED',
                           reviewer_or_model = ?
                     WHERE target_type = 'RECORD' AND target_id = ?
                       AND assignment_version = ?
                    """,
                    (reviewer, item.target_id, sample.assignment_version),
                )
                report.rejected += 1
            else:
                reviewer_domains = [
                    domain for domain in item.reviewer_domains if domain in DOMAIN_IDS
                ]
                if not reviewer_domains:
                    report.errors.append(
                        f"{item.sample_id}: CHANGE requires reviewer_domains"
                    )
                    report.skipped += 1
                    continue
                connection.execute(
                    """
                    DELETE FROM domain_assignments
                     WHERE target_type = 'RECORD' AND target_id = ?
                       AND assignment_version = ?
                    """,
                    (item.target_id, sample.assignment_version),
                )
                for domain_id in reviewer_domains:
                    connection.execute(
                        """
                        INSERT INTO domain_assignments (
                            target_type, target_id, domain_id, label_method,
                            confidence, review_status, is_primary,
                            assignment_version, reviewer_or_model, created_at
                        ) VALUES ('RECORD', ?, ?, 'ADJUDICATED', 1.0,
                                  'ADJUDICATED', ?, ?, ?, ?)
                        """,
                        (
                            item.target_id,
                            domain_id,
                            1 if domain_id == reviewer_domains[0] else 0,
                            sample.assignment_version,
                            reviewer,
                            _utc_now(),
                        ),
                    )
                report.changed += 1
                report.adjudicated += 1
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return report
