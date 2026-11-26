"""Technical and business evaluation for decision-point tasks.

Two evaluations are produced for every execution:

* **technical** - did the request succeed, was the output parseable and valid, how
  long did it take, how many tokens and attempts were used. Always computable and
  never dependent on a label.
* **business** - only computable when a *verified* reference exists. The historical
  agent's next response is an observed reference and is never treated as ground
  truth.

Business scoring keeps two **separate evaluation claims**:

``expert_adjudicated_decision_success_rate``
    An independent business reviewer judged the decision appropriate from the
    visible prefix and approved business knowledge. This is an appropriateness
    claim, not a formal policy-compliance claim.

``policy_verified_decision_success_rate``
    The decision is supported by an official policy or verified business rule with
    an explicit citation.

The two rates are never averaged into one number. ``decision_success`` is retained
as a legacy alias of the expert-tier outcome so an older artifact keeps its
meaning, and every aggregate reports which denominator produced each value.

The metric registry is explicit about what can and cannot be computed, so a report
cannot accidentally present an unavailable metric as a measured one.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from pydantic import Field

from cm_outbound_bench.decisions.annotation import (
    ANSWERABILITY_MORE_INFORMATION,
    TIER_NONE,
    tier_supports_expert,
    tier_supports_policy,
)
from cm_outbound_bench.decisions.schema import (
    AgentDecisionOutput,
    AgentInvocation,
    PrivateTaskReference,
    PublicDecisionTask,
)
from cm_outbound_bench.domain.data_model import DomainModel

#: Evaluator version recorded in every trace and report.
EVALUATOR_VERSION = "0.2.0"

SCORING_SCORED = "scored"
SCORING_CANDIDATE = "candidate"
SCORING_UNSCORED = "unscored"
SCORING_INVALID = "invalid"

METRIC_DECISION_SUCCESS = "decision_success"
METRIC_EXPERT_SUCCESS = "expert_adjudicated_decision_success"
METRIC_POLICY_SUCCESS = "policy_verified_decision_success"
METRIC_BRANCH = "branch_decision_accuracy"
METRIC_STEP_RECALL = "required_step_recall"
METRIC_STEP_PRECISION = "required_step_precision"
METRIC_POLICY_COMPLIANCE = "policy_compliance"
METRIC_CLARIFICATION = "appropriate_clarification"
METRIC_RESPONSE_QUALITY = "response_quality"

#: Per-task metric name -> whether and how this evaluator can compute it.
#:
#: ``computable_*`` means the value exists whenever a reference of that kind is
#: present; ``unavailable`` means no reference of any kind can produce it in this
#: milestone.
METRIC_SUPPORT: dict[str, str] = {
    METRIC_DECISION_SUCCESS: "computable_with_expert_adjudicated_reference",
    METRIC_EXPERT_SUCCESS: "computable_with_expert_adjudicated_reference",
    METRIC_POLICY_SUCCESS: "computable_with_policy_verified_reference",
    METRIC_BRANCH: "computable_with_verified_reference",
    METRIC_STEP_RECALL: "computable_with_verified_reference",
    METRIC_STEP_PRECISION: "computable_with_verified_reference",
    METRIC_POLICY_COMPLIANCE: "computable_with_policy_verified_reference",
    METRIC_CLARIFICATION: "computable_with_expert_adjudicated_reference",
    METRIC_RESPONSE_QUALITY: "computable_with_reviewed_quality_rubric",
}

#: Aggregate reporting names. ``decision_success`` keeps its legacy name; every
#: other metric reports under an explicit, self-describing name.
AGGREGATE_METRIC_NAMES: dict[str, str] = {
    METRIC_DECISION_SUCCESS: "decision_success",
    METRIC_EXPERT_SUCCESS: "expert_adjudicated_decision_success_rate",
    METRIC_POLICY_SUCCESS: "policy_verified_decision_success_rate",
    METRIC_BRANCH: "branch_decision_accuracy",
    METRIC_STEP_RECALL: "required_step_recall",
    METRIC_STEP_PRECISION: "required_step_precision",
    METRIC_POLICY_COMPLIANCE: "policy_compliance",
    METRIC_CLARIFICATION: "appropriate_clarification_rate",
    METRIC_RESPONSE_QUALITY: "response_quality",
}

#: Why a metric has no value. Reported next to ``None`` so an absent metric is
#: never mistaken for a measured zero.
METRIC_UNAVAILABLE_REASONS: dict[str, str] = {
    METRIC_RESPONSE_QUALITY: "no_reviewed_quality_rubric_applied_yet",
    METRIC_POLICY_SUCCESS: "no_policy_verified_reference_in_this_run",
    METRIC_POLICY_COMPLIANCE: "no_policy_verified_reference_in_this_run",
    METRIC_CLARIFICATION: "no_clarification_reference_in_this_run",
}

#: Metrics that never become available in this milestone, whatever the labels say.
STRUCTURALLY_UNAVAILABLE_METRICS: dict[str, str] = {
    "tool_intent_accuracy": "unavailable_no_business_tool_definitions",
    "business_task_completion_rate": "unavailable_no_business_backend",
}

#: Metrics whose absence must be explained in every aggregate report.
UNAVAILABLE_METRICS = tuple(STRUCTURALLY_UNAVAILABLE_METRICS)


class TechnicalEvaluation(DomainModel):
    """Execution-level evaluation. Independent of any business label."""

    request_succeeded: bool = False
    output_parsed: bool = False
    output_valid: bool = False
    missing_required_fields: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    timed_out: bool = False
    http_status: int | None = None
    latency_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    attempts: int = 0
    execution_status: str = "provider_error"


class BusinessEvaluation(DomainModel):
    """Business-level evaluation, or an explicit reason it cannot be scored."""

    scoring_status: str = SCORING_UNSCORED
    reason: str = "no_verified_reference"
    denominator_included: bool = False
    verification_tier: str = TIER_NONE
    metrics: dict[str, float] = Field(default_factory=dict)
    reference_authority: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @property
    def expert_tier_included(self) -> bool:
        return self.denominator_included and tier_supports_expert(
            self.verification_tier
        )

    @property
    def policy_tier_included(self) -> bool:
        return self.denominator_included and tier_supports_policy(
            self.verification_tier
        )


class TaskEvaluation(DomainModel):
    task_id: str
    evaluator_version: str = EVALUATOR_VERSION
    technical: TechnicalEvaluation
    business: BusinessEvaluation


class AggregateMetrics(DomainModel):
    """Aggregate over a run set, with explicit denominators."""

    tasks_total: int = 0
    execution_status_counts: dict[str, int] = Field(default_factory=dict)
    technical: dict[str, float] = Field(default_factory=dict)
    scoring_status_counts: dict[str, int] = Field(default_factory=dict)
    unscored_reason_counts: dict[str, int] = Field(default_factory=dict)
    scored_tasks: int = 0
    unscored_tasks: int = 0
    candidate_tasks: int = 0
    invalid_tasks: int = 0
    scored_fraction: float = 0.0
    expert_tier_tasks: int = 0
    policy_tier_tasks: int = 0
    both_tier_tasks: int = 0
    business_metrics: dict[str, float | None] = Field(default_factory=dict)
    business_metric_denominators: dict[str, int] = Field(default_factory=dict)
    unavailable_metrics: dict[str, str] = Field(default_factory=dict)
    independent_sampling_unit: str = "task"
    notes: list[str] = Field(default_factory=list)


def evaluate_invocation(
    task: PublicDecisionTask,
    reference: PrivateTaskReference,
    invocation: AgentInvocation,
) -> TaskEvaluation:
    """Evaluate one execution against one private reference."""
    technical = _technical_evaluation(invocation)
    if not technical.output_valid or invocation.output is None:
        status = (
            SCORING_INVALID
            if reference.scoring_status == SCORING_INVALID
            else SCORING_UNSCORED
        )
        return TaskEvaluation(
            task_id=task.task_id,
            technical=technical,
            business=BusinessEvaluation(
                scoring_status=status,
                reason="technical_failure_no_business_evaluation",
                denominator_included=False,
                verification_tier=reference.effective_verification_tier,
                reference_authority=reference.historical_next_action_authority,
            ),
        )

    if reference.scoring_status == SCORING_INVALID:
        return TaskEvaluation(
            task_id=task.task_id,
            technical=technical,
            business=BusinessEvaluation(
                scoring_status=SCORING_INVALID,
                reason="reference_marked_invalid",
                denominator_included=False,
                verification_tier=reference.effective_verification_tier,
            ),
        )

    diagnostics = _reference_diagnostics(task, reference, invocation.output)
    if not reference.is_scored:
        # Report the *evaluation* status, not the reference's declared status. A
        # reference can be marked "scored" while still failing an annotation gate
        # (pending review, ambiguous answerability, model-only provenance); calling
        # that outcome "scored" would put an unscorable task in a denominator.
        status = (
            SCORING_CANDIDATE
            if reference.scoring_status == SCORING_CANDIDATE
            else SCORING_UNSCORED
        )
        return TaskEvaluation(
            task_id=task.task_id,
            technical=technical,
            business=BusinessEvaluation(
                scoring_status=status,
                reason=_unscored_reason(reference),
                denominator_included=False,
                verification_tier=reference.effective_verification_tier,
                reference_authority=reference.historical_next_action_authority,
                diagnostics=diagnostics,
            ),
        )

    metrics = _business_metrics(reference, invocation.output)
    if not metrics:
        return TaskEvaluation(
            task_id=task.task_id,
            technical=technical,
            business=BusinessEvaluation(
                scoring_status=SCORING_CANDIDATE,
                reason="verified_reference_did_not_match_the_output_schema",
                denominator_included=False,
                verification_tier=reference.effective_verification_tier,
                reference_authority=reference.historical_next_action_authority,
                diagnostics=diagnostics,
            ),
        )
    return TaskEvaluation(
        task_id=task.task_id,
        technical=technical,
        business=BusinessEvaluation(
            scoring_status=SCORING_SCORED,
            reason="verified_reference_evaluated",
            denominator_included=True,
            verification_tier=reference.effective_verification_tier,
            metrics=metrics,
            reference_authority=reference.historical_next_action_authority,
            diagnostics=diagnostics,
        ),
    )


def _technical_evaluation(invocation: AgentInvocation) -> TechnicalEvaluation:
    missing_fields: list[str] = []
    if invocation.error_code in {
        "missing_required_fields",
        "schema_violation",
        "invalid_required_information",
        "invalid_uncertainty",
    }:
        message = invocation.error_message or ""
        if ":" in message:
            missing_fields = [
                item.strip()
                for item in message.split(":", 1)[1].split(",")
                if item.strip()
            ]
    return TechnicalEvaluation(
        request_succeeded=invocation.status
        in {"success", "invalid_output"},
        output_parsed=invocation.raw_text is not None,
        output_valid=invocation.status == "success" and invocation.output is not None,
        missing_required_fields=missing_fields,
        error_code=invocation.error_code,
        error_message=invocation.error_message,
        timed_out=invocation.status == "timeout",
        http_status=invocation.http_status,
        latency_ms=invocation.latency_ms,
        token_usage=invocation.token_usage,
        attempts=invocation.attempts,
        execution_status=invocation.status,
    )


def _unscored_reason(reference: PrivateTaskReference) -> str:
    if reference.scoring_status == SCORING_CANDIDATE:
        return "candidate_pending_review"
    if reference.scoring_status == SCORING_INVALID:
        return "reference_marked_invalid"
    if reference.annotation_provenance is not None and (
        reference.annotation_provenance.is_llm_only
    ):
        return "annotation_is_llm_proposed_not_verified"
    if reference.annotation_status in {"pending", "in_review"}:
        return "annotation_not_reviewed"
    if not reference.answerability_is_scoreable:
        return "task_marked_ambiguous_or_unscorable"
    if not reference.expected_decisions and not reference.scoring_references:
        return "no_verified_expected_decisions"
    return "no_verified_reference"


def _reference_diagnostics(
    task: PublicDecisionTask,
    reference: PrivateTaskReference,
    output: AgentDecisionOutput,
) -> dict[str, Any]:
    """Recorded observations about the historical reference.

    ``historical_reference_similarity`` is a lexical diagnostic only. It is not a
    correctness score, it never enters a numerator or denominator, and its
    authority is explicitly ``observed_reference_only``.
    """
    historical = reference.historical_next_action_reference
    return {
        "reference_authority": reference.historical_next_action_authority,
        "historical_reference_available": bool(historical),
        "historical_reference_similarity": (
            round(_char_ngram_jaccard(output.customer_response, historical), 4)
            if historical
            else None
        ),
        "historical_reference_is_ground_truth": False,
        "visible_turn_count": len(task.visible_context),
        "hidden_speech_turn_count": reference.hidden_speech_turn_count,
        "task_answerability": reference.task_answerability,
        "verification_tier": reference.effective_verification_tier,
        "expected_clarification": list(reference.expected_clarification),
        "prohibited_steps": list(reference.prohibited_steps),
    }


def _candidate_decisions(reference: PrivateTaskReference) -> list[dict[str, Any]]:
    """Normalize every acceptable decision into one comparable structure.

    Both the Milestone 1 legacy ``ExpectedDecision`` form and the Milestone 1.1
    ``DecisionReference`` form are supported, so an old artifact keeps its meaning
    and a new one gains keywords, branches, alternatives and prohibited steps.
    """
    candidates: list[dict[str, Any]] = []
    for decision in reference.scoring_references:
        candidates.append(
            {
                "decision_id": decision.decision_id,
                "label": decision.label,
                "branch": reference.expected_branch,
                "match_keywords": decision.effective_keywords,
                "required_steps": list(decision.required_steps),
                "prohibited_steps": list(decision.prohibited_steps),
                "rule_ids": list(decision.rule_citations),
            }
        )
    for legacy in reference.expected_decisions:
        candidates.append(
            {
                "decision_id": legacy.decision_id,
                "label": legacy.decision_id,
                "branch": legacy.branch,
                "match_keywords": list(legacy.response_keywords),
                "required_steps": list(legacy.required_steps),
                "prohibited_steps": [],
                "rule_ids": list(legacy.policy_ids),
            }
        )
    return candidates


def _business_metrics(
    reference: PrivateTaskReference,
    output: AgentDecisionOutput,
) -> dict[str, float]:
    """Compute business metrics from verified expected decisions.

    Matching is deterministic lexical containment over normalized strings. This is
    a deliberate, auditable choice: the reference phrases are human-verified, the
    comparison never invents a semantic judgement, and an exact-text match is never
    the criterion — any acceptable decision may satisfy the task.

    A task whose approved answer is "ask for the missing information" is scored on
    whether the agent actually requests that information, not on a decision label.
    """
    expert = reference.has_expert_tier
    policy = reference.has_policy_tier
    clarification_expected = bool(reference.expected_clarification) or (
        reference.task_answerability == ANSWERABILITY_MORE_INFORMATION
    )

    action = _normalize(output.next_action)
    spoken = _normalize(output.customer_response)
    claims = [_normalize(item) for item in output.required_information if item.strip()]
    claim_text = " ".join(claims)
    haystack = " ".join([action, spoken, claim_text])

    clarification_satisfied = _clarification_satisfied(
        reference.expected_clarification, action, spoken, claims
    )

    candidates = _candidate_decisions(reference)
    best: dict[str, float] | None = None
    for candidate in candidates:
        metrics = _score_candidate(candidate, reference, action, spoken, haystack, claims)
        if best is None or _score(metrics) > _score(best):
            best = metrics

    steps = _required_steps(reference)
    metrics: dict[str, float] = {}
    decision_matched = bool(best and best.get("decision_matched"))

    if clarification_expected:
        # The approved answer is to request the missing information.
        success = 1.0 if clarification_satisfied else 0.0
        metrics[METRIC_CLARIFICATION] = 1.0 if clarification_satisfied else 0.0
    else:
        success = 1.0 if decision_matched else 0.0

    if best is not None:
        if best.get("branch_defined"):
            metrics[METRIC_BRANCH] = best.get("branch_matched", 0.0)
        metrics[METRIC_STEP_RECALL] = best.get("step_recall", 1.0)
        precision = best.get("step_precision")
        if precision is not None:
            metrics[METRIC_STEP_PRECISION] = precision

    if expert:
        metrics[METRIC_DECISION_SUCCESS] = success
        metrics[METRIC_EXPERT_SUCCESS] = success

    if policy:
        metrics[METRIC_POLICY_SUCCESS] = 1.0 if decision_matched else 0.0
        compliance = _policy_compliance(reference, output)
        if compliance is not None:
            metrics[METRIC_POLICY_COMPLIANCE] = compliance

    if not metrics:
        return {}
    if steps and METRIC_STEP_RECALL not in metrics:
        metrics[METRIC_STEP_RECALL] = 0.0
    return metrics


def _score_candidate(
    candidate: dict[str, Any],
    reference: PrivateTaskReference,
    action: str,
    spoken: str,
    haystack: str,
    claims: Sequence[str],
) -> dict[str, Any]:
    branch = _normalize(candidate.get("branch") or "")
    branch_defined = bool(branch)
    branch_matched = (not branch_defined) or branch in action or branch in spoken

    keywords = [
        _normalize(item)
        for item in candidate.get("match_keywords", [])
        if item and item.strip()
    ]
    keyword_matched = (not keywords) or any(
        keyword in haystack for keyword in keywords
    )

    steps = _dedupe(
        [
            *[step for step in candidate.get("required_steps", []) if step.strip()],
            *[step for step in reference.required_steps if step.strip()],
        ]
    )
    normalized_steps = [_normalize(step) for step in steps]
    step_matches = [step in haystack for step in normalized_steps if step]
    step_recall = (sum(step_matches) / len(step_matches)) if step_matches else 1.0

    matched_claims = sum(
        1
        for claim in claims
        if any(claim in step or step in claim for step in normalized_steps if step)
    )
    step_precision = (matched_claims / len(claims)) if claims else None
    decision_matched = (
        branch_matched
        and keyword_matched
        and (not normalized_steps or all(step_matches))
    )
    return {
        "branch_defined": branch_defined,
        "branch_matched": 1.0 if branch_matched else 0.0,
        "keyword_matched": 1.0 if keyword_matched else 0.0,
        "step_recall": step_recall,
        "step_precision": step_precision,
        "decision_matched": decision_matched,
    }


def _score(metrics: Mapping[str, Any]) -> float:
    """Rank acceptable alternatives. Only used to pick the best match."""
    return (
        (2.0 if metrics.get("decision_matched") else 0.0)
        + float(metrics.get("branch_matched", 0.0))
        + float(metrics.get("step_recall", 0.0))
    )


def _required_steps(reference: PrivateTaskReference) -> list[str]:
    steps = [step for step in reference.required_steps if step.strip()]
    for decision in reference.scoring_references:
        steps.extend(step for step in decision.required_steps if step.strip())
    return _dedupe(steps)


def _clarification_satisfied(
    expected: Sequence[str],
    action: str,
    spoken: str,
    claims: Sequence[str],
) -> bool:
    """True when the agent asks for the information the reference requires.

    A lexical match against any expected item, in ``required_information``, the
    spoken response or the stated next action, counts. The check is deliberately
    permissive: the annotation establishes *what* must be requested, not the exact
    wording.
    """
    if not expected:
        return False
    claim_text = " ".join(claims)
    for item in expected:
        normalized = _normalize(item)
        if not normalized:
            continue
        if normalized in claim_text or normalized in action or normalized in spoken:
            return True
    return False


def _policy_compliance(
    reference: PrivateTaskReference,
    output: AgentDecisionOutput,
) -> float | None:
    """Return 1.0/0.0 only when a prohibited action is actually documented.

    Without a documented prohibition there is nothing to comply with, so the metric
    is absent rather than assumed to be 1.0. The scan covers the whole agent answer
    — stated action, spoken response, requested information and the evidence
    summary — because announcing a prohibited action is itself non-compliance.
    """
    prohibited = list(reference.prohibited_steps)
    for decision in reference.scoring_references:
        prohibited.extend(decision.prohibited_steps)
    prohibited = [item for item in prohibited if item.strip()]
    if not prohibited:
        return None
    haystack = " ".join(
        [
            _normalize(output.next_action),
            _normalize(output.customer_response),
            _normalize(output.evidence_summary),
            *[_normalize(item) for item in output.required_information],
        ]
    )
    for item in prohibited:
        normalized = _normalize(item)
        if normalized and normalized in haystack:
            return 0.0
    return 1.0


def aggregate_evaluations(
    evaluations: Iterable[TaskEvaluation],
    *,
    output_reviews: Mapping[str, float] | None = None,
    independent_sampling_unit: str = "task",
) -> AggregateMetrics:
    """Aggregate a run set, keeping every denominator explicit.

    ``output_reviews`` maps ``task_id`` to a human response-quality score in
    ``[0, 1]`` produced by the separate blind output-review stage. It is the only
    source of ``response_quality``: an LLM judge is never substituted for it here.
    """
    materialized = list(evaluations)
    total = len(materialized)
    aggregate = AggregateMetrics(
        tasks_total=total, independent_sampling_unit=independent_sampling_unit
    )
    if total == 0:
        aggregate.notes.append("no evaluations to aggregate")
        return aggregate

    status_counts: dict[str, int] = {}
    for evaluation in materialized:
        status = evaluation.technical.execution_status
        status_counts[status] = status_counts.get(status, 0) + 1
    aggregate.execution_status_counts = dict(sorted(status_counts.items()))

    succeeded = sum(1 for item in materialized if item.technical.request_succeeded)
    valid = sum(1 for item in materialized if item.technical.output_valid)
    timed_out = sum(1 for item in materialized if item.technical.timed_out)
    latencies = sorted(item.technical.latency_ms for item in materialized)
    tokens = [
        item.technical.token_usage.get("total_tokens")
        for item in materialized
        if item.technical.token_usage.get("total_tokens") is not None
    ]
    aggregate.technical = {
        "request_success_rate": round(succeeded / total, 4),
        "valid_output_rate": round(valid / total, 4),
        "timeout_rate": round(timed_out / total, 4),
        "latency_ms_p50": float(_quantile(latencies, 0.50)),
        "latency_ms_p90": float(_quantile(latencies, 0.90)),
        "tasks_with_token_usage": float(len(tokens)),
        "total_tokens": float(sum(tokens)) if tokens else 0.0,
    }

    scoring_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for evaluation in materialized:
        status = evaluation.business.scoring_status
        scoring_counts[status] = scoring_counts.get(status, 0) + 1
        if status != SCORING_SCORED:
            reason = evaluation.business.reason
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    aggregate.scoring_status_counts = dict(sorted(scoring_counts.items()))
    aggregate.unscored_reason_counts = dict(sorted(reason_counts.items()))
    aggregate.scored_tasks = scoring_counts.get(SCORING_SCORED, 0)
    aggregate.candidate_tasks = scoring_counts.get(SCORING_CANDIDATE, 0)
    aggregate.invalid_tasks = scoring_counts.get(SCORING_INVALID, 0)
    aggregate.unscored_tasks = total - aggregate.scored_tasks - aggregate.invalid_tasks
    aggregate.scored_fraction = round(aggregate.scored_tasks / total, 4)

    scored = [
        evaluation
        for evaluation in materialized
        if evaluation.business.scoring_status == SCORING_SCORED
    ]
    aggregate.expert_tier_tasks = sum(
        1 for evaluation in scored if evaluation.business.expert_tier_included
    )
    aggregate.policy_tier_tasks = sum(
        1 for evaluation in scored if evaluation.business.policy_tier_included
    )
    aggregate.both_tier_tasks = sum(
        1
        for evaluation in scored
        if evaluation.business.expert_tier_included
        and evaluation.business.policy_tier_included
    )

    for metric in METRIC_SUPPORT:
        name = AGGREGATE_METRIC_NAMES[metric]
        values = [
            evaluation.business.metrics[metric]
            for evaluation in scored
            if metric in evaluation.business.metrics
        ]
        if metric == METRIC_RESPONSE_QUALITY and output_reviews:
            values = [
                float(output_reviews[evaluation.task_id])
                for evaluation in scored
                if evaluation.task_id in output_reviews
            ]
        aggregate.business_metric_denominators[name] = len(values)
        aggregate.business_metrics[name] = (
            round(sum(values) / len(values), 4) if values else None
        )
        if not values:
            aggregate.unavailable_metrics[name] = METRIC_UNAVAILABLE_REASONS.get(
                metric, "no_reference_provided_by_this_run"
            )

    for name, reason in STRUCTURALLY_UNAVAILABLE_METRICS.items():
        aggregate.unavailable_metrics[name] = reason
        aggregate.business_metrics.setdefault(name, None)
        aggregate.business_metric_denominators.setdefault(name, 0)

    if aggregate.scored_tasks == 0:
        aggregate.notes.append(
            "decision_success_rate is not available: zero tasks have a verified "
            "reference. Unscored tasks are excluded from every business "
            "denominator, and the historical agent response is not treated as "
            "ground truth."
        )
    if aggregate.candidate_tasks:
        aggregate.notes.append(
            f"{aggregate.candidate_tasks} task(s) are pending annotation review "
            "and are excluded from business metrics."
        )
    if aggregate.expert_tier_tasks and not aggregate.policy_tier_tasks:
        aggregate.notes.append(
            "Only the expert-adjudicated tier is populated in this run, so "
            "policy_verified_decision_success_rate is absent. The two tiers are "
            "reported separately and are never averaged together."
        )
    aggregate.notes.append(
        "No business backend is executed in this milestone, so business task "
        "completion and real tool execution success are out of scope."
    )
    return aggregate


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _normalize(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def _char_ngram_jaccard(left: str, right: str, size: int = 3) -> float:
    left_set = _ngrams(_normalize(left), size)
    right_set = _ngrams(_normalize(right), size)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _ngrams(text: str, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _quantile(sorted_values: Sequence[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(
        len(sorted_values) - 1,
        max(0, int(round(fraction * (len(sorted_values) - 1)))),
    )
    return sorted_values[index]


__all__ = [
    "AGGREGATE_METRIC_NAMES",
    "EVALUATOR_VERSION",
    "METRIC_BRANCH",
    "METRIC_CLARIFICATION",
    "METRIC_DECISION_SUCCESS",
    "METRIC_EXPERT_SUCCESS",
    "METRIC_POLICY_COMPLIANCE",
    "METRIC_POLICY_SUCCESS",
    "METRIC_RESPONSE_QUALITY",
    "METRIC_STEP_PRECISION",
    "METRIC_STEP_RECALL",
    "METRIC_SUPPORT",
    "METRIC_UNAVAILABLE_REASONS",
    "SCORING_CANDIDATE",
    "SCORING_INVALID",
    "SCORING_SCORED",
    "SCORING_UNSCORED",
    "STRUCTURALLY_UNAVAILABLE_METRICS",
    "UNAVAILABLE_METRICS",
    "AggregateMetrics",
    "BusinessEvaluation",
    "TaskEvaluation",
    "TechnicalEvaluation",
    "aggregate_evaluations",
    "evaluate_invocation",
]
