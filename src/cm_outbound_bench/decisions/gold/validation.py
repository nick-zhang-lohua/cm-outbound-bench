"""Gold reference validation, application to PrivateTaskReference, and freezing.

The frozen artifact is immutable: writing it twice to the same version is
refused, and every reference is validated against the agent-visible task before
it can enter the frozen file. Evidence references may only point at visible
turns; any future-turn or out-of-range reference invalidates the annotation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from cm_outbound_bench.decisions.annotation import (
    ANNOTATION_ACCEPTED_AS_GOLD,
    ANNOTATION_GUIDELINE_VERSION,
    ANNOTATION_METHOD_LLM_BASED,
    AUTHORITY_LLM_GENERATED_GOLD,
    GROUND_TRUTH_SOURCE_DEEPSEEK_V4_PRO,
    GROUND_TRUTH_STATUS_FROZEN,
    GROUND_TRUTH_TYPE_LLM_GENERATED_GOLD,
    GROUND_TRUTH_VERSION_V1,
    POLICY_DEPENDENT_SCOREABLE_CATEGORIES,
    AnnotationProvenance,
    BranchCondition,
    DecisionReference,
)
from cm_outbound_bench.decisions.gold.models import (
    FinalGoldState,
    GoldDecision,
)
from cm_outbound_bench.decisions.gold.storage import (
    ANNOTATION_SUMMARY_FILENAME,
    FINAL_REFERENCES_FILENAME,
    FROZEN_MANIFEST_FILENAME,
    sha256_file,
    utc_now,
)
from cm_outbound_bench.decisions.schema import PrivateTaskReference, PublicDecisionTask


class GoldValidationError(RuntimeError):
    """Raised when a gold artifact fails validation."""


class FreezeError(RuntimeError):
    """Raised when a frozen artifact would be silently overwritten."""


def validate_final(final: FinalGoldState, task: PublicDecisionTask) -> list[str]:
    """Return structural problems with one final gold state. Empty means valid."""
    issues: list[str] = []
    if final.scoring_status == "scored":
        if final.final is None:
            issues.append("scored_state_has_no_final_annotation")
            return issues
        annotation = final.final
        if not annotation.scoreable:
            issues.append("scored_state_annotation_is_not_scoreable")
        if annotation.primary_expected_decision is None and not (
            annotation.required_information
        ):
            issues.append("scored_state_has_no_decision_and_no_required_information")
        if (
            annotation.task_answerability == "clarification_required"
            and not annotation.required_information
        ):
            issues.append("clarification_required_must_name_required_information")
        if annotation.task_answerability == "requires_external_business_policy":
            primary = annotation.primary_expected_decision
            if primary is None or (
                primary.category not in POLICY_DEPENDENT_SCOREABLE_CATEGORIES
            ):
                issues.append(
                    "external_policy_task_scored_with_non_clarification_decision"
                )
        if annotation.task_answerability not in {
            "sufficient_for_action",
            "clarification_required",
            "requires_external_business_policy",
        }:
            issues.append("scored_task_has_unscoreable_answerability")
    if final.final is not None:
        annotation = final.final
        visible_turns = len(task.visible_context)
        for evidence in annotation.evidence_refs:
            if evidence.visible_turn < 1 or evidence.visible_turn > visible_turns:
                issues.append(
                    f"evidence_ref_turn_out_of_range:{evidence.visible_turn}"
                )
        if annotation.expected_branch and not annotation.branch_conditions:
            issues.append("expected_branch_without_branch_conditions")
        if annotation.policy_verified:
            issues.append(
                "policy_verified_must_not_be_true_without_an_authoritative_source"
            )
    return issues


def demote_unscorable(
    final: FinalGoldState, issues: Sequence[str]
) -> FinalGoldState:
    """Return a copy of the final state demoted to unscored with the reason."""
    if not issues:
        return final
    annotation = final.final
    if annotation is not None:
        annotation = annotation.model_copy(deep=True)
        annotation.scoreable = False
        annotation.unscorable_reason = (
            annotation.unscorable_reason or "; ".join(issues)[:400]
        )
    return FinalGoldState(
        task_id=final.task_id,
        comparison=final.comparison,
        adjudication=final.adjudication,
        final=annotation,
        scoring_status="unscored",
        source=final.source,
        notes=[*final.notes, f"demoted_to_unscored: {'; '.join(issues)}"],
    )


def decision_to_reference(
    decision: GoldDecision,
    *,
    decision_id: str,
    steps: Sequence[str],
    prohibited: Sequence[str],
    evidence: Sequence[str],
) -> DecisionReference:
    return DecisionReference(
        decision_id=decision_id,
        label=decision.action,
        decision_family=decision.category,
        decision_category=decision.category,
        rationale=decision.reason,
        description=decision.reason,
        match_keywords=[decision.action],
        required_steps=list(steps),
        prohibited_steps=list(prohibited),
        evidence_refs=list(evidence),
    )


def build_gold_reference(
    *,
    final: FinalGoldState,
    base: PrivateTaskReference,
    task: PublicDecisionTask,
    model: str,
    prompt_version: str,
    config_hash: str,
    provider_label: str,
) -> PrivateTaskReference:
    """Produce the gold-annotated PrivateTaskReference for one task."""
    annotation = final.final
    reference = base.model_copy(deep=True)
    reference.ground_truth_type = GROUND_TRUTH_TYPE_LLM_GENERATED_GOLD
    reference.ground_truth_source = GROUND_TRUTH_SOURCE_DEEPSEEK_V4_PRO
    reference.ground_truth_status = GROUND_TRUTH_STATUS_FROZEN
    reference.ground_truth_version = GROUND_TRUTH_VERSION_V1
    reference.annotation_method = ANNOTATION_METHOD_LLM_BASED
    reference.annotation_status = ANNOTATION_ACCEPTED_AS_GOLD
    reference.annotation_guideline_version = ANNOTATION_GUIDELINE_VERSION
    reference.verification_tier = "none"  # LLM gold is not a human expert tier

    if annotation is None:
        reference.scoring_status = "unscored"
        reference.task_answerability = "insufficient_context"
        reference.unscorable_reason = final.notes[-1] if final.notes else "no annotation"
        reference.adjudication_status = (
            "adjudicated" if final.adjudication is not None else "not_required"
        )
        return reference

    reference.task_answerability = annotation.task_answerability
    reference.external_policy_dependency = annotation.external_policy_dependency
    reference.policy_verified = False  # never true without an authoritative source
    reference.annotation_confidence = annotation.confidence
    reference.required_information = list(annotation.required_information)
    reference.unscorable_reason = annotation.unscorable_reason
    reference.customer_intent = annotation.customer_intent
    reference.conversation_state = annotation.conversation_state
    reference.unsupported_claims_to_avoid = list(annotation.unsupported_claims_to_avoid)
    reference.assumptions_not_allowed = list(annotation.assumptions_not_allowed)
    reference.expected_clarification = (
        list(annotation.required_information)
        if annotation.task_answerability == "clarification_required"
        else []
    )
    reference.branch_conditions = [
        BranchCondition(
            condition_id=f"branch-{index}",
            description=branch.condition,
            expected_branch=(
                f"if {branch.if_branch} else {branch.else_branch}".strip()
                if branch.if_branch or branch.else_branch
                else branch.condition
            ),
            required_information=[],
        )
        for index, branch in enumerate(annotation.branch_conditions, start=1)
    ]
    reference.expected_branch = annotation.expected_branch
    reference.required_steps = list(annotation.required_steps)
    reference.optional_steps = list(annotation.optional_steps)
    reference.prohibited_steps = list(annotation.prohibited_steps)
    reference.evidence_refs = [
        f"visible_turn:{item.visible_turn}" for item in annotation.evidence_refs
    ]
    reference.annotation_provenance = AnnotationProvenance(
        authority=AUTHORITY_LLM_GENERATED_GOLD,
        method=ANNOTATION_METHOD_LLM_BASED,
        annotator_id=f"{model}:{prompt_version}",
        reviewer_id=model,
        model_name=model,
        model_version=prompt_version,
        prompt_version=prompt_version,
        guideline_version=ANNOTATION_GUIDELINE_VERSION,
        created_at=utc_now(),
        notes=(
            f"provider={provider_label}; config_hash={config_hash[:16]}; "
            f"comparison={final.comparison.classification}; "
            f"adjudicated={'yes' if final.adjudication is not None else 'no'}"
        ),
    )

    if final.scoring_status == "scored" and annotation.scoreable:
        reference.scoring_status = "scored"
        reference.primary_expected_decision = (
            decision_to_reference(
                annotation.primary_expected_decision,
                decision_id="gold-primary",
                steps=annotation.required_steps,
                prohibited=annotation.prohibited_steps,
                evidence=reference.evidence_refs,
            )
            if annotation.primary_expected_decision is not None
            else None
        )
        reference.acceptable_alternative_decisions = [
            decision_to_reference(
                decision,
                decision_id=f"gold-alt-{index}",
                steps=annotation.required_steps,
                prohibited=annotation.prohibited_steps,
                evidence=reference.evidence_refs,
            )
            for index, decision in enumerate(
                annotation.acceptable_alternative_decisions, start=1
            )
        ]
    else:
        reference.scoring_status = "unscored"
        reference.primary_expected_decision = None
        reference.acceptable_alternative_decisions = []
    reference.adjudication_status = (
        "adjudicated" if final.adjudication is not None else "not_required"
    )
    return reference


def apply_finals(
    *,
    finals: Iterable[FinalGoldState],
    references: Mapping[str, PrivateTaskReference],
    tasks: Mapping[str, PublicDecisionTask],
    model: str,
    prompt_version: str,
    config_hash: str,
    provider_label: str,
) -> tuple[dict[str, PrivateTaskReference], list[str]]:
    """Apply validated finals onto base references. Duplicates by task id are
    rejected rather than counted twice."""
    gold: dict[str, PrivateTaskReference] = {}
    problems: list[str] = []
    for final in finals:
        if final.task_id in gold:
            problems.append(f"{final.task_id}: duplicate final state")
            continue
        base = references.get(final.task_id)
        task = tasks.get(final.task_id)
        if base is None or task is None:
            problems.append(f"{final.task_id}: no base reference or task")
            continue
        if base.split not in {"train_r", "dev"}:
            problems.append(f"{final.task_id}: unexpected split {base.split!r}")
            continue
        issues = validate_final(final, task)
        if issues:
            final = demote_unscorable(final, issues)
        gold[final.task_id] = build_gold_reference(
            final=final,
            base=base,
            task=task,
            model=model,
            prompt_version=prompt_version,
            config_hash=config_hash,
            provider_label=provider_label,
        )
    return gold, problems


def write_references(
    directory: Path,
    references: Sequence[PrivateTaskReference],
) -> dict[str, object]:
    """Write final_references.jsonl and return its hash and counts."""
    target = Path(directory) / FINAL_REFERENCES_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    scored = sum(1 for reference in references if reference.scoring_status == "scored")
    lines = [
        json.dumps(reference.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        for reference in sorted(references, key=lambda item: item.task_id)
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "path": str(target),
        "tasks": len(references),
        "scored": scored,
        "unscored": len(references) - scored,
        "sha256": sha256_file(target),
    }


def write_frozen_manifest(
    manifests_dir: Path,
    *,
    train_r: dict[str, object],
    dev: dict[str, object],
    prompt_version: str,
    dataset_version: str,
    model: str,
) -> Path:
    """Write the frozen manifest. Refusing to overwrite an existing version."""
    target = Path(manifests_dir) / FROZEN_MANIFEST_FILENAME
    if target.is_file():
        raise FreezeError(
            f"frozen manifest already exists at {target}; a new Ground Truth "
            "version requires an explicit new version id"
        )
    payload = {
        "ground_truth_version": GROUND_TRUTH_VERSION_V1,
        "ground_truth_source": GROUND_TRUTH_SOURCE_DEEPSEEK_V4_PRO,
        "ground_truth_type": GROUND_TRUTH_TYPE_LLM_GENERATED_GOLD,
        "annotation_prompt_version": prompt_version,
        "adjudication_prompt_version": prompt_version,
        "dataset_version": dataset_version,
        "model": model,
        "train_r": train_r,
        "dev": dev,
        "test_annotated": False,
        "frozen_at": utc_now(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def write_annotation_summary(
    directory: Path,
    summary: dict[str, object],
) -> Path:
    target = Path(directory) / ANNOTATION_SUMMARY_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


__all__ = [
    "FreezeError",
    "GoldValidationError",
    "apply_finals",
    "build_gold_reference",
    "demote_unscorable",
    "validate_final",
    "write_annotation_summary",
    "write_frozen_manifest",
    "write_references",
]
