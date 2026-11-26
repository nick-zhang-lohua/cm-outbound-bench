"""Annotation packet export, import validation, adjudication and apply.

This module is the practical annotation infrastructure for Milestone 1.1. It keeps
three stages physically separate:

1. **task annotation** — a packet per task, containing only the agent-visible prefix
   and approved reference material, plus blank fields;
2. **adjudication** — independent submissions are compared, disagreements go to a
   designated adjudicator, and only a human-adjudicated record becomes eligible;
3. **agent-output evaluation** — a separate blind packet built *after* the run, so a
   reviewer never sees the model identity or the experimental condition while
   rating the output.

Guarantees enforced here, not merely documented:

* a packet never contains a historical continuation, a hidden segment id, a call id
  or a scoring label;
* a submission is rejected when it duplicates another submission from the same
  annotator, when it was written against a different guideline version without
  acknowledgement, or when its structure is internally inconsistent;
* a model-only annotation can never be applied as ground truth: the applier
  requires a human authority, and the reference model independently refuses to
  score an ``llm_proposed`` annotation.

Everything written here contains de-identified conversation text, so the default
output location is inside the git-ignored ``data/decision/`` tree.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from pydantic import Field

from cm_outbound_bench.decisions.annotation import (
    ADJUDICATION_NOT_REQUIRED,
    ADJUDICATION_PENDING,
    ADJUDICATION_RESOLVED,
    ANNOTATION_ADJUDICATED,
    ANNOTATION_GUIDELINE_VERSION,
    ANNOTATION_REVIEWED,
    AUTHORITY_HUMAN_ADJUDICATED,
    AUTHORITY_HUMAN_PRIMARY,
    AdjudicationRecord,
    AnnotationError,
    AnnotationSubmission,
    BusinessRuleCitation,
    tier_for,
    validate_annotation_consistency,
)
from cm_outbound_bench.decisions.sampling import SamplingPlan
from cm_outbound_bench.decisions.schema import (
    PrivateTaskReference,
    VisibleTurn,
)
from cm_outbound_bench.decisions.store import TaskStore
from cm_outbound_bench.domain.data_model import DomainModel

ANNOTATION_PACKET_VERSION = "1.0"

PACKETS_FILENAME = "annotation_packets.jsonl"
PACKETS_MANIFEST_FILENAME = "annotation_packets.manifest.json"
SUBMISSIONS_FILENAME = "annotation_submissions.jsonl"
IMPORT_REPORT_FILENAME = "annotation_import_report.json"
ADJUDICATIONS_FILENAME = "adjudications.jsonl"
ADJUDICATION_WORKLIST_FILENAME = "adjudication_worklist.jsonl"
APPLIED_REFERENCES_FILENAME = "references.annotated.jsonl"
APPLY_REPORT_FILENAME = "annotation_apply_report.json"
OUTPUT_PACKETS_FILENAME = "agent_output_review_packets.jsonl"
OUTPUT_REVIEWS_FILENAME = "agent_output_reviews.jsonl"
HISTORY_DIRNAME = "history"

#: Keys that must never appear in an exported annotation packet.
FORBIDDEN_PACKET_KEYS: frozenset[str] = frozenset(
    {
        "call_id",
        "transcript_id",
        "conversation_id",
        "split_manifest",
        "cutoff_record_index",
        "cutoff_seq",
        "cutoff_segment_id",
        "cutoff_start_ms",
        "hidden_speech_turn_count",
        "historical_next_action_reference",
        "historical_next_action_segment_id",
        "historical_next_action_authority",
        "scoring_status",
        "expected_decisions",
        "primary_expected_decision",
        "acceptable_alternative_decisions",
        "annotation_provenance",
        "verified_business_rules",
        "annotator_ids",
        "adjudication_status",
    }
)

#: Minimum length of a verbatim overlap that is reported as a possible leak of the
#: hidden continuation into an annotation.
LEAK_NGRAM = 12


class AnnotationPacket(DomainModel):
    """One review-ready annotation packet for one task."""

    packet_schema_version: str = ANNOTATION_PACKET_VERSION
    packet_id: str
    task_id: str
    sample_purpose: str = ""
    split: str = ""
    guideline_version: str = ANNOTATION_GUIDELINE_VERSION
    instruction: str = ""
    visible_context: list[VisibleTurn] = Field(default_factory=list)
    reference_material: list[BusinessRuleCitation] = Field(default_factory=list)
    reference_material_status: str = "no_approved_authoritative_material"
    annotation_form: dict[str, object] = Field(default_factory=dict)
    annotator_ids_allowed: int = 2
    review_note: str = ""


class AnnotationPacketManifest(DomainModel):
    packets: int = 0
    guideline_version: str = ANNOTATION_GUIDELINE_VERSION
    sample_purpose: str = ""
    split: str = ""
    sampling_plan_id: str = ""
    sampling_plan_sha256: str = ""
    packet_sha256: str = ""
    generated_at: str = ""
    forbidden_key_check: bool = True
    hidden_continuation_exported: bool = False
    reference_material_status: str = "no_approved_authoritative_material"
    notes: list[str] = Field(default_factory=list)


class SubmissionDecision(DomainModel):
    """One imported submission and the decision about it."""

    task_id: str
    annotator_id: str
    authority: str
    accepted: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    guideline_version: str = ""


class AnnotationImportReport(DomainModel):
    files_read: int = 0
    submissions_in_file: int = 0
    accepted: int = 0
    rejected: int = 0
    tasks_covered: int = 0
    tasks_with_two_human_ratings: int = 0
    tasks_with_one_human_rating: int = 0
    llm_proposed: int = 0
    duplicate_submissions: int = 0
    guideline_mismatches: int = 0
    unknown_task_ids: list[str] = Field(default_factory=list)
    possible_continuation_leaks: list[str] = Field(default_factory=list)
    decisions: list[SubmissionDecision] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AdjudicationWorklistRow(DomainModel):
    task_id: str
    status: str
    annotator_ids: list[str] = Field(default_factory=list)
    agreement: bool = False
    reason: str = ""
    labels: dict[str, str] = Field(default_factory=dict)


class AnnotationApplyReport(DomainModel):
    references_read: int = 0
    references_written: int = 0
    scored: int = 0
    candidate: int = 0
    left_unscored: int = 0
    skipped_llm_only: int = 0
    skipped_no_adjudication: int = 0
    skipped_inconsistent: int = 0
    by_tier: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _blank_annotation_form() -> dict[str, object]:
    """Return the annotation fields a reviewer fills in.

    The blank form is part of the packet so a reviewer cannot invent a field the
    importer will silently drop. Every enumerated field lists its permitted values.
    """
    return {
        "annotator_id": "",
        "annotation_status": ["pending", "in_review", "reviewed"],
        "task_answerability": [
            "sufficient_information",
            "more_information_required",
            "ambiguous",
            "unscorable",
        ],
        "primary_expected_decision": {
            "decision_id": "",
            "label": "",
            "decision_family": "",
            "description": "",
            "expected_customer_response_intent": "",
            "required_steps": [],
            "optional_steps": [],
            "prohibited_steps": [],
            "rule_citations": [],
            "evidence_refs": [],
        },
        "acceptable_alternative_decisions": [],
        "branch_conditions": [
            {
                "condition_id": "",
                "description": "",
                "expected_branch": "",
                "required_information": [],
            }
        ],
        "expected_branch": "",
        "required_steps": [],
        "optional_steps": [],
        "prohibited_steps": [],
        "expected_clarification": [],
        "verified_business_rules": [],
        "evidence_refs": [],
        "reviewer_confidence": None,
        "guideline_version": ANNOTATION_GUIDELINE_VERSION,
        "notes": "",
    }


def export_annotation_packets(
    store: TaskStore,
    plan: SamplingPlan,
    output_dir: str | Path,
    *,
    reference_material: Sequence[BusinessRuleCitation] | None = None,
    reference_material_status: str = "no_approved_authoritative_material",
    guard_test_isolation: bool = True,
) -> tuple[AnnotationPacketManifest, Path]:
    """Write review-ready packets for one sampling plan.

    The packet is built from :class:`PublicDecisionTask` only. The private
    reference is used for exactly one purpose: proving that nothing private is
    written. Its historical continuation is never read into the packet.
    """
    if guard_test_isolation and plan.split == "test":
        raise AnnotationError(
            "refusing to export annotation packets for the isolated Test split"
        )
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / PACKETS_FILENAME

    digest = hashlib.sha256()
    packets = 0
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for sampled in plan.selected:
            public = store.load_public_task(sampled.task_id)
            packet = AnnotationPacket(
                packet_id="PACKET-" + _sha256_text(
                    f"{plan.plan_id}:{sampled.task_id}"
                )[:16],
                task_id=public.task_id,
                sample_purpose=plan.purpose,
                split=plan.split,
                guideline_version=plan.guideline_version,
                instruction=public.agent_task.instruction,
                visible_context=list(public.visible_context),
                reference_material=list(reference_material or []),
                reference_material_status=reference_material_status,
                annotation_form=_blank_annotation_form(),
                review_note=(
                    "Annotate only from the visible conversation prefix above and "
                    "the approved reference material. The historical continuation "
                    "is deliberately withheld so it cannot anchor the annotation."
                ),
            )
            _assert_packet_is_clean(packet)
            line = json.dumps(
                packet.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.write(line + "\n")
            digest.update(line.encode("utf-8"))
            packets += 1

    plan_sha = _sha256_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    )
    manifest = AnnotationPacketManifest(
        packets=packets,
        guideline_version=plan.guideline_version,
        sample_purpose=plan.purpose,
        split=plan.split,
        sampling_plan_id=plan.plan_id,
        sampling_plan_sha256=plan_sha,
        packet_sha256=digest.hexdigest(),
        generated_at=_now(),
        forbidden_key_check=True,
        hidden_continuation_exported=False,
        reference_material_status=reference_material_status,
        notes=[
            "Every packet contains only the agent-visible prefix, the task "
            "instruction and blank annotation fields.",
            "No historical continuation, hidden segment id, call id or label is "
            "present in any packet.",
        ],
    )
    (root / PACKETS_MANIFEST_FILENAME).write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return manifest, target


def _assert_packet_is_clean(packet: AnnotationPacket) -> None:
    """Prove that a packet carries no private evaluator field.

    The blank ``annotation_form`` deliberately names the fields a reviewer must
    fill in, and those names overlap the private reference schema. The scan
    therefore checks the top-level field set and every *value-bearing* section,
    and treats the blank form as a schema description rather than data.
    """
    payload = packet.model_dump(mode="json")
    leaked = set(payload) & FORBIDDEN_PACKET_KEYS
    if leaked:
        raise AnnotationError(
            f"annotation packet contains forbidden fields: {sorted(leaked)}"
        )
    scanned = {key: value for key, value in payload.items() if key != "annotation_form"}
    serialized = json.dumps(scanned, ensure_ascii=False)
    for key in FORBIDDEN_PACKET_KEYS:
        if f'"{key}"' in serialized:
            raise AnnotationError(
                f"annotation packet embeds a forbidden key value: {key}"
            )


def iter_packets(path: str | Path) -> Iterable[AnnotationPacket]:
    from pydantic import ValidationError

    target = Path(path)
    if not target.is_file():
        raise AnnotationError(f"annotation packet file not found: {target}")
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield AnnotationPacket.model_validate_json(line)
            except ValidationError as error:
                raise AnnotationError(
                    f"invalid annotation packet on line {line_number}"
                ) from error


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def import_annotation_submissions(
    paths: Sequence[str | Path],
    store: TaskStore,
    *,
    output_dir: str | Path | None = None,
    expected_guideline_version: str = ANNOTATION_GUIDELINE_VERSION,
    acknowledge_guideline_mismatch: bool = False,
) -> tuple[AnnotationImportReport, list[AnnotationSubmission]]:
    """Read, validate and normalise reviewer submissions.

    A submission is rejected when it is structurally inconsistent, when the same
    annotator already submitted for the same task, or when its guideline version
    differs and the caller did not acknowledge the difference. Model-only
    submissions are accepted into the record but flagged, and they never become
    verified ground truth further down the pipeline.
    """
    report = AnnotationImportReport()
    accepted: list[AnnotationSubmission] = []
    seen: dict[tuple[str, str], AnnotationSubmission] = {}
    known_tasks = set(store.task_ids)

    for path in paths:
        target = Path(path)
        if not target.is_file():
            raise AnnotationError(f"annotation submission file not found: {target}")
        report.files_read += 1
        with target.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                report.submissions_in_file += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as error:
                    raise AnnotationError(
                        f"submission file {target.name} line {line_number} is not "
                        "valid JSON"
                    ) from error
                try:
                    submission = AnnotationSubmission.model_validate(payload)
                except Exception as error:  # noqa: BLE001 - pydantic ValidationError
                    report.rejected += 1
                    report.decisions.append(
                        SubmissionDecision(
                            task_id=str(payload.get("task_id", "")),
                            annotator_id=str(payload.get("annotator_id", "")),
                            authority="unknown",
                            accepted=False,
                            reasons=[f"schema_invalid: {type(error).__name__}"],
                        )
                    )
                    continue
                decision = _validate_one(
                    submission,
                    known_tasks=known_tasks,
                    seen=seen,
                    expected_guideline_version=expected_guideline_version,
                    acknowledge_guideline_mismatch=acknowledge_guideline_mismatch,
                )
                report.decisions.append(decision)
                if any(
                    reason.startswith("duplicate_submission")
                    for reason in decision.reasons
                ):
                    report.duplicate_submissions += 1
                if decision.warnings:
                    report.guideline_mismatches += 1
                if not decision.accepted:
                    report.rejected += 1
                    continue
                accepted.append(submission)
                report.accepted += 1
                if submission.provenance is not None and (
                    submission.provenance.is_llm_only
                ):
                    report.llm_proposed += 1

    by_task: dict[str, set[str]] = defaultdict(set)
    for submission in accepted:
        by_task[submission.task_id].add(submission.annotator_id)
    report.tasks_covered = len(by_task)
    report.tasks_with_two_human_ratings = sum(
        1 for annotators in by_task.values() if len(annotators) >= 2
    )
    report.tasks_with_one_human_rating = sum(
        1 for annotators in by_task.values() if len(annotators) == 1
    )
    report.possible_continuation_leaks = sorted(
        _detect_continuation_leaks(accepted, store)
    )
    if report.possible_continuation_leaks:
        report.notes.append(
            "some annotation text overlaps the withheld historical continuation. "
            "That is reported as a warning, not proof of leakage: outbound calls "
            "repeat stock phrases. Review the listed tasks before scoring."
        )
    if report.llm_proposed:
        report.notes.append(
            f"{report.llm_proposed} submission(s) are llm_proposed and can never "
            "become verified ground truth. A human must annotate independently."
        )

    if output_dir is not None:
        _write_import_artifacts(Path(output_dir), accepted, report)
    return report, accepted


def _validate_one(
    submission: AnnotationSubmission,
    *,
    known_tasks: set[str],
    seen: Mapping[tuple[str, str], AnnotationSubmission],
    expected_guideline_version: str,
    acknowledge_guideline_mismatch: bool,
) -> SubmissionDecision:
    authority = (
        submission.provenance.authority
        if submission.provenance is not None
        else "unknown"
    )
    reasons: list[str] = []
    warnings: list[str] = []

    if submission.task_id not in known_tasks:
        reasons.append("unknown_task_id")
    if submission.guideline_version != expected_guideline_version:
        if acknowledge_guideline_mismatch:
            warnings.append(
                f"guideline_version_mismatch: {submission.guideline_version}"
            )
        else:
            reasons.append(
                f"guideline_version_mismatch: {submission.guideline_version}"
            )
    key = (submission.task_id, submission.annotator_id)
    if key in seen:
        reasons.append("duplicate_submission_for_same_annotator_and_task")
    issues = validate_annotation_consistency(submission)
    if issues:
        reasons.extend(issues)
    if submission.provenance is None:
        reasons.append("annotation_provenance_missing")
    elif submission.provenance.authority == AUTHORITY_HUMAN_PRIMARY and (
        submission.guideline_version != ANNOTATION_GUIDELINE_VERSION
    ):
        warnings.append("human_annotation_on_an_older_guideline")

    accepted = not reasons
    if accepted:
        seen[key] = submission
    return SubmissionDecision(
        task_id=submission.task_id,
        annotator_id=submission.annotator_id,
        authority=authority,
        accepted=accepted,
        reasons=reasons,
        warnings=warnings,
        guideline_version=submission.guideline_version,
    )


def _detect_continuation_leaks(
    submissions: Sequence[AnnotationSubmission],
    store: TaskStore,
) -> set[str]:
    """Flag submissions whose text reproduces the withheld continuation."""
    flagged: set[str] = set()
    for submission in submissions:
        reference = store.require_reference(submission.task_id)
        hidden = reference.historical_next_action_reference or ""
        hidden_ngrams = _ngrams(hidden, LEAK_NGRAM)
        if not hidden_ngrams:
            continue
        annotated = " ".join(
            [
                submission.primary_expected_decision.description
                if submission.primary_expected_decision
                else "",
                submission.notes,
                " ".join(submission.required_steps),
                " ".join(submission.evidence_refs),
            ]
        )
        if hidden_ngrams & _ngrams(annotated, LEAK_NGRAM):
            flagged.add(submission.task_id)
    return flagged


def _ngrams(text: str, size: int) -> set[str]:
    compact = "".join(character for character in text if not character.isspace())
    if len(compact) < size:
        return set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _write_import_artifacts(
    root: Path,
    submissions: Sequence[AnnotationSubmission],
    report: AnnotationImportReport,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / SUBMISSIONS_FILENAME
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for submission in submissions:
            handle.write(
                json.dumps(
                    submission.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    (root / IMPORT_REPORT_FILENAME).write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    history = root / HISTORY_DIRNAME
    history.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (history / f"import-{stamp}.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_submissions(path: str | Path) -> list[AnnotationSubmission]:
    from pydantic import ValidationError

    target = Path(path)
    if not target.is_file():
        raise AnnotationError(f"submission file not found: {target}")
    submissions: list[AnnotationSubmission] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                submissions.append(AnnotationSubmission.model_validate_json(line))
            except ValidationError as error:
                raise AnnotationError(
                    f"invalid submission on line {line_number} of {target.name}"
                ) from error
    return submissions


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


def build_adjudication_worklist(
    submissions: Sequence[AnnotationSubmission],
) -> list[AdjudicationWorklistRow]:
    """Return one row per task that needs a human decision.

    A task whose two independent human annotations agree structurally is marked
    ``not_required``. Everything else goes to a human adjudicator: nothing here
    resolves a disagreement automatically, and no model is allowed to adjudicate.
    """
    by_task: dict[str, list[AnnotationSubmission]] = defaultdict(list)
    for submission in submissions:
        by_task[submission.task_id].append(submission)

    rows: list[AdjudicationWorklistRow] = []
    for task_id, items in sorted(by_task.items()):
        human = [
            item
            for item in items
            if item.provenance is not None and item.provenance.is_human
        ]
        labels = {
            item.annotator_id: (
                "|".join(sorted(item.decision_identities()))
                or f"answerability:{item.task_answerability}"
            )
            for item in human
        }
        if len(human) < 2:
            rows.append(
                AdjudicationWorklistRow(
                    task_id=task_id,
                    status=ADJUDICATION_PENDING,
                    annotator_ids=[item.annotator_id for item in human],
                    reason="fewer_than_two_human_annotations",
                    labels=labels,
                )
            )
            continue
        distinct = set(labels.values())
        step_sets = {tuple(sorted(item.required_steps)) for item in human}
        if len(distinct) == 1 and len(step_sets) == 1:
            rows.append(
                AdjudicationWorklistRow(
                    task_id=task_id,
                    status=ADJUDICATION_NOT_REQUIRED,
                    annotator_ids=[item.annotator_id for item in human],
                    agreement=True,
                    reason="independent_human_annotations_agree",
                    labels=labels,
                )
            )
        else:
            rows.append(
                AdjudicationWorklistRow(
                    task_id=task_id,
                    status=ADJUDICATION_PENDING,
                    annotator_ids=[item.annotator_id for item in human],
                    agreement=False,
                    reason="human_annotations_disagree",
                    labels=labels,
                )
            )
    return rows


def save_adjudication_worklist(
    root: str | Path, rows: Sequence[AdjudicationWorklistRow]
) -> Path:
    target = Path(root) / ADJUDICATION_WORKLIST_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return target


def import_adjudications(
    path: str | Path,
    submissions: Sequence[AnnotationSubmission],
) -> list[AdjudicationRecord]:
    """Validate a human adjudication file.

    Structural guards:

    * the adjudicator must not be one of the two annotators of the task;
    * a ``resolved`` record must carry the final annotation;
    * a resolved annotation must be attributed to ``human_adjudicated``;
    * the task must actually have at least two independent human annotations.
    """
    target = Path(path)
    if not target.is_file():
        raise AnnotationError(f"adjudication file not found: {target}")
    by_task: dict[str, list[AnnotationSubmission]] = defaultdict(list)
    for submission in submissions:
        by_task[submission.task_id].append(submission)

    records: list[AdjudicationRecord] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = AdjudicationRecord.model_validate_json(line)
            except Exception as error:  # noqa: BLE001
                raise AnnotationError(
                    f"invalid adjudication on line {line_number}"
                ) from error
            annotators = {
                item.annotator_id
                for item in by_task.get(record.task_id, [])
                if item.provenance is not None and item.provenance.is_human
            }
            if record.task_id not in by_task:
                raise AnnotationError(
                    f"adjudication for unknown task {record.task_id}"
                )
            if len(annotators) < 2:
                raise AnnotationError(
                    f"task {record.task_id} has fewer than two human annotations; "
                    "adjudication cannot fill that gap"
                )
            if record.status == ADJUDICATION_RESOLVED:
                if record.adjudicator_id in annotators:
                    raise AnnotationError(
                        f"task {record.task_id}: the adjudicator must not be one of "
                        "the annotators"
                    )
                final = record.final_annotation
                if final is None or final.provenance is None:
                    raise AnnotationError(
                        f"task {record.task_id}: a resolved adjudication needs a "
                        "final annotation with provenance"
                    )
                if final.provenance.authority != AUTHORITY_HUMAN_ADJUDICATED:
                    raise AnnotationError(
                        f"task {record.task_id}: the final annotation authority must "
                        "be human_adjudicated"
                    )
            record.annotator_ids = sorted(annotators)
            records.append(record)
    return records


def save_adjudications(
    root: str | Path, records: Sequence[AdjudicationRecord]
) -> Path:
    target = Path(root) / ADJUDICATIONS_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    record.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    return target


# ---------------------------------------------------------------------------
# Apply annotations to references
# ---------------------------------------------------------------------------


def apply_annotations_to_references(
    store: TaskStore,
    submissions: Sequence[AnnotationSubmission],
    adjudications: Sequence[AdjudicationRecord],
    *,
    output_path: str | Path,
    report_path: str | Path | None = None,
) -> tuple[AnnotationApplyReport, Path]:
    """Write an annotated copy of the private reference artifact.

    Only a human-adjudicated (or unanimously reviewed) human annotation can set
    ``scoring_status = scored``. Model-proposed annotations are recorded as
    ``candidate`` so they are visible to a reviewer but excluded from every
    denominator.
    """
    adjudication_by_task = {record.task_id: record for record in adjudications}
    by_task: dict[str, list[AnnotationSubmission]] = defaultdict(list)
    for submission in submissions:
        by_task[submission.task_id].append(submission)

    report = AnnotationApplyReport()
    updated: list[PrivateTaskReference] = []
    for task_id in store.task_ids:
        reference = store.require_reference(task_id)
        report.references_read += 1
        items = by_task.get(task_id, [])
        updated_reference, outcome = _apply_one(
            reference, items, adjudication_by_task.get(task_id), report
        )
        updated.append(updated_reference)
        if outcome == "scored":
            report.scored += 1
        elif outcome == "candidate":
            report.candidate += 1
        else:
            report.left_unscored += 1

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for reference in updated:
            handle.write(
                json.dumps(
                    reference.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    report.references_written = len(updated)
    report.notes.append(
        "A scored reference requires two independent human annotations or a "
        "human adjudication. Model-proposed annotations are written as candidates "
        "and are excluded from every business denominator."
    )
    if report_path is not None:
        output = Path(report_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return report, target


def _apply_one(
    reference: PrivateTaskReference,
    submissions: Sequence[AnnotationSubmission],
    adjudication: AdjudicationRecord | None,
    report: AnnotationApplyReport,
) -> tuple[PrivateTaskReference, str]:
    if adjudication is not None and adjudication.status == ADJUDICATION_RESOLVED:
        final = adjudication.final_annotation
        if final is None:  # pragma: no cover - guarded by the importer
            report.skipped_no_adjudication += 1
            return reference, "unscored"
        return _reference_with_annotation(
            reference,
            final,
            report,
            adjudicated=True,
            adjudicator_id=adjudication.adjudicator_id,
            annotator_ids=adjudication.annotator_ids,
        )

    human = [
        item
        for item in submissions
        if item.provenance is not None and item.provenance.is_human
    ]
    llm = [
        item
        for item in submissions
        if item.provenance is not None and item.provenance.is_llm_only
    ]
    if len(human) >= 2:
        # Two independent human annotations without an adjudication record are a
        # reviewed consensus only when they agree structurally.
        identities = {"|".join(sorted(item.decision_identities())) for item in human}
        steps = {tuple(sorted(item.required_steps)) for item in human}
        if len(identities) == 1 and len(steps) == 1:
            return _reference_with_annotation(
                reference,
                human[0],
                report,
                adjudicated=False,
                adjudicator_id="",
                annotator_ids=sorted(item.annotator_id for item in human),
            )
        report.skipped_no_adjudication += 1
        return reference, "unscored"
    if human:
        report.skipped_no_adjudication += 1
        return reference, "unscored"
    if llm:
        updated = reference.model_copy(deep=True)
        updated.annotation_provenance = llm[0].provenance
        updated.annotation_status = "in_review"
        updated.scoring_status = "candidate"
        updated.task_answerability = llm[0].task_answerability
        updated.annotator_ids = [llm[0].annotator_id]
        updated.annotation_guideline_version = llm[0].guideline_version
        report.skipped_llm_only += 1
        return updated, "candidate"
    return reference, "unscored"


def _reference_with_annotation(
    reference: PrivateTaskReference,
    submission: AnnotationSubmission,
    report: AnnotationApplyReport,
    *,
    adjudicated: bool,
    adjudicator_id: str,
    annotator_ids: Sequence[str],
) -> tuple[PrivateTaskReference, str]:
    issues = validate_annotation_consistency(submission)
    if issues:
        report.skipped_inconsistent += 1
        return reference, "unscored"

    updated = reference.model_copy(deep=True)
    updated.annotation_status = (
        ANNOTATION_ADJUDICATED if adjudicated else ANNOTATION_REVIEWED
    )
    updated.adjudication_status = (
        ADJUDICATION_RESOLVED if adjudicated else ADJUDICATION_NOT_REQUIRED
    )
    updated.task_answerability = submission.task_answerability
    updated.primary_expected_decision = submission.primary_expected_decision
    updated.acceptable_alternative_decisions = list(
        submission.acceptable_alternative_decisions
    )
    updated.branch_conditions = list(submission.branch_conditions)
    updated.expected_branch = submission.expected_branch
    updated.required_steps = list(submission.required_steps)
    updated.optional_steps = list(submission.optional_steps)
    updated.prohibited_steps = list(submission.prohibited_steps)
    updated.expected_clarification = list(submission.expected_clarification)
    updated.verified_business_rules = list(submission.verified_business_rules)
    updated.rule_citations = list(submission.rule_citations)
    updated.evidence_refs = sorted(
        set(reference.evidence_refs) | set(submission.evidence_refs)
    )
    updated.annotation_provenance = submission.provenance
    updated.reviewer_confidence = submission.reviewer_confidence
    updated.annotator_ids = sorted({*annotator_ids, *(submission.annotator_id,)})
    updated.annotation_guideline_version = submission.guideline_version

    policy_verified = any(
        citation.supports_policy_tier for citation in submission.rule_citations
    )
    updated.verification_tier = tier_for(
        expert_adjudicated=bool(
            submission.primary_expected_decision
            or submission.acceptable_alternative_decisions
            or submission.expected_clarification
        ),
        policy_verified=policy_verified,
    )
    updated.scoring_status = "scored"
    if not updated.is_scored:
        updated.scoring_status = "candidate"
        return updated, "candidate"
    return updated, "scored"


# ---------------------------------------------------------------------------
# Blind output review packets
# ---------------------------------------------------------------------------


class OutputReviewPacket(DomainModel):
    """One blind packet for human evaluation of an agent output."""

    packet_schema_version: str = ANNOTATION_PACKET_VERSION
    packet_id: str
    task_id: str
    rubric_version: str = "response-quality-rubric-v1"
    visible_context: list[VisibleTurn] = Field(default_factory=list)
    agent_customer_response: str = ""
    agent_next_action: str = ""
    agent_required_information: list[str] = Field(default_factory=list)
    agent_evidence_summary: str = ""
    expected_decision_reference: dict[str, object] = Field(default_factory=dict)
    quality_form: dict[str, object] = Field(default_factory=dict)
    condition_blinded: bool = True
    model_identity_withheld: bool = True


def export_output_review_packets(
    store: TaskStore,
    traces_directory: str | Path,
    output_dir: str | Path,
    *,
    only_scored: bool = True,
) -> tuple[int, Path]:
    """Build blind agent-output review packets from a run set.

    Model identity, provider label, run id and condition are dropped. Only the
    output fields a human needs to rate quality are kept, plus the adjudicated
    expected-decision reference when one exists.
    """
    from cm_outbound_bench.decisions.trace import iter_traces

    traces = iter_traces(traces_directory)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / OUTPUT_PACKETS_FILENAME
    written = 0
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for trace in traces:
            if trace.agent_output is None:
                continue
            reference = store.require_reference(trace.task_id)
            if only_scored and not reference.is_scored:
                continue
            public = store.load_public_task(trace.task_id)
            packet = OutputReviewPacket(
                packet_id="OUTREVIEW-" + _sha256_text(trace.task_id)[:16],
                task_id=trace.task_id,
                visible_context=list(public.visible_context),
                agent_customer_response=str(
                    trace.agent_output.get("customer_response", "")
                ),
                agent_next_action=str(trace.agent_output.get("next_action", "")),
                agent_required_information=list(
                    trace.agent_output.get("required_information", [])
                ),
                agent_evidence_summary=str(
                    trace.agent_output.get("evidence_summary", "")
                ),
                expected_decision_reference=_expected_reference_payload(reference),
                quality_form=_blank_quality_form(),
            )
            handle.write(
                json.dumps(
                    packet.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            written += 1
    return written, target


def _expected_reference_payload(reference: PrivateTaskReference) -> dict[str, object]:
    if not reference.is_scored:
        return {}
    primary = reference.primary_expected_decision
    return {
        "task_answerability": reference.task_answerability,
        "primary_decision_label": primary.label if primary else None,
        "primary_decision_family": primary.decision_family if primary else None,
        "acceptable_alternatives": [
            decision.label for decision in reference.acceptable_alternative_decisions
        ],
        "required_steps": list(reference.required_steps),
        "prohibited_steps": list(reference.prohibited_steps),
        "expected_clarification": list(reference.expected_clarification),
        "verification_tier": reference.effective_verification_tier,
    }


def _blank_quality_form() -> dict[str, object]:
    return {
        "reviewer_id": "",
        "decision_appropriateness_1_to_5": None,
        "customer_response_quality_1_to_5": None,
        "factual_grounding_1_to_5": None,
        "professional_tone_1_to_5": None,
        "unsupported_assumptions": False,
        "notes": "",
    }


def load_output_reviews(path: str | Path) -> dict[str, float]:
    """Load human response-quality reviews and return ``task_id -> 0..1``.

    The score is the mean of the four rubric dimensions mapped from 1-5 onto 0-1.
    Missing dimensions are ignored; a review with no dimension is skipped rather
    than counted as zero.
    """
    target = Path(path)
    if not target.is_file():
        raise AnnotationError(f"output review file not found: {target}")
    scores: dict[str, float] = {}
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise AnnotationError(
                    f"invalid output review on line {line_number}"
                ) from error
            task_id = str(payload.get("task_id", ""))
            form = payload.get("quality_form") or payload
            values = [
                float(form[key])
                for key in (
                    "decision_appropriateness_1_to_5",
                    "customer_response_quality_1_to_5",
                    "factual_grounding_1_to_5",
                    "professional_tone_1_to_5",
                )
                if form.get(key) is not None
            ]
            if not task_id or not values:
                continue
            mean = sum(values) / len(values)
            scores[task_id] = round(max(0.0, min(1.0, (mean - 1.0) / 4.0)), 4)
    return scores


__all__ = [
    "ADJUDICATIONS_FILENAME",
    "ADJUDICATION_WORKLIST_FILENAME",
    "ANNOTATION_PACKET_VERSION",
    "APPLIED_REFERENCES_FILENAME",
    "APPLY_REPORT_FILENAME",
    "AdjudicationWorklistRow",
    "AnnotationApplyReport",
    "AnnotationError",
    "AnnotationImportReport",
    "AnnotationPacket",
    "AnnotationPacketManifest",
    "FORBIDDEN_PACKET_KEYS",
    "LEAK_NGRAM",
    "OUTPUT_PACKETS_FILENAME",
    "OUTPUT_REVIEWS_FILENAME",
    "OutputReviewPacket",
    "PACKETS_FILENAME",
    "SUBMISSIONS_FILENAME",
    "SubmissionDecision",
    "apply_annotations_to_references",
    "build_adjudication_worklist",
    "export_annotation_packets",
    "export_output_review_packets",
    "import_adjudications",
    "import_annotation_submissions",
    "iter_packets",
    "load_output_reviews",
    "load_submissions",
    "save_adjudication_worklist",
    "save_adjudications",
]
