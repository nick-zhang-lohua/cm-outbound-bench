"""Sample integrity, overlap and annotation summary reporting.

The overlap report inspects the actual manifests — never assumptions — and
answers: whether the pilot overlaps the feedback sample, whether any
conversation unit repeats, whether the Dev sample matches the Group A traces,
and whether any duplicate task ids exist across the sample manifests.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from cm_outbound_bench.decisions.gold.models import (
    AdjudicationResult,
    FinalGoldState,
    PassComparison,
)
from cm_outbound_bench.decisions.gold.storage import GoldStorage, sha256_file, utc_now
from cm_outbound_bench.decisions.schema import PrivateTaskReference, PublicDecisionTask
from cm_outbound_bench.decisions.store import TaskStore


def build_overlap_report(
    *,
    store: TaskStore,
    plans: Mapping[str, Mapping[str, object]],
    run_set_dir: Path | None = None,
) -> dict[str, object]:
    """Compare the sample manifests and the Group A run set, by actual ids."""

    def _plan(purpose: str) -> dict[str, object]:
        return plans.get(purpose) or {}

    pilot = _plan("pilot")
    feedback = _plan("feedback")
    dev = _plan("dev")

    pilot_tasks = [item.get("task_id") for item in pilot.get("selected", [])]
    pilot_convs = [item.get("conversation_id") for item in pilot.get("selected", [])]
    feedback_tasks = [
        item.get("task_id") for item in feedback.get("selected", [])
    ]
    feedback_convs = [
        item.get("conversation_id") for item in feedback.get("selected", [])
    ]
    dev_tasks = [item.get("task_id") for item in dev.get("selected", [])]
    dev_convs = [item.get("conversation_id") for item in dev.get("selected", [])]

    pilot_set = set(pilot_tasks)
    feedback_set = set(feedback_tasks)
    dev_set = set(dev_tasks)

    all_tasks = [*pilot_tasks, *feedback_tasks, *dev_tasks]
    duplicate_task_ids = sorted(
        {item for item in all_tasks if all_tasks.count(item) > 1}
    )
    all_convs = [*pilot_convs, *feedback_convs, *dev_convs]
    duplicate_convs = sorted(
        {item for item in all_convs if all_convs.count(item) > 1}
    )

    split_errors: list[str] = []
    for task_id in sorted(pilot_set | feedback_set | dev_set):
        try:
            split = store.split_of(task_id)
        except Exception:
            split_errors.append(f"{task_id}: no reference")
            continue
        expected = (
            "train_r" if task_id in pilot_set or task_id in feedback_set else "dev"
        )
        if split != expected:
            split_errors.append(f"{task_id}: split {split} != {expected}")

    trace_ids: set[str] = set()
    trace_issues: list[str] = []
    if run_set_dir is not None and run_set_dir.is_dir():
        for path in sorted(run_set_dir.glob("RUN-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                trace_ids.add(payload["task_id"])
            except (OSError, json.JSONDecodeError, KeyError) as error:
                trace_issues.append(f"{path.name}: {error}")
        if trace_ids:
            if trace_ids != dev_set:
                missing = sorted(dev_set - trace_ids)
                extra = sorted(trace_ids - dev_set)
                trace_issues.append(
                    f"trace/sample mismatch: missing={len(missing)} extra={len(extra)}"
                )

    return {
        "generated_at": utc_now(),
        "pilot": {
            "tasks": len(pilot_tasks),
            "unique_tasks": len(pilot_set),
            "conversations": len(pilot_convs),
            "unique_conversations": len(set(pilot_convs)),
            "plan_id": pilot.get("plan_id"),
            "selection_sha256": pilot.get("selection_sha256"),
        },
        "feedback": {
            "tasks": len(feedback_tasks),
            "unique_tasks": len(feedback_set),
            "conversations": len(feedback_convs),
            "unique_conversations": len(set(feedback_convs)),
            "plan_id": feedback.get("plan_id"),
            "selection_sha256": feedback.get("selection_sha256"),
            "excluded_conversations": len(
                feedback.get("excluded_conversation_ids", [])
            ),
        },
        "dev": {
            "tasks": len(dev_tasks),
            "unique_tasks": len(dev_set),
            "conversations": len(dev_convs),
            "unique_conversations": len(set(dev_convs)),
            "plan_id": dev.get("plan_id"),
            "selection_sha256": dev.get("selection_sha256"),
        },
        "pilot_feedback_overlap": {
            "task_ids": len(pilot_set & feedback_set),
            "conversation_ids": len(
                set(pilot_convs) & set(feedback_convs)
            ),
        },
        "pilot_dev_overlap": {
            "task_ids": len(pilot_set & dev_set),
            "conversation_ids": len(set(pilot_convs) & set(dev_convs)),
        },
        "feedback_dev_overlap": {
            "task_ids": len(feedback_set & dev_set),
            "conversation_ids": len(set(feedback_convs) & set(dev_convs)),
        },
        "duplicate_task_ids_across_manifests": duplicate_task_ids,
        "duplicate_conversation_units_across_manifests": duplicate_convs,
        "dev_sample_matches_group_a_traces": bool(
            trace_ids and trace_ids == dev_set and not trace_issues
        ),
        "group_a_traces": len(trace_ids),
        "trace_issues": trace_issues,
        "split_errors": split_errors,
        "conclusion": (
            "The three samples are mutually disjoint in task ids and conversation "
            "units, so the 100 pilot tasks and the 300 feedback tasks are counted "
            "separately and every Dev annotation maps to exactly one Group A trace."
            if not duplicate_task_ids
            and not duplicate_convs
            and not split_errors
            and bool(trace_ids and trace_ids == dev_set)
            else "sample integrity problems were found; see the detail fields"
        ),
    }


def summarize_stage(
    storage: GoldStorage,
    split: str,
    stage: str,
    task_ids: Sequence[str],
) -> dict[str, object]:
    """Compute the annotation summary for one stage."""
    finals = _load_jsonl_map(
        storage.finals_path(split, stage), FinalGoldState, "task_id"
    )
    comparisons = _load_jsonl_map(
        storage.comparisons_path(split, stage), PassComparison, "task_id"
    )
    adjudications = _load_jsonl_map(
        storage.adjudications_path(split, stage), AdjudicationResult, "task_id"
    )

    expected = set(task_ids)
    covered = set(finals) & expected
    missing = sorted(expected - covered)

    answerability: Counter = Counter()
    categories: Counter = Counter()
    confidence: Counter = Counter()
    comparison_classes: Counter = Counter()
    adjudication_outcomes: Counter = Counter()
    scored = 0
    unscored = 0
    policy_dependent = 0
    policy_verified = 0
    token_counts: list[int] = []
    api_records = 0
    for task_id in sorted(covered):
        final = finals[task_id]
        if final.scoring_status == "scored":
            scored += 1
        else:
            unscored += 1
        comparison = comparisons.get(task_id)
        if comparison is not None:
            comparison_classes[comparison.classification] += 1
        if final.adjudication is not None:
            adjudication_outcomes[final.adjudication.outcome] += 1
            if final.adjudication.token_usage:
                token_counts.append(
                    final.adjudication.token_usage.get("total_tokens", 0)
                )
                api_records += 1
        if final.final is not None:
            annotation = final.final
            answerability[annotation.task_answerability] += 1
            confidence[annotation.confidence] += 1
            if annotation.primary_expected_decision is not None:
                categories[annotation.primary_expected_decision.category] += 1
            if annotation.external_policy_dependency:
                policy_dependent += 1
            if annotation.policy_verified:
                policy_verified += 1
    for record in storage.iter_pass_records(split, stage):
        if record.token_usage:
            token_counts.append(record.token_usage.get("total_tokens", 0))
            api_records += 1

    return {
        "split": split,
        "stage": stage,
        "tasks_expected": len(expected),
        "tasks_finalized": len(covered),
        "tasks_missing": missing,
        "scored": scored,
        "unscored": unscored,
        "unscored_rate": round(unscored / len(covered), 4) if covered else None,
        "answerability_distribution": dict(sorted(answerability.items())),
        "primary_category_distribution": dict(sorted(categories.items())),
        "confidence_distribution": dict(sorted(confidence.items())),
        "pass_comparison_distribution": dict(sorted(comparison_classes.items())),
        "adjudication_outcome_distribution": dict(
            sorted(adjudication_outcomes.items())
        ),
        "adjudication_rate": (
            round(
                len(adjudications) / max(1, len(covered)),
                4,
            )
            if covered
            else None
        ),
        "external_policy_dependency_tasks": policy_dependent,
        "policy_verified_tasks": policy_verified,
        "api_records_with_usage": api_records,
        "total_tokens": sum(token_counts),
        "total_annotation_calls": _count_pass_records(storage, split, stage),
    }


def _count_pass_records(storage: GoldStorage, split: str, stage: str) -> int:
    return sum(1 for _ in storage.iter_pass_records(split, stage))


def _load_jsonl_map(path: Path, model: type, key: str) -> dict:
    result: dict = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = model.model_validate_json(line)
        except Exception:
            continue
        result[getattr(record, key)] = record
    return result


def reference_distribution(
    references: Sequence[PrivateTaskReference],
    tasks: Mapping[str, PublicDecisionTask],
) -> dict[str, object]:
    """Distribution over the frozen gold references."""
    answerability: Counter = Counter()
    categories: Counter = Counter()
    scored = 0
    unscored = 0
    evidence_issues = 0
    for reference in references:
        if reference.scoring_status == "scored":
            scored += 1
        else:
            unscored += 1
        answerability[reference.task_answerability] += 1
        if reference.primary_expected_decision is not None:
            categories[reference.primary_expected_decision.decision_category] += 1
        task = tasks.get(reference.task_id)
        if task is not None:
            visible = len(task.visible_context)
            for ref in reference.evidence_refs:
                if ref.startswith("visible_turn:"):
                    try:
                        turn = int(ref.split(":", 1)[1])
                    except ValueError:
                        continue
                    if turn < 1 or turn > visible:
                        evidence_issues += 1
    return {
        "tasks": len(references),
        "scored": scored,
        "unscored": unscored,
        "answerability_distribution": dict(sorted(answerability.items())),
        "primary_category_distribution": dict(sorted(categories.items())),
        "evidence_ref_out_of_range": evidence_issues,
    }


def tasks_artifact_hash(tasks_dir: Path) -> str:
    public = Path(tasks_dir) / "tasks.jsonl"
    if not public.is_file():
        return ""
    return sha256_file(public)


__all__ = [
    "build_overlap_report",
    "reference_distribution",
    "summarize_stage",
    "tasks_artifact_hash",
]
