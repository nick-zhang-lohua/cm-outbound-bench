"""Business evaluation semantics: two tiers, alternatives, clarification, denominators."""

from __future__ import annotations

import pytest

from cm_outbound_bench.decisions.annotation import (
    ANSWERABILITY_AMBIGUOUS,
    ANSWERABILITY_MORE_INFORMATION,
    ANSWERABILITY_SUFFICIENT,
    AUTHORITY_LLM_PROPOSED,
    AnnotationProvenance,
    BusinessRuleCitation,
    DecisionReference,
)
from cm_outbound_bench.decisions.business_report import (
    classify_failures,
    cluster_bootstrap_ci,
)
from cm_outbound_bench.decisions.evaluator import (
    METRIC_CLARIFICATION,
    METRIC_DECISION_SUCCESS,
    METRIC_EXPERT_SUCCESS,
    METRIC_POLICY_COMPLIANCE,
    METRIC_POLICY_SUCCESS,
    METRIC_STEP_RECALL,
    SCORING_CANDIDATE,
    SCORING_SCORED,
    SCORING_UNSCORED,
    aggregate_evaluations,
    evaluate_invocation,
)
from cm_outbound_bench.decisions.schema import (
    AgentDecisionOutput,
    AgentInvocation,
    PrivateTaskReference,
    PublicDecisionTask,
    VisibleTurn,
)

TASK_ID = "TASK_FIXTURE_1"
CONVERSATION_ID = "CALL_FIXTURE_1"


def _task() -> PublicDecisionTask:
    return PublicDecisionTask(
        task_id=TASK_ID,
        visible_context=[
            VisibleTurn(role="historical_agent", content="占位客服开场"),
            VisibleTurn(role="customer", content="占位客户回应"),
        ],
    )


def _provenance(authority: str = "human_primary_annotation") -> AnnotationProvenance:
    return AnnotationProvenance(
        authority=authority,
        annotator_id="reviewer-a",
        model_name="fixture-model" if authority == AUTHORITY_LLM_PROPOSED else "",
    )


def _reference(**overrides) -> PrivateTaskReference:
    values: dict[str, object] = {
        "task_id": TASK_ID,
        "call_id": CONVERSATION_ID,
        "split": "dev",
        "cutoff_record_index": 1,
        "cutoff_seq": 1,
        "cutoff_segment_id": f"{CONVERSATION_ID}:1",
        "cutoff_start_ms": 1000,
        "cutoff_visible_turn_count": 2,
        "hidden_speech_turn_count": 1,
        "historical_next_action_reference": "占位历史客服下一句",
        "historical_next_action_segment_id": f"{CONVERSATION_ID}:2",
        "scoring_status": "unscored",
    }
    values.update(overrides)
    return PrivateTaskReference(**values)


def _expert(*, label: str = "占位决策甲", steps: list[str] | None = None, **overrides):
    values: dict[str, object] = {
        "scoring_status": "scored",
        "annotation_status": "adjudicated",
        "adjudication_status": "adjudicated",
        "task_answerability": ANSWERABILITY_SUFFICIENT,
        "verification_tier": "expert_adjudicated",
        "primary_expected_decision": DecisionReference(
            decision_id="D1",
            label=label,
            decision_family="占位动作族",
            match_keywords=[label],
        ),
        "required_steps": list(steps if steps is not None else []),
        "annotation_provenance": _provenance(),
        "annotator_ids": ["reviewer-a", "reviewer-b"],
        "reviewer_confidence": 0.9,
        "annotation_guideline_version": "annotation-guidelines-v1",
    }
    values.update(overrides)
    return _reference(**values)


def _output(**overrides) -> AgentDecisionOutput:
    values = {
        "customer_response": "占位客户回应建议",
        "next_action": "占位行动",
        "required_information": [],
        "tool_intent": None,
        "uncertainty": None,
        "evidence_summary": "占位证据摘要",
    }
    values.update(overrides)
    return AgentDecisionOutput(**values)


def _invoke(output: AgentDecisionOutput | None = None) -> AgentInvocation:
    return AgentInvocation(
        status="success",
        output=output or _output(),
        raw_text="{}",
        latency_ms=10,
        token_usage={"total_tokens": 20},
        attempts=1,
    )


# ---------------------------------------------------------------------------
# Only adjudicated tasks are scored
# ---------------------------------------------------------------------------


def test_a_pending_annotation_is_not_scored() -> None:
    reference = _expert(annotation_status="pending", adjudication_status="pending")
    evaluation = evaluate_invocation(_task(), reference, _invoke())
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    assert evaluation.business.denominator_included is False
    assert evaluation.business.reason == "annotation_not_reviewed"


def test_a_single_reviewed_annotation_is_scored_but_not_adjudicated() -> None:
    reference = _expert(annotation_status="reviewed", adjudication_status="not_required")
    evaluation = evaluate_invocation(_task(), reference, _invoke(_output(next_action="占位决策甲")))
    assert evaluation.business.scoring_status == SCORING_SCORED
    assert evaluation.business.denominator_included is True


def test_an_llm_proposed_annotation_is_never_scored() -> None:
    reference = _expert(
        annotation_status="adjudicated",
        annotation_provenance=_provenance(AUTHORITY_LLM_PROPOSED),
    )
    evaluation = evaluate_invocation(_task(), reference, _invoke())
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    assert evaluation.business.reason == "annotation_is_llm_proposed_not_verified"


def test_an_ambiguous_task_is_not_scored_even_when_marked_scored() -> None:
    reference = _expert(
        task_answerability=ANSWERABILITY_AMBIGUOUS,
        primary_expected_decision=None,
    )
    evaluation = evaluate_invocation(_task(), reference, _invoke())
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    assert evaluation.business.denominator_included is False


# ---------------------------------------------------------------------------
# Two tiers stay separate
# ---------------------------------------------------------------------------


def test_expert_tier_reports_only_expert_metrics() -> None:
    reference = _expert()
    evaluation = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位决策甲"))
    )
    metrics = evaluation.business.metrics
    assert metrics[METRIC_DECISION_SUCCESS] == 1.0
    assert metrics[METRIC_EXPERT_SUCCESS] == 1.0
    assert METRIC_POLICY_SUCCESS not in metrics
    assert METRIC_POLICY_COMPLIANCE not in metrics
    assert evaluation.business.expert_tier_included is True
    assert evaluation.business.policy_tier_included is False


def test_policy_tier_reports_a_separate_policy_metric() -> None:
    citation = BusinessRuleCitation(
        rule_id="R1",
        source_id="SRC-1",
        statement="占位规则陈述",
        evidence_source="official_policy",
    )
    reference = _expert(
        verification_tier="expert_adjudicated_and_policy_verified",
        rule_citations=[citation],
        prohibited_steps=["占位禁止动作"],
    )
    evaluation = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位决策甲"))
    )
    metrics = evaluation.business.metrics
    assert metrics[METRIC_EXPERT_SUCCESS] == 1.0
    assert metrics[METRIC_POLICY_SUCCESS] == 1.0
    assert metrics[METRIC_POLICY_COMPLIANCE] == 1.0
    assert evaluation.business.policy_tier_included is True


def test_policy_compliance_fails_when_a_prohibited_action_appears() -> None:
    citation = BusinessRuleCitation(
        rule_id="R1",
        source_id="SRC-1",
        statement="占位规则陈述",
        evidence_source="official_policy",
    )
    reference = _expert(
        verification_tier="policy_verified",
        rule_citations=[citation],
        prohibited_steps=["占位禁止动作"],
    )
    evaluation = evaluate_invocation(
        _task(),
        reference,
        _invoke(_output(next_action="占位决策甲", evidence_summary="占位禁止动作")),
    )
    assert evaluation.business.metrics[METRIC_POLICY_COMPLIANCE] == 0.0


def test_the_two_tiers_are_never_averaged_into_one_number() -> None:
    expert = evaluate_invocation(
        _task(), _expert(), _invoke(_output(next_action="占位决策甲"))
    )
    policy_reference = _expert(
        label="占位策略甲",
        verification_tier="policy_verified",
        rule_citations=[
            BusinessRuleCitation(
                rule_id="R1",
                source_id="SRC-1",
                statement="占位规则陈述",
                evidence_source="official_policy",
            )
        ],
    )
    policy = evaluate_invocation(
        _task(), policy_reference, _invoke(_output(next_action="占位其它"))
    )
    aggregate = aggregate_evaluations([expert, policy])
    assert aggregate.scored_tasks == 2
    assert aggregate.expert_tier_tasks == 1
    assert aggregate.policy_tier_tasks == 1
    assert (
        aggregate.business_metric_denominators[
            "expert_adjudicated_decision_success_rate"
        ]
        == 1
    )
    assert (
        aggregate.business_metric_denominators["policy_verified_decision_success_rate"]
        == 1
    )
    assert (
        aggregate.business_metrics["expert_adjudicated_decision_success_rate"] == 1.0
    )
    assert (
        aggregate.business_metrics["policy_verified_decision_success_rate"] == 0.0
    )


# ---------------------------------------------------------------------------
# Multiple acceptable decisions
# ---------------------------------------------------------------------------


def test_an_acceptable_alternative_counts_as_success() -> None:
    reference = _expert()
    reference.acceptable_alternative_decisions = [
        DecisionReference(
            decision_id="D2",
            label="占位备选决策",
            decision_family="占位备选族",
            match_keywords=["占位备选决策"],
        )
    ]
    primary = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位决策甲"))
    )
    alternative = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位备选决策"))
    )
    assert primary.business.metrics[METRIC_EXPERT_SUCCESS] == 1.0
    assert alternative.business.metrics[METRIC_EXPERT_SUCCESS] == 1.0


def test_exact_wording_is_not_the_criterion() -> None:
    reference = _expert(steps=["占位必需步骤"])
    evaluation = evaluate_invocation(
        _task(),
        reference,
        _invoke(
            _output(
                next_action="占位决策甲",
                customer_response="占位完全不同的措辞",
                required_information=["占位必需步骤"],
            )
        ),
    )
    assert evaluation.business.metrics[METRIC_EXPERT_SUCCESS] == 1.0
    assert evaluation.business.metrics[METRIC_STEP_RECALL] == 1.0


def test_a_missing_required_step_fails_the_decision() -> None:
    reference = _expert(steps=["占位必需步骤"])
    evaluation = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位决策甲"))
    )
    assert evaluation.business.metrics[METRIC_EXPERT_SUCCESS] == 0.0
    assert evaluation.business.metrics[METRIC_STEP_RECALL] == 0.0


# ---------------------------------------------------------------------------
# Clarification
# ---------------------------------------------------------------------------


def test_an_appropriate_clarification_counts_as_success() -> None:
    reference = _expert(
        task_answerability=ANSWERABILITY_MORE_INFORMATION,
        primary_expected_decision=None,
        expected_clarification=["占位缺失事实"],
    )
    evaluation = evaluate_invocation(
        _task(),
        reference,
        _invoke(_output(next_action="占位询问", required_information=["占位缺失事实"])),
    )
    assert evaluation.business.scoring_status == SCORING_SCORED
    assert evaluation.business.metrics[METRIC_CLARIFICATION] == 1.0
    assert evaluation.business.metrics[METRIC_EXPERT_SUCCESS] == 1.0


def test_a_missed_clarification_fails_the_task() -> None:
    reference = _expert(
        task_answerability=ANSWERABILITY_MORE_INFORMATION,
        primary_expected_decision=None,
        expected_clarification=["占位缺失事实"],
    )
    evaluation = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位直接办理"))
    )
    assert evaluation.business.metrics[METRIC_CLARIFICATION] == 0.0
    assert evaluation.business.metrics[METRIC_EXPERT_SUCCESS] == 0.0


# ---------------------------------------------------------------------------
# Denominators
# ---------------------------------------------------------------------------


def test_unscored_tasks_are_excluded_from_every_business_denominator() -> None:
    scored = evaluate_invocation(
        _task(), _expert(), _invoke(_output(next_action="占位决策甲"))
    )
    unscored = evaluate_invocation(_task(), _reference(), _invoke())
    aggregate = aggregate_evaluations([scored, unscored])
    assert aggregate.tasks_total == 2
    assert aggregate.scored_tasks == 1
    assert aggregate.unscored_tasks == 1
    assert aggregate.scored_fraction == 0.5
    assert aggregate.business_metric_denominators["decision_success"] == 1
    assert aggregate.business_metrics["decision_success"] == 1.0
    assert aggregate.unscored_reason_counts


def test_zero_scored_tasks_reports_not_available_not_zero() -> None:
    aggregate = aggregate_evaluations(
        [evaluate_invocation(_task(), _reference(), _invoke()) for _ in range(3)]
    )
    assert aggregate.scored_tasks == 0
    assert aggregate.business_metrics["decision_success"] is None
    assert aggregate.business_metrics["expert_adjudicated_decision_success_rate"] is None
    assert "policy_compliance" in aggregate.unavailable_metrics
    assert aggregate.scored_fraction == 0.0
    assert any("not available" in note for note in aggregate.notes)


def test_technical_failure_is_unscored_regardless_of_the_reference() -> None:
    failure = AgentInvocation(
        status="provider_error",
        error_code="http_error",
        error_message="provider returned HTTP 500",
        latency_ms=40,
        attempts=2,
    )
    evaluation = evaluate_invocation(_task(), _expert(), failure)
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    assert evaluation.business.reason == "technical_failure_no_business_evaluation"
    assert evaluation.business.denominator_included is False


def test_structural_metrics_are_reported_as_unavailable() -> None:
    aggregate = aggregate_evaluations(
        [evaluate_invocation(_task(), _expert(), _invoke(_output(next_action="占位决策甲")))]
    )
    assert aggregate.unavailable_metrics["tool_intent_accuracy"]
    assert aggregate.unavailable_metrics["business_task_completion_rate"]
    assert aggregate.business_metrics["tool_intent_accuracy"] is None


# ---------------------------------------------------------------------------
# Historical behaviour is never ground truth
# ---------------------------------------------------------------------------


def test_historical_reference_never_becomes_a_label() -> None:
    evaluation = evaluate_invocation(
        _task(),
        _reference(),
        _invoke(_output(customer_response="占位历史客服下一句")),
    )
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    diagnostics = evaluation.business.diagnostics
    assert diagnostics["historical_reference_is_ground_truth"] is False
    assert diagnostics["reference_authority"] == "observed_reference_only"
    assert diagnostics["historical_reference_similarity"] is not None
    assert "historical_reference_similarity" not in evaluation.business.metrics


def test_a_matching_historical_response_scores_nothing() -> None:
    reference = _reference(historical_next_action_reference="占位历史客服下一句")
    evaluation = evaluate_invocation(
        _task(),
        reference,
        _invoke(
            _output(
                customer_response="占位历史客服下一句",
                next_action="占位历史客服下一句",
            )
        ),
    )
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    assert evaluation.business.metrics == {}


def test_a_candidate_annotation_is_excluded_but_visible() -> None:
    reference = _expert(annotation_status="reviewed")
    reference.scoring_status = "candidate"
    evaluation = evaluate_invocation(_task(), reference, _invoke())
    assert evaluation.business.scoring_status == SCORING_CANDIDATE
    assert evaluation.business.denominator_included is False
    assert evaluation.business.reason == "candidate_pending_review"


# ---------------------------------------------------------------------------
# Statistics and error analysis
# ---------------------------------------------------------------------------


def test_cluster_bootstrap_reports_no_interval_for_a_single_cluster() -> None:
    interval = cluster_bootstrap_ci({"one": [1.0, 0.0]}, metric="decision_success")
    assert interval.point_estimate == 0.5
    assert interval.lower is None
    assert "single conversation" in interval.note


def test_cluster_bootstrap_is_reproducible_and_bounded() -> None:
    values = {f"cluster-{index}": [float(index % 2)] for index in range(10)}
    first = cluster_bootstrap_ci(values, metric="decision_success", resamples=200)
    second = cluster_bootstrap_ci(values, metric="decision_success", resamples=200)
    assert first.lower == second.lower
    assert first.upper == second.upper
    assert 0.0 <= first.lower <= first.point_estimate <= first.upper <= 1.0


def test_failure_classification_separates_no_review_from_a_poor_review() -> None:
    reference = _expert(steps=["占位必需步骤"])
    evaluation = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位决策甲"))
    )
    references = {TASK_ID: reference}
    without_review = classify_failures([evaluation], references)
    with_review = classify_failures([evaluation], references, output_reviews={TASK_ID: 0.1})
    assert without_review.counts["poor_customer_response"] == 0
    assert with_review.counts["poor_customer_response"] == 1
    assert any("not reviewed" in note for note in without_review.notes)


def test_failure_classification_flags_wrong_branch() -> None:
    reference = _expert()
    reference.expected_branch = "占位分支甲"
    evaluation = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位完全不同的行动"))
    )
    analysis = classify_failures([evaluation], {TASK_ID: reference})
    assert analysis.counts["wrong_branch"] == 1


@pytest.mark.parametrize("tier", ["expert_adjudicated", "policy_verified"])
def test_a_resolved_reference_is_scored_for_both_tiers(tier: str) -> None:
    reference = _expert(verification_tier=tier)
    evaluation = evaluate_invocation(
        _task(), reference, _invoke(_output(next_action="占位决策甲"))
    )
    assert evaluation.business.scoring_status == SCORING_SCORED
    assert evaluation.business.verification_tier == tier
