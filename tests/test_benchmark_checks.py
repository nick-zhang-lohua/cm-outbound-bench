"""Stage 10: evaluator check applicability and relevance."""

from __future__ import annotations

from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.evaluator import CheckCategory, CheckStatus


def _checks(task_id: str) -> dict[str, object]:
    outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
    return {check.name: check for check in outcome.evaluation.checks}


def test_identity_verification_is_required_where_a_protected_tool_is_allowed() -> None:
    checks = _checks("d03_bill_explanation")
    check = checks["identity_verification"]
    assert check.status == CheckStatus.REQUIRED.value
    assert check.passed is True


def test_identity_verification_is_not_applicable_without_a_protected_tool() -> None:
    checks = _checks("d02_direct_refusal_do_not_call")
    check = checks["identity_verification"]
    assert check.status == CheckStatus.NOT_APPLICABLE.value
    assert check.passed is True


def test_task_level_identity_configuration_overrides_the_default() -> None:
    task = find_task("d03_bill_explanation")
    criteria = task.success_criteria.model_copy(
        update={"require_identity_verification": False}
    )
    relaxed = task.model_copy(update={"success_criteria": criteria})
    outcome = execute_task(relaxed, agent_factory=BenchmarkDemoAgent)
    check = next(
        item for item in outcome.evaluation.checks if item.name == "identity_verification"
    )
    assert check.status == CheckStatus.NOT_APPLICABLE.value

    required = task.model_copy(
        update={
            "success_criteria": task.success_criteria.model_copy(
                update={"require_identity_verification": True}
            )
        }
    )
    assert required.requires_identity_verification() is True


def test_vacuous_conditional_checks_are_marked_not_applicable() -> None:
    # A bill explanation never recharges and never requests a cancellation.
    bill = _checks("d03_bill_explanation")
    assert bill["d03_recharge_consistency"].status == (
        CheckStatus.NOT_APPLICABLE.value
    )
    assert bill["d03_recharge_consent"].status == CheckStatus.NOT_APPLICABLE.value
    assert bill["no_marketing_after_opt_out"].status == (
        CheckStatus.NOT_APPLICABLE.value
    )
    assert bill["sms_after_enrollment"].status == CheckStatus.NOT_APPLICABLE.value
    assert bill["offer_cancellation_consent"].status == (
        CheckStatus.NOT_APPLICABLE.value
    )


def test_decline_task_marks_disclosure_checks_not_applicable() -> None:
    checks = _checks("d02_hesitation_then_decline")
    assert checks["d02_offer_terms_disclosed"].status == (
        CheckStatus.NOT_APPLICABLE.value
    )
    assert checks["d02_eligibility_before_enrollment"].status == (
        CheckStatus.NOT_APPLICABLE.value
    )
    # The offer pack stays load-bearing for the inactive-offer rule.
    assert checks["d02_offer_active_at_enrollment"].status == (
        CheckStatus.APPLICABLE.value
    )


def test_d04_cancellation_check_is_not_applicable_without_a_cancellation() -> None:
    checks = _checks("d04_plan_change_blocked_by_commitment")
    assert checks["d04_cancellation_applied"].status == CheckStatus.NOT_APPLICABLE.value
    assert checks["d04_no_noop_change"].status == CheckStatus.NOT_APPLICABLE.value


def test_d05_transfer_condition_check_is_not_applicable_without_a_transfer() -> None:
    checks = _checks("d05_ownership_transfer_conditions")
    assert checks["d05_transfer_conditions"].status == CheckStatus.NOT_APPLICABLE.value
    assert checks["d05_identity_information_retrieved"].status == (
        CheckStatus.APPLICABLE.value
    )


def test_d06_duplicate_check_needs_two_notifications() -> None:
    checks = _checks("d06_enrollment_confirmation_notification")
    assert checks["d06_no_duplicate_notification"].status == (
        CheckStatus.NOT_APPLICABLE.value
    )
    assert checks["d06_confirmation_channel_used_once"].status == (
        CheckStatus.APPLICABLE.value
    )


def test_enrollment_checks_are_load_bearing_when_an_enrollment_occurs() -> None:
    checks = _checks("d02_offer_benefit_inquiry_accept")
    for name in (
        "consent_before_mutation",
        "sms_after_enrollment",
        "d02_eligibility_before_enrollment",
        "d02_offer_terms_disclosed",
    ):
        assert checks[name].status != CheckStatus.NOT_APPLICABLE.value, name
        assert checks[name].passed, name


def test_not_applicable_checks_do_not_count_toward_success_or_completion() -> None:
    outcome = execute_task(
        find_task("d03_bill_explanation"), agent_factory=BenchmarkDemoAgent
    )
    evaluation = outcome.evaluation
    applicable = [
        check
        for check in evaluation.checks
        if check.status != CheckStatus.NOT_APPLICABLE.value
    ]
    assert evaluation.success
    assert len(applicable) < len(evaluation.checks)
    assert evaluation.partial_completion == round(
        sum(1 for check in applicable if check.passed) / len(applicable), 4
    )
    assert set(evaluation.not_applicable_checks) == {
        check.name
        for check in evaluation.checks
        if check.status == CheckStatus.NOT_APPLICABLE.value
    }


def test_dimensions_separate_state_tool_policy_answer_and_domain() -> None:
    outcome = execute_task(
        find_task("d02_offer_benefit_inquiry_accept"), agent_factory=BenchmarkDemoAgent
    )
    dimensions = outcome.evaluation.dimensions
    for category in CheckCategory:
        if category is CheckCategory.LIFECYCLE:
            continue
        assert category.value in dimensions, category
    for name, bucket in dimensions.items():
        assert bucket["total"] == (
            bucket["applicable"] + bucket["not_applicable"]
        ), name
        assert bucket["applicable"] == bucket["passed"] + bucket["failed"], name


def test_every_task_reports_each_dimension_with_a_count() -> None:
    for task in all_tasks():
        outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
        assert outcome.evaluation.dimensions, task.task_id
        assert outcome.evaluation.dimensions["policy"]["total"] >= 1, task.task_id
        assert outcome.evaluation.dimensions["state"]["total"] >= 1, task.task_id
        assert outcome.evaluation.dimensions["answer"]["total"] >= 1, task.task_id


def test_a_failing_applicable_check_makes_the_task_fail() -> None:
    class IdleAgent:
        def act(self, context):
            from cm_outbound_bench.agent import FinishAction

            return FinishAction(reason="nothing")

    outcome = execute_task(
        find_task("d05_ownership_transfer_conditions"), agent_factory=IdleAgent
    )
    assert not outcome.evaluation.success
    assert outcome.evaluation.dimensions["answer"]["failed"] >= 1
