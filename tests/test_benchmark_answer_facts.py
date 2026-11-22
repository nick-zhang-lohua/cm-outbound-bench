"""Stage 4: deterministic answer-fact correctness.

These tests cover the audit's central validity defect: an agent that calls the
right tools, receives the true business values and then tells the customer the
opposite must no longer pass an information task.
"""

from __future__ import annotations

import pytest

from cm_outbound_bench.agent import AgentContext, FinishAction, SayAction
from cm_outbound_bench.benchmark.answer_facts import (
    evaluate_forbidden_fact,
    evaluate_required_fact,
    find_affirmed_occurrences,
    normalise_text,
)
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.tasks import ForbiddenFact, RequiredFact
from cm_outbound_bench.evaluator import CheckCategory
from cm_outbound_bench.runner import RunResult

#: The eight information / negative-outcome tasks the audit found to be
#: satisfiable with an inverted final statement.
INFORMATION_TASKS = (
    "d01_plan_content_explanation",
    "d02_hesitation_then_decline",
    "d02_eligibility_ineligible_explanation",
    "d02_direct_refusal_do_not_call",
    "d03_bill_explanation",
    "d04_plan_change_blocked_by_commitment",
    "d05_ownership_transfer_conditions",
    "d05_real_name_not_verified",
)

#: Deliberately false closing statements, one per information task. They invert
#: the business fact the tools returned.
FALSE_STATEMENTS = {
    "d01_plan_content_explanation": "您的套餐月费只要 1 元，包含无限流量和无限通话。",
    "d02_hesitation_then_decline": "已经为您成功办理该活动，稍后生效。",
    "d02_eligibility_ineligible_explanation": "完全符合条件，已经为您成功办理。",
    "d02_direct_refusal_do_not_call": "已加入免打扰，同时也帮您开通了活动。",
    "d03_bill_explanation": "账单完全正常，没有任何额外费用，也没有欠费。",
    "d04_plan_change_blocked_by_commitment": "变更已成功提交，没有合约限制。",
    "d05_ownership_transfer_conditions": "可以直接在电话里完成过户，现在就帮您办理。",
    "d05_real_name_not_verified": "实名已通过，没有欠费，随时可以过户。",
}

#: The tools each lying agent must call first so the episode has true tool
#: results in its trace before the false statement.
TOOL_SEQUENCE = {
    "d01_plan_content_explanation": [
        ("verify_customer_identity", {"identity_token": "TEST_IDENTITY_C201"}),
        ("list_plans", {}),
        ("get_plan_details", {"plan_id": "PLAN_S"}),
    ],
    "d02_hesitation_then_decline": [
        ("get_offer_details", {"offer_id": "OFFER_D02_002"}),
        ("verify_customer_identity", {"identity_token": "TEST_IDENTITY_C102"}),
        (
            "check_offer_eligibility",
            {"offer_id": "OFFER_D02_002"},
        ),
    ],
    "d02_eligibility_ineligible_explanation": [
        ("get_offer_details", {"offer_id": "OFFER_D02_001"}),
        ("verify_customer_identity", {"identity_token": "TEST_IDENTITY_C103"}),
        ("check_offer_eligibility", {"offer_id": "OFFER_D02_001"}),
    ],
    "d02_direct_refusal_do_not_call": [
        ("get_offer_details", {"offer_id": "OFFER_D02_001"}),
        ("register_do_not_call", {}),
    ],
    "d03_bill_explanation": [
        ("verify_customer_identity", {"identity_token": "TEST_IDENTITY_C301"}),
        ("get_billing_summary", {}),
        ("list_charges", {}),
    ],
    "d04_plan_change_blocked_by_commitment": [
        ("verify_customer_identity", {"identity_token": "TEST_IDENTITY_C402"}),
        ("get_subscription_status", {}),
        ("check_plan_change_eligibility", {"to_plan_id": "PLAN_M"}),
    ],
    "d05_ownership_transfer_conditions": [
        ("verify_customer_identity", {"identity_token": "TEST_IDENTITY_C501"}),
        ("get_identity_status", {}),
        ("check_ownership_transfer_conditions", {}),
    ],
    "d05_real_name_not_verified": [
        ("verify_customer_identity", {"identity_token": "TEST_IDENTITY_C502"}),
        ("get_identity_status", {}),
        ("check_ownership_transfer_conditions", {}),
    ],
}


class LyingAgent:
    """Calls the required tools, then states the opposite of the tool results."""

    def __init__(self, task_id: str) -> None:
        from cm_outbound_bench.agent import CallToolAction

        self._calls = list(TOOL_SEQUENCE[task_id])
        self._statement = FALSE_STATEMENTS[task_id]
        self._index = 0
        self._said = False
        self._CallToolAction = CallToolAction

    def act(self, context: AgentContext):
        if self._index < len(self._calls):
            tool_name, extra = self._calls[self._index]
            self._index += 1
            arguments = {"phone_number_id": context.task.phone_number_id}
            arguments.update(extra)
            if tool_name == "list_plans":
                arguments = {}
            return self._CallToolAction(tool_name=tool_name, arguments=arguments)
        if not self._said:
            self._said = True
            return SayAction(message=self._statement)
        return FinishAction(reason="lie delivered")


def _run_with(task_id: str, agent_factory) -> tuple[RunResult, object]:
    outcome = execute_task(find_task(task_id), agent_factory=agent_factory)
    return outcome.run, outcome.evaluation


def test_lying_agent_fails_all_eight_information_tasks() -> None:
    failures: list[str] = []
    for task_id in INFORMATION_TASKS:
        run, evaluation = _run_with(task_id, lambda task_id=task_id: LyingAgent(task_id))
        assert run.tool_trace, task_id
        answer_failures = [
            check.name
            for check in evaluation.checks
            if check.category == CheckCategory.ANSWER.value and not check.passed
        ]
        if evaluation.success:
            failures.append(f"{task_id}: still PASSED")
        elif not answer_failures:
            failures.append(f"{task_id}: failed without an answer-fact failure")
    assert failures == []


def test_lying_agent_failure_is_reported_in_the_answer_dimension() -> None:
    _, evaluation = _run_with(
        "d03_bill_explanation", lambda: LyingAgent("d03_bill_explanation")
    )
    assert "answer" in evaluation.dimensions
    assert evaluation.dimensions["answer"]["failed"] >= 1
    assert any(
        violation.startswith("answer_fact_") for violation in evaluation.policy_violations
    )
    assert evaluation.answer_fact_results
    assert any(not item["passed"] for item in evaluation.answer_fact_results)


def test_state_tool_and_policy_dimensions_still_pass_for_the_lying_agent() -> None:
    """The answer layer is additive: it must not weaken the other dimensions."""
    _, evaluation = _run_with(
        "d05_ownership_transfer_conditions",
        lambda: LyingAgent("d05_ownership_transfer_conditions"),
    )
    assert evaluation.dimensions["state"]["failed"] == 0
    assert evaluation.dimensions["answer"]["failed"] >= 1


def test_correct_factual_answer_passes() -> None:
    for task_id in INFORMATION_TASKS:
        outcome = execute_task(
            find_task(task_id), agent_factory=BenchmarkDemoAgent
        )
        answer_failures = [
            check.name
            for check in outcome.evaluation.checks
            if check.category == CheckCategory.ANSWER.value and not check.passed
        ]
        assert outcome.evaluation.success, task_id
        assert answer_failures == [], (task_id, answer_failures)


def test_acceptable_paraphrase_passes() -> None:
    fact = RequiredFact(
        fact_id="fee",
        accepted_patterns=["29 CNY", "29 元", "29元"],
    )
    for text in ("月费 29 元。", "每月 29CNY", "费用是２９元"):
        assert evaluate_required_fact(fact, normalise_text(text)).passed, text


def test_markdown_emphasis_is_normalised_away() -> None:
    assert normalise_text("The status is **SENT**.") == normalise_text(
        "The status is SENT."
    )
    assert normalise_text("**Enrollment:** Successful") == normalise_text(
        "Enrollment: Successful"
    )
    assert normalise_text("__SENT__ and `SENT`") == normalise_text("SENT and SENT")


def test_correct_negative_statement_passes() -> None:
    fact = RequiredFact(
        fact_id="no_renewal",
        accepted_patterns=["不自动续订"],
    )
    assert evaluate_required_fact(fact, normalise_text("到期后不自动续订。")).passed

    arrears = RequiredFact(
        fact_id="no_arrears",
        accepted_patterns=["没有欠费", "欠费 0.00"],
    )
    assert evaluate_required_fact(arrears, normalise_text("您没有欠费。")).passed


def test_negated_required_statement_fails() -> None:
    fact = RequiredFact(fact_id="fee", accepted_patterns=["29 元"])
    assert not evaluate_required_fact(fact, normalise_text("月费不是 29 元。")).passed
    assert not evaluate_required_fact(fact, normalise_text("月费并非 29 元。")).passed


def test_missing_required_information_fails() -> None:
    fact = RequiredFact(fact_id="fee", accepted_patterns=["29 元"])
    outcome = evaluate_required_fact(fact, normalise_text("感谢接听，再见。"))
    assert not outcome.passed
    assert "was not stated" in outcome.detail


def test_no_factual_assertion_fails_a_required_fact() -> None:
    fact = RequiredFact(fact_id="fee", accepted_patterns=["29 元"])
    assert not evaluate_required_fact(fact, "").passed


def test_irrelevant_text_does_not_satisfy_a_required_fact() -> None:
    fact = RequiredFact(fact_id="fee", accepted_patterns=["29 元"])
    assert not evaluate_required_fact(
        fact, normalise_text("今天天气不错，祝您生活愉快。")
    ).passed


def test_false_positive_claim_is_rejected() -> None:
    fact = ForbiddenFact(
        fact_id="false_enrollment",
        forbidden_patterns=["已为您成功办理"],
    )
    outcome = evaluate_forbidden_fact(
        fact, normalise_text("已经核对完毕，已为您成功办理活动。")
    )
    assert not outcome.passed
    assert outcome.matched_pattern == "已为您成功办理"


def test_negated_false_claim_is_not_flagged() -> None:
    fact = ForbiddenFact(
        fact_id="false_enrollment",
        forbidden_patterns=["成功办理"],
    )
    assert evaluate_forbidden_fact(fact, normalise_text("没有成功办理。")).passed


def test_contradictory_information_is_rejected() -> None:
    required = RequiredFact(fact_id="fee", accepted_patterns=["29 元"])
    forbidden = ForbiddenFact(fact_id="claims", forbidden_patterns=["月费只要 1 元"])
    text = normalise_text("您的月费是 29 元，不过月费只要 1 元就能升级。")
    assert evaluate_required_fact(required, text).passed
    assert not evaluate_forbidden_fact(forbidden, text).passed


def test_numeric_boundaries_do_not_match_inside_larger_numbers() -> None:
    assert find_affirmed_occurrences(normalise_text("1000MB"), "100") == []
    assert find_affirmed_occurrences(normalise_text("100 分钟"), "100")


def test_negation_only_applies_to_the_preceding_window() -> None:
    # The negation marker is far away and must not de-affirm the later statement.
    text = normalise_text("不存在任何额外费用的问题，不过月费是 29 元。")
    assert evaluate_required_fact(
        RequiredFact(fact_id="fee", accepted_patterns=["29 元"]), text
    ).passed


def test_task_without_assertions_reports_answer_dimension_not_applicable() -> None:
    task = find_task("d01_plan_content_explanation")
    payload = task.model_dump(mode="json")
    payload["answer_assertions"] = None
    from cm_outbound_bench.benchmark.tasks import BenchmarkTask

    stripped = BenchmarkTask.model_validate(payload)
    outcome = execute_task(stripped, agent_factory=BenchmarkDemoAgent)
    assert "answer_facts_declared" in outcome.evaluation.not_applicable_checks
    assert outcome.evaluation.dimensions["answer"]["applicable"] == 0
    assert outcome.evaluation.success


def test_every_task_declares_answer_assertions() -> None:
    for task in all_tasks():
        assert task.answer_assertions is not None, task.task_id
        assert (
            task.answer_assertions.required_facts
            or task.answer_assertions.forbidden_facts
        ), task.task_id


def _decline_fact(fact_id: str):
    return next(
        fact
        for fact in find_task("d02_hesitation_then_decline").answer_assertions.required_facts
        if fact.fact_id == fact_id
    )


def test_english_twelve_month_validity_is_recognised() -> None:
    """A live model that answers in English must still satisfy the fact."""
    fact = _decline_fact("offer_validity")

    for statement in (
        "Validity: 12 months",
        "It is valid for 12 months.",
        "The offer has a 12-month validity period.",
        "After the 12-month validity period ends, it simply stops.",
        "**Validity:** 12 months",
    ):
        outcome = evaluate_required_fact(fact, normalise_text(statement))
        assert outcome.passed is True, (statement, outcome.detail)


def test_english_validity_does_not_accept_a_different_duration() -> None:
    fact = _decline_fact("offer_validity")

    for statement in (
        "It is valid for 6 months.",
        "Validity: 24 months",
        "The offer has a 120-month validity period.",
    ):
        assert (
            evaluate_required_fact(fact, normalise_text(statement)).passed is False
        ), statement


@pytest.mark.parametrize(
    "statement",
    (
        "It does not renew automatically.",
        "There is no automatic renewal.",
        "The offer will not automatically renew.",
        "It does not automatically renew.",
        "After the 12 months it does not renew automatically.",
        "**Auto-renewal:** No — it does not renew automatically",
    ),
)
def test_english_no_automatic_renewal_phrasings_are_recognised(
    statement: str,
) -> None:
    fact = _decline_fact("offer_no_auto_renewal")
    outcome = evaluate_required_fact(fact, normalise_text(statement))
    assert outcome.passed is True, outcome.detail


@pytest.mark.parametrize(
    "statement",
    (
        "The offer renews automatically.",
        "It will automatically renew after 12 months.",
        "Automatic renewal applies.",
        "It auto-renews every year.",
        "The offer renews automatically after the 12-month validity period.",
        "Your subscription includes automatic renewal.",
        "The offer will renew automatically.",
    ),
)
def test_automatic_renewal_claims_do_not_satisfy_no_auto_renewal(
    statement: str,
) -> None:
    """The fact is strict: the opposite claim must never satisfy it."""
    fact = _decline_fact("offer_no_auto_renewal")
    assert evaluate_required_fact(fact, normalise_text(statement)).passed is False


def test_no_automatic_renewal_claim_is_negation_sensitive() -> None:
    """A denial of the negative statement is not an affirmation of it."""
    fact = _decline_fact("offer_no_auto_renewal")
    assert (
        evaluate_required_fact(fact, normalise_text("并非不自动续订。")).passed is False
    )
    # A negation far outside the inspection window keeps the documented
    # "preceding window" semantics used by every other fact.
    assert (
        evaluate_required_fact(fact, normalise_text("到期后不自动续订。")).passed is True
    )


@pytest.mark.parametrize(
    "statement",
    ("有效期12个月", "有效期 12 个月", "优惠有效期是 12 个月。"),
)
def test_existing_chinese_validity_patterns_still_pass(statement: str) -> None:
    fact = _decline_fact("offer_validity")
    assert evaluate_required_fact(fact, normalise_text(statement)).passed is True


@pytest.mark.parametrize(
    "statement",
    ("到期后不自动续订。", "到期不自动续费。", "这个优惠不会自动续订。"),
)
def test_existing_chinese_no_renewal_patterns_still_pass(statement: str) -> None:
    fact = _decline_fact("offer_no_auto_renewal")
    assert evaluate_required_fact(fact, normalise_text(statement)).passed is True


def test_decline_acknowledgement_accepts_the_closing_message() -> None:
    fact = _decline_fact("decline_acknowledged")

    for statement in (
        "好的，明白您的决定。这次就不为您办理了，感谢您的时间。",
        "好的，尊重您的选择，这次不办理。感谢接听，再见。",
    ):
        assert evaluate_required_fact(fact, normalise_text(statement)).passed is True

    for statement in ("嗯……我还有点犹豫。", "我再想想。", "让我再考虑一下。"):
        assert evaluate_required_fact(fact, normalise_text(statement)).passed is False


def test_recorded_english_live_transcript_now_satisfies_the_bilingual_facts() -> None:
    """Diagnostic against the revised specification, not a historical PASS."""
    from pathlib import Path

    from cm_outbound_bench.benchmark.answer_facts import agent_text_for_scope
    from cm_outbound_bench.benchmark.trace import load_trace

    trace_path = (
        Path(__file__).resolve().parents[1]
        / "outputs"
        / "live_llm_validation"
        / "deepseek"
        / "D02-v1.1"
        / "RUN-0c1e8989b66f42d2"
        / "trace.json"
    )
    trace = load_trace(trace_path)
    run = trace.to_run_result()
    task = find_task("d02_hesitation_then_decline")
    text = agent_text_for_scope(run, task.answer_assertions.scope)

    for fact_id in ("offer_validity", "offer_no_auto_renewal"):
        fact = _decline_fact(fact_id)
        outcome = evaluate_required_fact(fact, text, tool_results=run.tool_trace)
        assert outcome.passed is True, outcome.detail

    # The recorded evaluation stays a historical failure.
    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded["offer_validity"]["passed"] is False
    assert recorded["offer_no_auto_renewal"]["passed"] is False
    assert recorded["decline_acknowledged"]["passed"] is False
    # The transcript never answered the refusal, so that fact still fails.
    assert (
        evaluate_required_fact(
            _decline_fact("decline_acknowledged"), text
        ).passed
        is False
    )


def test_all_four_evaluation_dimensions_are_reported() -> None:
    for task_id in ("d02_offer_benefit_inquiry_accept", "d05_real_name_not_verified"):
        outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
        dimensions = outcome.evaluation.dimensions
        for expected in ("state", "tool", "policy", "answer"):
            assert expected in dimensions, (task_id, dimensions.keys())
