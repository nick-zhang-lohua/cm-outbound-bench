"""Milestone 1.2 Group A business metrics.

Primary metric is the LLM-Gold Decision Success Rate (DSR): fully correct tasks
over all scored tasks. Partially correct outputs are reported separately and
never enter the DSR numerator. Unscored tasks are excluded from every business
denominator. Deterministic lexical secondary metrics are computed only where the
frozen Gold Label actually provides the necessary information.
"""

from __future__ import annotations

from collections import Counter
from typing import Mapping

from cm_outbound_bench.decisions.schema import (
    AgentDecisionOutput,
    PrivateTaskReference,
)
from cm_outbound_bench.decisions.scoring.schema import (
    OUTCOME_FULLY_CORRECT,
    OUTCOME_INCORRECT,
    OUTCOME_PARTIALLY_CORRECT,
    OUTCOME_UNSCORABLE,
    FinalScore,
)


def _normalize(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


def _clarification_satisfied(reference: PrivateTaskReference, output) -> bool:
    expected = [item for item in reference.expected_clarification if item.strip()]
    if not expected:
        return False
    action = _normalize(output.next_action)
    spoken = _normalize(output.customer_response)
    claims = " ".join(
        _normalize(item) for item in output.required_information if item.strip()
    )
    haystack = " ".join([action, spoken, claims])
    return any(
        _normalize(item) and _normalize(item) in haystack for item in expected
    )


def _step_metrics(reference: PrivateTaskReference, output) -> tuple[float | None, float | None]:
    steps = [item for item in reference.required_steps if item.strip()]
    if not steps:
        return None, None
    action = _normalize(output.next_action)
    spoken = _normalize(output.customer_response)
    claims = " ".join(
        _normalize(item) for item in output.required_information if item.strip()
    )
    evidence = _normalize(output.evidence_summary)
    haystack = " ".join([action, spoken, claims, evidence])
    matches = [1.0 for step in steps if _normalize(step) in haystack]
    recall = sum(matches) / len(steps)
    requested = [
        _normalize(item) for item in output.required_information if item.strip()
    ]
    if not requested:
        precision = 0.0
    else:
        precision = sum(
            1.0
            for claim in requested
            if any(claim in _normalize(step) or _normalize(step) in claim for step in steps)
        ) / len(requested)
    return recall, precision


def compute_metrics(
    *,
    finals: Mapping[str, FinalScore],
    references: Mapping[str, PrivateTaskReference],
    outputs: Mapping[str, AgentDecisionOutput],
    executed_tasks: int,
) -> dict[str, object]:
    """Compute the full metric table with explicit denominators."""
    outcome_counts: Counter = Counter()
    scored = 0
    for task_id, final in finals.items():
        if final.outcome != OUTCOME_UNSCORABLE:
            scored += 1
        outcome_counts[final.outcome] += 1

    fully = outcome_counts.get(OUTCOME_FULLY_CORRECT, 0)
    partial = outcome_counts.get(OUTCOME_PARTIALLY_CORRECT, 0)
    incorrect = outcome_counts.get(OUTCOME_INCORRECT, 0)

    def _rate(numerator: int) -> float | None:
        if scored == 0:
            return None
        return round(numerator / scored, 6)

    critical = sum(
        1
        for final in finals.values()
        if final.outcome != OUTCOME_UNSCORABLE and final.critical_errors
    )

    # Deterministic secondary metrics, computed only where the gold provides data.
    clarification_hits: list[int] = []
    step_recalls: list[float] = []
    step_precisions: list[float] = []
    branch_hits: list[int] = []
    for task_id, reference in references.items():
        final = finals.get(task_id)
        if final is None or final.outcome == OUTCOME_UNSCORABLE:
            continue
        output = outputs.get(task_id)
        if output is None:
            continue
        if reference.expected_clarification:
            clarification_hits.append(
                1 if _clarification_satisfied(reference, output) else 0
            )
        recall, precision = _step_metrics(reference, output)
        if recall is not None:
            step_recalls.append(recall)
        if precision is not None:
            step_precisions.append(precision)
        if reference.expected_branch and reference.branch_conditions:
            haystack = " ".join(
                [
                    _normalize(output.next_action),
                    _normalize(output.customer_response),
                    _normalize(output.evidence_summary),
                ]
            )
            branch_hits.append(
                1 if _normalize(reference.expected_branch) in haystack else 0
            )

    # Required-step *precision* is not defined by this Ground Truth: the gold
    # records required steps as actions ("confirm a date avoiding Friday"), while
    # the agent output exposes requested *information*. A precision denominator
    # would compare two different kinds of object, so the headline metric stays
    # null and the lexical surrogate is reported separately as a diagnostic.
    lexical_precision = (
        round(sum(step_precisions) / len(step_precisions), 6)
        if step_precisions
        else None
    )

    return {
        "executed_tasks": executed_tasks,
        "scored_tasks": scored,
        "unscorable_tasks": outcome_counts.get(OUTCOME_UNSCORABLE, 0),
        "scored_task_coverage": (
            round(scored / executed_tasks, 6) if executed_tasks else None
        ),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "llm_gold_decision_success_rate": _rate(fully),
        "decision_acceptance_rate": _rate(fully + partial),
        "partial_decision_rate": _rate(partial),
        "incorrect_decision_rate": _rate(incorrect),
        "critical_error_rate": _rate(critical),
        "critical_error_tasks": critical,
        "appropriate_clarification_rate": (
            round(sum(clarification_hits) / len(clarification_hits), 6)
            if clarification_hits
            else None
        ),
        "appropriate_clarification_denominator": len(clarification_hits),
        "required_step_recall": (
            round(sum(step_recalls) / len(step_recalls), 6)
            if step_recalls
            else None
        ),
        "required_step_recall_denominator": len(step_recalls),
        "required_step_precision": None,
        "required_step_precision_reason": (
            "frozen_gold_records_steps_as_actions_not_an_information_set"
        ),
        "required_step_precision_lexical_diagnostic": lexical_precision,
        "required_step_precision_lexical_denominator": len(step_precisions),
        "branch_decision_accuracy": (
            round(sum(branch_hits) / len(branch_hits), 6) if branch_hits else None
        ),
        "branch_decision_denominator": len(branch_hits),
        "secondary_metric_method": "lexical_substring_surrogate",
        "secondary_metric_note": (
            "Clarification, required-step and branch metrics are lexical "
            "substring surrogates over free-text Chinese. They understate "
            "semantic compliance (a paraphrase scores zero) and are reported as "
            "lower-bound diagnostics, not as semantic accuracy."
        ),
        "response_quality": None,
        "response_quality_reason": "no_reviewed_human_quality_rubric",
        "policy_verified_decision_success_rate": None,
        "policy_verified_reason": "no_authoritative_china_mobile_source",
        "denominator_note": (
            "Only scored tasks enter business denominators; unscorable tasks are "
            "excluded, and partially correct outputs are reported separately from "
            "the DSR numerator."
        ),
    }


__all__ = ["compute_metrics"]
