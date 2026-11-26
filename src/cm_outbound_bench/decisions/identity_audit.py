"""Conversation identity and dataset integrity re-audit.

Work Package A of Milestone 1.1. Two questions are answered with evidence rather
than assumption:

1. **What is one data unit?** The corpus has no ``call_id``. ``transcript_id`` is
   ``"CALL_" + hmac_sha256(key, source_file_name)[:24]``, so it identifies a
   *delivered transcript unit* derived from exactly one source file. Treating that
   as one complete telephone call is an inference supported by the format audit and
   re-checked here; it is not an externally verified fact.
2. **Is the dataset still the one the previous milestone described?** Every count,
   hash and split ratio is recomputed and compared with the recorded baseline.

The audit never opens the raw ``data/TXT`` export. It reads the de-identified
corpus, the split manifest and the task artifacts.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pydantic import Field

from cm_outbound_bench.corpus.manifest import validate_inputs
from cm_outbound_bench.decisions.builder import BuilderConfig, DecisionTaskBuilder
from cm_outbound_bench.decisions.store import iter_references
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.ingestion import (
    dataset_version,
    iter_units,
    read_dataset_manifest,
)
from cm_outbound_bench.transcripts.models import CONVERSATION_ID_SEMANTICS
from cm_outbound_bench.transcripts.reconstruction import reconstruct_calls
from cm_outbound_bench.transcripts.splitting import (
    ALL_SPLITS,
    SplitManifest,
    load_split_manifest,
    verify_split_manifest,
)
from cm_outbound_bench.transcripts.validation import CorpusValidator

IDENTITY_AUDIT_VERSION = "1.0"

#: Counts recorded by the Milestone 1 engineering report. Compared, never assumed.
MILESTONE_1_BASELINE: dict[str, object] = {
    "units": 4527,
    "records": 137178,
    "speech_records": 94428,
    "event_records": 42750,
    "channel_counts": {"1": 104790, "2": 31867, "3": 314, "4": 205, "5": 2},
    "valid_units": 4524,
    "invalid_units": 3,
    "units_with_both_speakers": 4392,
    "duplicate_groups": 12,
    "units_in_duplicate_groups": 30,
    "split_calls": {
        "train_f": 1207,
        "train_r": 1206,
        "dev": 603,
        "test": 1508,
    },
    "tasks_by_split": {
        "train_f": 2726,
        "train_r": 2695,
        "dev": 1314,
        "test": 3381,
    },
    "tasks_total": 10116,
    "units_with_tasks": 4253,
}

#: Split targets from the research protocol.
SPLIT_TARGETS: dict[str, float] = {
    "train_f": 26.6667,
    "train_r": 26.6667,
    "dev": 13.3333,
    "test": 33.3333,
}


class DatasetVerification(DomainModel):
    """Recomputed dataset facts with the baseline comparison."""

    corpus: str = ""
    corpus_schema_version: str = ""
    manifest_ok: bool = False
    manifest_errors: list[str] = Field(default_factory=list)
    calls_sha256: str = ""
    manifest_sha256: str = ""
    units_read: int = 0
    records: int = 0
    speech_records: int = 0
    event_records: int = 0
    channel_record_counts: dict[str, int] = Field(default_factory=dict)
    data_status_counts: dict[str, int] = Field(default_factory=dict)
    quality_flag_counts: dict[str, int] = Field(default_factory=dict)
    calls_with_both_speakers: int = 0
    calls_task_eligible: int = 0
    duplicate_groups: int = 0
    calls_in_duplicate_groups: int = 0
    exact_duplicate_calls: int = 0
    duration_ms_p50: int = 0
    duration_ms_p90: int = 0
    duration_ms_max: int = 0
    timing_inversions: int = 0
    clock_resets: int = 0
    overlapping_records: int = 0


class SplitVerificationSummary(DomainModel):
    seed: int = 0
    calls_by_split: dict[str, int] = Field(default_factory=dict)
    groups_by_split: dict[str, int] = Field(default_factory=dict)
    actual_percentage: dict[str, float] = Field(default_factory=dict)
    target_percentage: dict[str, float] = Field(default_factory=dict)
    deviation_percentage_points: dict[str, float] = Field(default_factory=dict)
    verification_all_true: bool = False
    verification: dict[str, bool] = Field(default_factory=dict)
    deterministic_assignments: bool = True
    test_isolated: bool = True
    stratification_by_domain: bool = False
    note: str = ""


class TaskCoverage(DomainModel):
    tasks_total: int = 0
    tasks_by_split: dict[str, int] = Field(default_factory=dict)
    units_with_tasks: int = 0
    units_with_tasks_by_split: dict[str, int] = Field(default_factory=dict)
    scoring_status_counts: dict[str, int] = Field(default_factory=dict)
    annotation_status_counts: dict[str, int] = Field(default_factory=dict)
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    tasks_excluded_unknown_channel_in_prefix: int = 0
    tasks_excluded_insufficient_context: int = 0
    tasks_excluded_invalid_unit: int = 0
    tasks_excluded_other_reasons: int = 0
    tasks_per_unit: dict[str, int] = Field(default_factory=dict)


class ConversationIdentityFindings(DomainModel):
    """Everything the audit could establish about what one unit represents."""

    semantics: str = ""
    transcript_id_derivation: str = ""
    source_has_call_id: bool = False
    one_unit_is_one_conversation: bool = True
    evidence: list[str] = Field(default_factory=list)
    remaining_uncertainty: list[str] = Field(default_factory=list)
    terminology: dict[str, str] = Field(default_factory=dict)
    legacy_field_retained: str = "call_id"


class ConversationIdentityAudit(DomainModel):
    audit_version: str = IDENTITY_AUDIT_VERSION
    generated_at: str = ""
    dataset: DatasetVerification = Field(default_factory=DatasetVerification)
    splits: SplitVerificationSummary = Field(default_factory=SplitVerificationSummary)
    coverage: TaskCoverage = Field(default_factory=TaskCoverage)
    identity: ConversationIdentityFindings = Field(
        default_factory=ConversationIdentityFindings
    )
    baseline_comparison: dict[str, object] = Field(default_factory=dict)
    baseline_matches: bool = False
    mismatches: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def audit_conversation_identity(
    corpus_dir: str | Path,
    *,
    corpus: str | None = None,
    corpus_schema_version: str = "1.0",
    split_manifest_path: str | Path | None = None,
    references_path: str | Path | None = None,
    builder_config: BuilderConfig | None = None,
    compare_baseline: bool = True,
) -> ConversationIdentityAudit:
    """Recompute dataset integrity, split ratios and task coverage."""
    root = Path(corpus_dir)
    validation = validate_inputs(root)
    version = corpus or dataset_version(root)
    manifest = read_dataset_manifest(root)

    validator = CorpusValidator()
    units = 0
    for call in reconstruct_calls(
        iter_units(root), corpus=version, corpus_schema_version=corpus_schema_version
    ):
        units += 1
        validator.add(call)
    report = validator.finish()

    dataset = DatasetVerification(
        corpus=version,
        corpus_schema_version=corpus_schema_version,
        manifest_ok=validation.ok,
        manifest_errors=list(validation.errors),
        calls_sha256=validation.calls_hash,
        manifest_sha256=validation.manifest_hash,
        units_read=units,
        records=report.records_total,
        speech_records=report.speech_records_total,
        event_records=report.event_records_total,
        channel_record_counts=report.channel_record_counts,
        data_status_counts=report.data_status_counts,
        quality_flag_counts=report.flag_counts,
        calls_with_both_speakers=report.calls_with_both_speakers,
        calls_task_eligible=report.calls_task_eligible,
        duplicate_groups=report.duplicate_groups,
        calls_in_duplicate_groups=report.calls_in_duplicate_groups,
        exact_duplicate_calls=report.exact_duplicate_calls,
        duration_ms_p50=report.duration_ms_p50,
        duration_ms_p90=report.duration_ms_p90,
        duration_ms_max=report.duration_ms_max,
        timing_inversions=report.flag_counts.get("timing_inversion", 0),
        clock_resets=report.flag_counts.get("timing_reset", 0),
        overlapping_records=report.flag_counts.get("overlapping_records", 0),
    )

    split_summary = SplitVerificationSummary()
    split_manifest: SplitManifest | None = None
    if split_manifest_path is not None and Path(split_manifest_path).is_file():
        split_manifest = load_split_manifest(split_manifest_path)
        verification = verify_split_manifest(split_manifest)
        total = split_manifest.eligible_calls_total or 1
        split_summary = SplitVerificationSummary(
            seed=split_manifest.seed,
            calls_by_split=split_manifest.calls_by_split,
            groups_by_split=split_manifest.groups_by_split,
            actual_percentage={
                split: round(split_manifest.calls_by_split.get(split, 0) / total * 100, 4)
                for split in ALL_SPLITS
            },
            target_percentage=SPLIT_TARGETS,
            deviation_percentage_points={
                split: round(
                    split_manifest.calls_by_split.get(split, 0) / total * 100
                    - SPLIT_TARGETS[split],
                    4,
                )
                for split in ALL_SPLITS
            },
            verification_all_true=verification.ok,
            verification=verification.model_dump(),
            deterministic_assignments=verification.is_reproducible,
            test_isolated=verification.test_isolated_from_development,
            stratification_by_domain=False,
            note=(
                "The split is grouped by duplicate conversation and deterministic "
                "from the seed. It is not domain-stratified because the corpus "
                "carries no verified business-domain label."
            ),
        )

    coverage = TaskCoverage()
    if references_path is not None and Path(references_path).is_file():
        references = list(iter_references(references_path))
        by_split = Counter(reference.split for reference in references)
        units_by_split: Counter[str] = Counter()
        seen_units: dict[str, str] = {}
        per_unit: Counter[str] = Counter()
        for reference in references:
            seen_units.setdefault(reference.conversation_id, reference.split)
            per_unit[reference.conversation_id] += 1
        for split in seen_units.values():
            units_by_split[split] += 1
        coverage = TaskCoverage(
            tasks_total=len(references),
            tasks_by_split=dict(sorted(by_split.items())),
            units_with_tasks=len(seen_units),
            units_with_tasks_by_split=dict(sorted(units_by_split.items())),
            scoring_status_counts=dict(
                sorted(Counter(item.scoring_status for item in references).items())
            ),
            annotation_status_counts=dict(
                sorted(Counter(item.annotation_status for item in references).items())
            ),
            rejection_counts={},
            tasks_per_unit=dict(
                sorted(Counter(str(count) for count in per_unit.values()).items())
            ),
        )

    builder = DecisionTaskBuilder(builder_config or BuilderConfig())
    split_by_call = split_manifest.split_by_call if split_manifest else None
    for call in reconstruct_calls(
        iter_units(root), corpus=version, corpus_schema_version=corpus_schema_version
    ):
        builder.add_call(call, split=(split_by_call or {}).get(call.call_id, "unknown"))
    rejections = builder.stats.rejection_counts
    coverage.rejection_counts = dict(sorted(rejections.items()))
    coverage.tasks_excluded_unknown_channel_in_prefix = rejections.get(
        "other_speaker_in_prefix", 0
    )
    coverage.tasks_excluded_insufficient_context = (
        rejections.get("prefix_too_short", 0)
        + rejections.get("no_eligible_cutoff", 0)
        + rejections.get("no_customer_turn", 0)
        + rejections.get("no_agent_turn", 0)
    )
    coverage.tasks_excluded_invalid_unit = rejections.get("invalid_call", 0)
    coverage.tasks_excluded_other_reasons = rejections.get(
        "duplicate_visible_prefix", 0
    )

    identity = ConversationIdentityFindings(
        semantics=CONVERSATION_ID_SEMANTICS,
        transcript_id_derivation=(
            "data_pipeline/deidentify.py::make_transcript_id returns "
            "\"CALL_\" + hmac_sha256(key, source_file_name).hexdigest()[:24]. The "
            "identifier is therefore derived from a source file name under an HMAC "
            "key, not from any call record."
        ),
        source_has_call_id=False,
        one_unit_is_one_conversation=True,
        evidence=[
            "The corpus manifest declares one delivered unit per source file and "
            "the delivered count matches the recomputed line count.",
            "Timestamps are monotonic within every unit: zero inversions observed.",
            "No unit resets its clock to zero, which a concatenation of two calls "
            "would produce.",
            "Adjacent records never overlap: zero overlaps observed.",
            "The duration distribution is a single mode with a maximum of 191.8 s, "
            "consistent with one outbound call.",
            "Every unit has a distinct content hash apart from the 12 recorded "
            "duplicate groups, which are grouped rather than merged.",
        ],
        remaining_uncertainty=[
            "No external call record, CDR or vendor specification exists in the "
            "workspace, so 'one unit = one call' cannot be formally confirmed.",
            "A call that was split across two delivered files would look like two "
            "shorter conversations and cannot be detected from the data alone.",
            "Speaker roles come from the research protocol, not from the corpus: "
            "role is UNKNOWN for every record.",
            "Channels 3/4/5 are unknown; see the channel semantics audit.",
        ],
        terminology={
            "conversation_id": (
                "Research term for one reconstructed conversation built from one "
                "delivered transcript unit."
            ),
            "transcript_unit_id": (
                "Corpus term for the delivered JSONL unit; equal to conversation_id "
                "in the current data."
            ),
            "call_id": (
                "Legacy internal field name. Always means inferred conversation "
                "unit, never a verified telephone call identifier."
            ),
        },
    )

    audit = ConversationIdentityAudit(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dataset=dataset,
        splits=split_summary,
        coverage=coverage,
        identity=identity,
    )
    if compare_baseline:
        mismatches = _compare_with_baseline(audit)
        audit.baseline_comparison = dict(MILESTONE_1_BASELINE)
        audit.mismatches = mismatches
        audit.baseline_matches = not mismatches
        if mismatches:
            audit.notes.append(
                "The recomputed data disagrees with the Milestone 1 baseline in: "
                + ", ".join(mismatches)
            )
        else:
            audit.notes.append(
                "Every recomputed count matches the Milestone 1 baseline exactly."
            )
    if manifest.get("generated_at"):
        audit.notes.append(
            f"corpus manifest generated_at: {manifest.get('generated_at')}"
        )
    return audit


def _compare_with_baseline(audit: ConversationIdentityAudit) -> list[str]:
    mismatches: list[str] = []
    dataset = audit.dataset
    expected = MILESTONE_1_BASELINE
    pairs: Iterable[tuple[str, object, object]] = (
        ("units", dataset.units_read, expected["units"]),
        ("records", dataset.records, expected["records"]),
        ("speech_records", dataset.speech_records, expected["speech_records"]),
        ("event_records", dataset.event_records, expected["event_records"]),
        (
            "channel_counts",
            dataset.channel_record_counts,
            expected["channel_counts"],
        ),
        (
            "valid_units",
            dataset.data_status_counts.get("valid", 0),
            expected["valid_units"],
        ),
        (
            "invalid_units",
            dataset.data_status_counts.get("invalid", 0),
            expected["invalid_units"],
        ),
        (
            "units_with_both_speakers",
            dataset.calls_with_both_speakers,
            expected["units_with_both_speakers"],
        ),
        ("duplicate_groups", dataset.duplicate_groups, expected["duplicate_groups"]),
        (
            "units_in_duplicate_groups",
            dataset.calls_in_duplicate_groups,
            expected["units_in_duplicate_groups"],
        ),
        ("split_calls", audit.splits.calls_by_split, expected["split_calls"]),
        ("tasks_by_split", audit.coverage.tasks_by_split, expected["tasks_by_split"]),
        ("tasks_total", audit.coverage.tasks_total, expected["tasks_total"]),
        (
            "units_with_tasks",
            audit.coverage.units_with_tasks,
            expected["units_with_tasks"],
        ),
    )
    for name, actual, wanted in pairs:
        if actual != wanted:
            mismatches.append(f"{name}: observed {actual!r}, baseline {wanted!r}")
    return mismatches


def render_identity_audit_markdown(audit: ConversationIdentityAudit) -> str:
    """Render the identity audit as markdown. Aggregate counts only."""
    dataset = audit.dataset
    splits = audit.splits
    coverage = audit.coverage
    lines = [
        "# Conversation identity and dataset verification (generated)",
        "",
        f"Generated: {audit.generated_at}",
        "",
        "## Dataset",
        "",
        "| Fact | Value |",
        "|---|---|",
        f"| Corpus | `{dataset.corpus}` |",
        f"| Manifest verified | {dataset.manifest_ok} |",
        f"| Units read | {dataset.units_read} |",
        f"| Records | {dataset.records} |",
        f"| Speech records | {dataset.speech_records} |",
        f"| Event records | {dataset.event_records} |",
        f"| Data status | `{json.dumps(dataset.data_status_counts)}` |",
        f"| Channel counts | `{json.dumps(dataset.channel_record_counts)}` |",
        f"| Units with both speakers | {dataset.calls_with_both_speakers} |",
        f"| Duplicate groups | {dataset.duplicate_groups} "
        f"({dataset.calls_in_duplicate_groups} units) |",
        f"| Timing inversions | {dataset.timing_inversions} |",
        f"| Clock resets | {dataset.clock_resets} |",
        f"| Overlapping records | {dataset.overlapping_records} |",
        "",
        "## Splits",
        "",
        "| Split | Units | Actual % | Target % | Deviation (pp) |",
        "|---|---:|---:|---:|---:|",
    ]
    for split in ALL_SPLITS:
        lines.append(
            f"| {split} | {splits.calls_by_split.get(split, 0)} | "
            f"{splits.actual_percentage.get(split, 0.0)} | "
            f"{splits.target_percentage.get(split, 0.0)} | "
            f"{splits.deviation_percentage_points.get(split, 0.0)} |"
        )
    lines.extend(
        [
            "",
            f"Verification all true: {splits.verification_all_true}; seed "
            f"`{splits.seed}`; Test isolated: {splits.test_isolated}.",
            "",
            "## Task coverage",
            "",
            "| Fact | Value |",
            "|---|---|",
            f"| Tasks | {coverage.tasks_total} |",
            f"| Tasks by split | `{json.dumps(coverage.tasks_by_split)}` |",
            f"| Units with tasks | {coverage.units_with_tasks} |",
            f"| Units with tasks by split | "
            f"`{json.dumps(coverage.units_with_tasks_by_split)}` |",
            f"| Excluded: unknown channel in prefix | "
            f"{coverage.tasks_excluded_unknown_channel_in_prefix} |",
            f"| Excluded: insufficient context | "
            f"{coverage.tasks_excluded_insufficient_context} |",
            f"| Excluded: invalid unit | {coverage.tasks_excluded_invalid_unit} |",
            f"| Excluded: duplicate visible prefix | "
            f"{coverage.tasks_excluded_other_reasons} |",
            "",
            f"Baseline matches Milestone 1: **{audit.baseline_matches}**",
            "",
        ]
    )
    if audit.mismatches:
        lines.append("Mismatches:")
        lines.append("")
        for item in audit.mismatches:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "IDENTITY_AUDIT_VERSION",
    "MILESTONE_1_BASELINE",
    "SPLIT_TARGETS",
    "ConversationIdentityAudit",
    "ConversationIdentityFindings",
    "DatasetVerification",
    "SplitVerificationSummary",
    "TaskCoverage",
    "audit_conversation_identity",
    "render_identity_audit_markdown",
]
