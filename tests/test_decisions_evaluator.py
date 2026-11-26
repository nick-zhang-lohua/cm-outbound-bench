"""Evaluator tests, including the scored/unscored denominator boundary."""

from __future__ import annotations

from cm_outbound_bench.decisions.evaluator import (
    EVALUATOR_VERSION,
    SCORING_SCORED,
    SCORING_UNSCORED,
    aggregate_evaluations,
    evaluate_invocation,
)
from cm_outbound_bench.decisions.schema import (
    AgentDecisionOutput,
    AgentInvocation,
    ExpectedDecision,
    PrivateTaskReference,
    PublicDecisionTask,
    VisibleTurn,
)


def _task() -> PublicDecisionTask:
    return PublicDecisionTask(
        task_id="TASK_FIXTURE_1",
        visible_context=[
            VisibleTurn(role="historical_agent", content="占位客服开场"),
            VisibleTurn(role="customer", content="占位客户回应"),
        ],
    )


def _reference(**overrides) -> PrivateTaskReference:
    values = {
        "task_id": "TASK_FIXTURE_1",
        "call_id": "CALL_FIXTURE_1",
        "split": "dev",
        "cutoff_record_index": 1,
        "cutoff_seq": 1,
        "cutoff_segment_id": "CALL_FIXTURE_1:1",
        "cutoff_start_ms": 1000,
        "cutoff_visible_turn_count": 2,
        "hidden_speech_turn_count": 1,
        "historical_next_action_reference": "占位历史客服下一句",
        "historical_next_action_segment_id": "CALL_FIXTURE_1:2",
        "scoring_status": "unscored",
    }
    values.update(overrides)
    return PrivateTaskReference(**values)


def _output(**overrides) -> AgentDecisionOutput:
    values = {
        "customer_response": "占位客户回应建议",
        "next_action": "占位下一步动作",
        "required_information": [],
        "tool_intent": None,
        "uncertainty": None,
        "evidence_summary": "占位证据摘要",
    }
    values.update(overrides)
    return AgentDecisionOutput(**values)


def _success(output: AgentDecisionOutput | None = None) -> AgentInvocation:
    return AgentInvocation(
        status="success",
        output=output or _output(),
        raw_text="{}",
        latency_ms=12,
        token_usage={"total_tokens": 34},
        attempts=1,
    )


# ---------------------------------------------------------------------------
# evaluator
# ---------------------------------------------------------------------------


def test_unscored_task_is_not_counted_in_the_denominator() -> None:
    evaluation = evaluate_invocation(_task(), _reference(), _success())
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    assert evaluation.business.denominator_included is False
    assert evaluation.business.metrics == {}
    assert evaluation.technical.output_valid is True


def test_historical_response_is_a_reference_only() -> None:
    evaluation = evaluate_invocation(_task(), _reference(), _success())
    diagnostics = evaluation.business.diagnostics
    assert diagnostics["reference_authority"] == "observed_reference_only"
    assert diagnostics["historical_reference_is_ground_truth"] is False
    assert diagnostics["historical_reference_available"] is True
    assert "historical_reference_similarity" in diagnostics
    assert "historical_reference_similarity" not in evaluation.business.metrics


def test_technical_evaluation_is_independent_of_labels() -> None:
    failure = AgentInvocation(
        status="provider_error",
        error_code="http_error",
        error_message="provider returned HTTP 500",
        latency_ms=99,
        attempts=2,
    )
    evaluation = evaluate_invocation(_task(), _reference(), failure)
    assert evaluation.technical.request_succeeded is False
    assert evaluation.technical.output_valid is False
    assert evaluation.technical.execution_status == "provider_error"
    assert evaluation.business.scoring_status == SCORING_UNSCORED
    assert evaluation.business.reason == "technical_failure_no_business_evaluation"


def test_scored_reference_produces_business_metrics() -> None:
    reference = _reference(
        scoring_status="scored",
        annotation_status="reviewed",
        expected_decisions=[
            ExpectedDecision(
                decision_id="D1",
                branch="办理",
                required_steps=["确认身份"],
                policy_ids=["P1"],
            )
        ],
    )
    output = _output(
        next_action="先确认身份，再为客户办理",
        customer_response="我先为您确认身份，然后办理",
        required_information=["确认身份"],
    )
    evaluation = evaluate_invocation(_task(), reference, _success(output))
    assert evaluation.business.scoring_status == SCORING_SCORED
    assert evaluation.business.denominator_included is True
    assert evaluation.business.metrics["decision_success"] == 1.0
    assert evaluation.business.metrics["branch_decision_accuracy"] == 1.0
    assert evaluation.business.metrics["required_step_recall"] == 1.0
    assert evaluation.business.metrics["required_step_precision"] == 1.0


def test_scored_reference_can_fail_the_decision() -> None:
    reference = _reference(
        scoring_status="scored",
        annotation_status="reviewed",
        expected_decisions=[ExpectedDecision(decision_id="D1", branch="转人工")],
    )
    evaluation = evaluate_invocation(
        _task(), reference, _success(_output(next_action="直接挂断"))
    )
    assert evaluation.business.metrics["decision_success"] == 0.0
    assert evaluation.business.metrics["branch_decision_accuracy"] == 0.0


def test_aggregate_reports_not_available_with_zero_scored_tasks() -> None:
    evaluations = [
        evaluate_invocation(_task(), _reference(), _success()),
        evaluate_invocation(_task(), _reference(), _success()),
    ]
    aggregate = aggregate_evaluations(evaluations)
    assert aggregate.tasks_total == 2
    assert aggregate.scored_tasks == 0
    assert aggregate.scored_fraction == 0.0
    assert aggregate.business_metrics["decision_success"] is None
    assert aggregate.business_metric_denominators["decision_success"] == 0
    assert any("not available" in note for note in aggregate.notes)
    assert "policy_compliance" in aggregate.unavailable_metrics


def test_aggregate_uses_only_scored_tasks_in_the_denominator() -> None:
    scored_reference = _reference(
        task_id="TASK_FIXTURE_1",
        scoring_status="scored",
        annotation_status="reviewed",
        expected_decisions=[ExpectedDecision(decision_id="D1", branch="办理")],
    )
    scored = evaluate_invocation(
        _task(), scored_reference, _success(_output(next_action="办理"))
    )
    unscored = evaluate_invocation(_task(), _reference(), _success())
    aggregate = aggregate_evaluations([scored, unscored])
    assert aggregate.scored_tasks == 1
    assert aggregate.unscored_tasks == 1
    assert aggregate.scored_fraction == 0.5
    assert aggregate.business_metric_denominators["decision_success"] == 1
    assert aggregate.business_metrics["decision_success"] == 1.0


def test_evaluator_version_is_recorded_in_the_evaluation() -> None:
    evaluation = evaluate_invocation(_task(), _reference(), _success())
    # The version itself moved from 0.1.0 to 0.2.0 in Milestone 1.1 because the
    # evaluation semantics changed (two-tier business metrics). The contract this
    # test protects is that every evaluation records the exact evaluator version.
    assert evaluation.evaluator_version == EVALUATOR_VERSION == "0.2.0"
    assert evaluation.technical.latency_ms == 12
    assert evaluation.technical.token_usage["total_tokens"] == 34
