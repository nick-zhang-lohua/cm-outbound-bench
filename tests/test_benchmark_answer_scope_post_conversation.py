"""Scoped answer-fact extraction around Runner post-conversation messages.

The Runner keeps the turn after the simulated customer closes the conversation,
so an agent may append a closing courtesy. It records that turn as a
``post_conversation_message`` action. ``final_answer`` must extract the agent's
last *substantive* customer-facing message from that structure, not from prose,
and must not let the courtesy erase the answer that preceded it.

These tests pin six behaviours:

A. the D01 comparison regression: Step 6 stays the final answer even though
   Step 7 is a recorded post-conversation courtesy, so the three required facts
   still pass;
B. ``final_report`` still selects a post-mutation outcome report delivered after
   the customer's consent closed the conversation;
C. ``any_agent_message`` still contains every agent message;
D. a conversation whose only agent message is a post-conversation courtesy has
   no final answer at all;
E. Tool Arguments and Tool Results are never treated as customer-facing answers;
F. forbidden-fact and negation behaviour is preserved, including a false claim
   made inside the courtesy that was skipped.

A separate section re-evaluates the preserved D01 live failure in read-only mode.
That run used ``d01_plan_comparison`` v1.0; the installed catalogue later
advanced to v1.1 in the D01 Simulator fallback repair. The revised-specification
re-evaluation therefore passes the installed task explicitly and is labelled a
diagnostic — the historical trace keeps its v1.0 identity and is never silently
resolved against the new catalogue.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cm_outbound_bench.agent import ConversationMessage
from cm_outbound_bench.benchmark.answer_facts import (
    agent_text_for_scope,
    evaluate_forbidden_fact,
    evaluate_required_fact,
    normalise_text,
    text_for_forbidden_facts,
)
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.replay import evaluate_trace, resolve_task
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.runner import (
    POST_CONVERSATION_MESSAGE_OUTCOME,
    RunAction,
    RunResult,
    SimulatorEvent,
    StopReason,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
D01_COMPARISON_TASK_ID = "d01_plan_comparison"

#: The preserved system-v4 D01 live failure. It is read-only.
FAILED_RUN_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "D01-system-v4-regression-20261122-r1"
)
FAILED_RUN_ID = "RUN-a95ed9e2040f407e"
FAILED_TRACE_PATH = FAILED_RUN_DIR / FAILED_RUN_ID / "trace.json"
FAILED_MANIFEST_PATH = FAILED_RUN_DIR / f"{FAILED_RUN_ID}.json"
FAILED_TRACE_FILE_SHA256 = (
    "b6a26191173dadea356699507c7f7360a0c3d7d85d16fbf454ed7be200810e1a"
)
FAILED_MANIFEST_SHA256 = (
    "652462aa57807cd9477c416b74f497834689de5b630e3146c73c619ffd2c7ac2"
)

#: The two substantive comparison answers and the closing courtesy of that run.
D01_STEP5_ANSWER = (
    "身份验证已通过，为您对比这两个套餐：\n\n"
    "**模拟畅享套餐（PLAN_M）**\n- 月费：59 元\n- 流量：20000 MB（20 GB）\n\n"
    "**模拟尊享套餐（PLAN_L）**\n- 月费：99 元\n- 流量：60000 MB（60 GB）\n\n"
    "**差异：** 畅享套餐更便宜，每月少 40 元。"
)
D01_STEP6_ANSWER = (
    "两个套餐的对比结果如下：\n\n"
    "| 项目 | 模拟畅享套餐（PLAN_M） | 模拟尊享套餐（PLAN_L） |\n"
    "|---|---|---|\n"
    "| 月费 | 59 元 | 99 元 |\n\n"
    "**核心差异：**\n- **月费**：畅享套餐便宜 40 元/月。\n\n"
    "本次仅为介绍，未为您办理任何套餐变更。"
)
D01_COURTESY = (
    "好的，您慢慢考虑，本次没有为您办理任何套餐变更。"
    "感谢您的来电，祝您生活愉快！"
)

#: A state-changing D06-style exchange: the consent turn closes the
#: conversation and the outcome report is delivered afterwards.
D06_DISCLOSURE = (
    "『模拟流量加赠包』的月费是 10 CNY，有效期 6 个月，到期后自动续订。"
    "您是否同意办理这个活动？"
)
D06_CONSENT = "行，我同意办理这个活动，办完记得给我发确认短信。"
D06_REPORT = (
    "已为您办理『模拟流量加赠包』，确认消息状态为 SENT，已模拟发送。"
    "感谢接听，再见。"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _run(
    conversation: list[ConversationMessage],
    actions: list[RunAction],
    *,
    simulator_events: list[SimulatorEvent] | None = None,
    tool_trace: list[ToolTraceEntry] | None = None,
    task_id: str = D01_COMPARISON_TASK_ID,
) -> RunResult:
    return RunResult(
        task_id=task_id,
        stop_reason=StopReason.AGENT_FINISHED,
        steps_executed=max(
            (action.step for action in actions),
            default=1,
        ),
        conversation=conversation,
        tool_trace=list(tool_trace or []),
        consent_events=[],
        final_db=CmOutboundDB(),
        actions=actions,
        simulator_events=list(simulator_events or []),
    )


def _say(step: int, message: str, *, outcome: str = "simulated_reply") -> RunAction:
    return RunAction(
        step=step, action_type="say", message=message, outcome=outcome
    )


def _d01_comparison_run(*, step6: str = D01_STEP6_ANSWER) -> RunResult:
    """The D01 shape: two answers, a customer sign-off, then a courtesy."""
    return _run(
        conversation=[
            ConversationMessage(step=5, role="agent", content=D01_STEP5_ANSWER),
            ConversationMessage(
                step=5,
                role="user",
                content="那这两个套餐差在哪儿？你直接比较一下。",
            ),
            ConversationMessage(step=6, role="agent", content=step6),
            ConversationMessage(
                step=6, role="user", content="好，那我再想想，谢谢你。"
            ),
            ConversationMessage(step=7, role="agent", content=D01_COURTESY),
        ],
        actions=[
            _say(5, D01_STEP5_ANSWER),
            _say(6, step6),
            _say(7, D01_COURTESY, outcome=POST_CONVERSATION_MESSAGE_OUTCOME),
        ],
        simulator_events=[
            SimulatorEvent(
                step=1,
                step_id="ask_compare",
                message="我在看你们的中档和高档套餐，这两个差在哪儿？",
                intent="ask_question",
            ),
            SimulatorEvent(
                step=2,
                step_id="provide_token",
                message="可以，我的测试身份令牌是 TEST_IDENTITY_C201。",
                intent="provide_identity",
            ),
            SimulatorEvent(
                step=5,
                step_id="fallback",
                message="那这两个套餐差在哪儿？你直接比较一下。",
                intent="ask_question",
            ),
            SimulatorEvent(
                step=6,
                step_id="done",
                message="好，那我再想想，谢谢你。",
                intent="end",
                ends_conversation=True,
            ),
        ],
    )


def _d01_comparison_facts() -> list:
    assertions = find_task(D01_COMPARISON_TASK_ID).answer_assertions
    assert assertions is not None
    return list(assertions.required_facts)


def _d01_forbidden_fact():
    assertions = find_task(D01_COMPARISON_TASK_ID).answer_assertions
    assert assertions is not None
    return next(
        fact
        for fact in assertions.forbidden_facts
        if fact.fact_id == "unrequested_change_claim"
    )


def _entry(tool_name: str, data: dict, *, step: int) -> ToolTraceEntry:
    return ToolTraceEntry(
        step=step,
        tool_name=tool_name,
        arguments={},
        result=ToolResult.success(data),
    )


# ---------------------------------------------------------------------------
# A. D01 exact regression
# ---------------------------------------------------------------------------


def test_final_answer_selects_the_substantive_answer_not_the_courtesy() -> None:
    run = _d01_comparison_run()

    final_answer = agent_text_for_scope(run, "final_answer")

    assert final_answer == normalise_text(D01_STEP6_ANSWER)
    assert normalise_text(D01_COURTESY) not in final_answer
    assert "好的,您慢慢考虑" not in final_answer


def test_d01_required_facts_pass_through_the_courtesy() -> None:
    run = _d01_comparison_run()
    final_answer = agent_text_for_scope(run, "final_answer")

    for fact in _d01_comparison_facts():
        outcome = evaluate_required_fact(
            fact, final_answer, tool_results=run.tool_trace
        )
        assert outcome.passed is True, (fact.fact_id, outcome.detail)


def test_d01_forbidden_fact_still_passes_through_the_courtesy() -> None:
    run = _d01_comparison_run()

    outcome = evaluate_forbidden_fact(
        _d01_forbidden_fact(),
        text_for_forbidden_facts(run, "final_answer"),
    )

    assert outcome.passed is True, outcome.detail


# ---------------------------------------------------------------------------
# B. Post-conversation report preservation
# ---------------------------------------------------------------------------


def _d06_outcome_run() -> RunResult:
    return _run(
        conversation=[
            ConversationMessage(step=6, role="agent", content=D06_DISCLOSURE),
            ConversationMessage(step=6, role="user", content=D06_CONSENT),
            ConversationMessage(step=10, role="agent", content=D06_REPORT),
        ],
        actions=[
            _say(6, D06_DISCLOSURE),
            RunAction(
                step=7,
                action_type="call_tool",
                tool_name="enroll_offer",
                arguments={"phone_number_id": "PN601", "offer_id": "OFFER_D02_001"},
                outcome="executed_post_conversation",
            ),
            RunAction(
                step=8,
                action_type="call_tool",
                tool_name="send_notification",
                arguments={"kind": "CONFIRMATION"},
                outcome="executed_post_conversation",
            ),
            RunAction(
                step=9,
                action_type="call_tool",
                tool_name="get_notification_status",
                arguments={},
                outcome="executed_post_conversation",
            ),
            _say(10, D06_REPORT, outcome=POST_CONVERSATION_MESSAGE_OUTCOME),
        ],
        simulator_events=[
            SimulatorEvent(
                step=6,
                step_id="consent",
                message=D06_CONSENT,
                intent="accept",
                consent_granted=True,
                consent_action="OFFER_ENROLLMENT",
                consent_target="OFFER_D02_001",
                ends_conversation=True,
            )
        ],
        tool_trace=[
            _entry(
                "enroll_offer",
                {"enrollment_id": "ENR-0001", "status": "enrolled"},
                step=7,
            ),
            _entry(
                "send_notification",
                {"notification_id": "NTF-0001", "status": "SENT"},
                step=8,
            ),
            _entry(
                "get_notification_status",
                {"notification_id": "NTF-0001", "status": "SENT"},
                step=9,
            ),
        ],
        task_id="d06_enrollment_confirmation_notification",
    )


def test_final_report_still_selects_the_post_conversation_outcome_report() -> None:
    run = _d06_outcome_run()

    final_report = agent_text_for_scope(run, "final_report")

    assert final_report == normalise_text(D06_REPORT)
    assert normalise_text(D06_DISCLOSURE) not in final_report


def test_final_report_facts_still_pass_with_their_grounding() -> None:
    run = _d06_outcome_run()
    assertions = find_task(
        "d06_enrollment_confirmation_notification"
    ).answer_assertions
    assert assertions is not None
    final_report = agent_text_for_scope(run, "final_report")

    for fact_id in ("enrollment_confirmed", "notification_status"):
        fact = next(
            item
            for item in assertions.required_facts
            if item.fact_id == fact_id
        )
        assert fact.scope == "final_report"
        outcome = evaluate_required_fact(
            fact, final_report, tool_results=run.tool_trace
        )
        assert outcome.passed is True, (fact_id, outcome.detail)


def test_consent_closing_keeps_the_outcome_report_in_the_final_answer() -> None:
    """A decision-answering post-conversation message is not a courtesy.

    The customer's consent is the last move; the mutation report is the answer to
    it. Excluding it here would make every post-consent report unreachable for a
    ``final_answer`` fact, which is why only a sign-off excludes it.
    """
    run = _d06_outcome_run()

    assert agent_text_for_scope(run, "final_answer") == normalise_text(D06_REPORT)


def test_refusal_closing_keeps_the_acknowledgement_in_the_final_answer() -> None:
    """D02 v1.2 protects its acknowledgement with the same structured rule.

    ``d02_hesitation_then_decline`` declares ``decline_acknowledged`` at
    ``final_answer`` scope, and the acknowledgement is necessarily the one
    post-conversation turn after the refusal. It must stay eligible.
    """
    acknowledgement = (
        "好的，明白您的决定。这次就不为您办理了，感谢您的时间。"
    )
    run = _run(
        conversation=[
            ConversationMessage(
                step=7,
                role="agent",
                content="您现在是想参加，还是先不参加呢？",
            ),
            ConversationMessage(
                step=7,
                role="user",
                content="那算了，我这次先不办了，谢谢你的介绍。",
            ),
            ConversationMessage(step=8, role="agent", content=acknowledgement),
        ],
        actions=[
            _say(7, "您现在是想参加，还是先不参加呢？"),
            _say(8, acknowledgement, outcome=POST_CONVERSATION_MESSAGE_OUTCOME),
        ],
        simulator_events=[
            SimulatorEvent(
                step=7,
                step_id="decline",
                message="那算了，我这次先不办了，谢谢你的介绍。",
                intent="refusal",
                ends_conversation=True,
            )
        ],
    )

    assert agent_text_for_scope(run, "final_answer") == normalise_text(
        acknowledgement
    )


# ---------------------------------------------------------------------------
# C. Any-agent-message preservation
# ---------------------------------------------------------------------------


def test_any_agent_message_still_contains_every_agent_message() -> None:
    run = _d01_comparison_run()

    everything = agent_text_for_scope(run, "any_agent_message")

    for fragment in (D01_STEP5_ANSWER, D01_STEP6_ANSWER, D01_COURTESY):
        assert normalise_text(fragment) in everything
    # The courtesy is present here even though ``final_answer`` skips it.
    assert normalise_text(D01_COURTESY) not in agent_text_for_scope(
        run, "final_answer"
    )


# ---------------------------------------------------------------------------
# D. No eligible answer
# ---------------------------------------------------------------------------


def test_only_a_post_conversation_courtesy_has_no_final_answer() -> None:
    run = _run(
        conversation=[
            ConversationMessage(
                step=1, role="user", content="好的，听明白了，谢谢你。"
            ),
            ConversationMessage(step=2, role="agent", content=D01_COURTESY),
        ],
        actions=[
            _say(2, D01_COURTESY, outcome=POST_CONVERSATION_MESSAGE_OUTCOME)
        ],
        simulator_events=[
            SimulatorEvent(
                step=1,
                step_id="done",
                message="好的，听明白了，谢谢你。",
                intent="end",
                ends_conversation=True,
            )
        ],
    )

    assert agent_text_for_scope(run, "final_answer") == ""
    # The courtesy itself is still recorded, in the scopes that keep everything.
    assert agent_text_for_scope(run, "any_agent_message") == normalise_text(
        D01_COURTESY
    )


def test_unrecorded_closing_turn_is_treated_as_a_sign_off() -> None:
    """Action metadata without a simulator record fails closed.

    The post-conversation turn is identified, but the closing customer turn has
    no recorded intent, so the extractor cannot prove that the trailing message
    answers a decision. It excludes it, which is the safe direction: a courtesy
    may never become the business answer on its own.
    """
    run = _run(
        conversation=[
            ConversationMessage(step=6, role="agent", content=D01_STEP6_ANSWER),
            ConversationMessage(
                step=6, role="user", content="好，那我再想想，谢谢你。"
            ),
            ConversationMessage(step=7, role="agent", content=D01_COURTESY),
        ],
        actions=[
            _say(6, D01_STEP6_ANSWER),
            _say(7, D01_COURTESY, outcome=POST_CONVERSATION_MESSAGE_OUTCOME),
        ],
        simulator_events=[],
    )

    assert agent_text_for_scope(run, "final_answer") == normalise_text(
        D01_STEP6_ANSWER
    )


def test_run_without_recorded_actions_keeps_the_historical_selection() -> None:
    """A trace without recorded actions still loads with the old behaviour.

    Post-conversation turns can only be identified from the Runner's action
    metadata. When that metadata is absent, the extraction must not guess: it
    keeps the historical "last agent message" rule instead of inventing an
    exclusion it cannot justify.
    """
    run = _run(
        conversation=[
            ConversationMessage(step=1, role="user", content="好的，谢谢你。"),
            ConversationMessage(step=2, role="agent", content=D01_COURTESY),
        ],
        actions=[],
        simulator_events=[],
    )

    assert agent_text_for_scope(run, "final_answer") == normalise_text(D01_COURTESY)


# ---------------------------------------------------------------------------
# E. Tool results are not answers
# ---------------------------------------------------------------------------


def test_tool_results_never_satisfy_a_customer_facing_fact() -> None:
    comparison = {
        "currency": "CNY",
        "monthly_fee_delta": 40.0,
        "plans": [
            {"plan_id": "PLAN_M", "monthly_fee": 59.0},
            {"plan_id": "PLAN_L", "monthly_fee": 99.0},
        ],
    }
    run = _run(
        conversation=[
            ConversationMessage(
                step=2,
                role="agent",
                content="我已经为您对比了这两个套餐，结果请以工具查询为准。",
            ),
            ConversationMessage(
                step=2,
                role="user",
                content="那 59 元和 99 元差 40 元，对吧？",
            ),
        ],
        actions=[_say(2, "我已经为您对比了这两个套餐，结果请以工具查询为准。")],
        simulator_events=[
            SimulatorEvent(
                step=2,
                step_id="ask_compare",
                message="那 59 元和 99 元差 40 元，对吧？",
                intent="ask_question",
            )
        ],
        tool_trace=[_entry("compare_plans", comparison, step=1)],
    )

    final_answer = agent_text_for_scope(run, "final_answer")
    assert "59" not in final_answer
    assert "99" not in final_answer
    assert "40" not in final_answer
    # The customer's numbers and the Tool Result are not agent answers.
    assert "59" not in agent_text_for_scope(run, "any_agent_message")
    assert "59" not in agent_text_for_scope(run, "final_report")

    for fact in _d01_comparison_facts():
        assert (
            evaluate_required_fact(
                fact, final_answer, tool_results=run.tool_trace
            ).passed
            is False
        ), fact.fact_id


def test_tool_arguments_are_not_answers() -> None:
    run = _run(
        conversation=[
            ConversationMessage(
                step=2,
                role="agent",
                content="已经为您查好了，详情请查看工具结果。",
            )
        ],
        actions=[
            RunAction(
                step=1,
                action_type="call_tool",
                tool_name="compare_plans",
                arguments={"plan_id_a": "PLAN_M", "plan_id_b": "PLAN_L"},
                outcome="executed",
            ),
            _say(2, "已经为您查好了，详情请查看工具结果。"),
        ],
    )

    final_answer = agent_text_for_scope(run, "final_answer")
    assert "PLAN_M" not in final_answer
    assert "PLAN_L" not in final_answer


# ---------------------------------------------------------------------------
# F. Contradictions and negation
# ---------------------------------------------------------------------------


def test_a_contradiction_in_the_selected_answer_still_fails() -> None:
    run = _d01_comparison_run(
        step6=("两个套餐的对比结果如下：月费 59 元和 99 元，相差 40 元。"
               "已为您办理套餐变更。")
    )

    outcome = evaluate_forbidden_fact(
        _d01_forbidden_fact(), text_for_forbidden_facts(run, "final_answer")
    )

    assert outcome.passed is False
    assert outcome.matched_pattern == "已为您办理"


def test_a_contradiction_in_the_skipped_courtesy_is_not_hidden() -> None:
    courtesy = "好的，已为您办理套餐变更，感谢您的来电。"
    run = _run(
        conversation=[
            ConversationMessage(step=6, role="agent", content=D01_STEP6_ANSWER),
            ConversationMessage(
                step=6, role="user", content="好，那我再想想，谢谢你。"
            ),
            ConversationMessage(step=7, role="agent", content=courtesy),
        ],
        actions=[
            _say(6, D01_STEP6_ANSWER),
            _say(7, courtesy, outcome=POST_CONVERSATION_MESSAGE_OUTCOME),
        ],
        simulator_events=[
            SimulatorEvent(
                step=6,
                step_id="done",
                message="好，那我再想想，谢谢你。",
                intent="end",
                ends_conversation=True,
            )
        ],
    )

    # The substantive answer is still the final answer.
    assert agent_text_for_scope(run, "final_answer") == normalise_text(
        D01_STEP6_ANSWER
    )
    # The forbidden claim in the courtesy is still inspected and still fails.
    forbidden_text = text_for_forbidden_facts(run, "final_answer")
    assert normalise_text(courtesy) in forbidden_text
    outcome = evaluate_forbidden_fact(_d01_forbidden_fact(), forbidden_text)
    assert outcome.passed is False
    assert outcome.matched_pattern == "已为您办理"


def test_negation_still_de_affirms_a_required_statement() -> None:
    run = _d01_comparison_run(
        step6="这两个套餐的月费不是 59 元，也不是 99 元，相差也不是 40 元。"
    )
    final_answer = agent_text_for_scope(run, "final_answer")

    for fact in _d01_comparison_facts():
        assert (
            evaluate_required_fact(
                fact, final_answer, tool_results=run.tool_trace
            ).passed
            is False
        ), fact.fact_id


# ---------------------------------------------------------------------------
# Preserved D01 live failure: read-only re-evaluation
# ---------------------------------------------------------------------------


def test_preserved_d01_artifacts_are_byte_unchanged() -> None:
    assert (
        trace_file_sha256(FAILED_TRACE_PATH) == FAILED_TRACE_FILE_SHA256
    )
    assert (
        hashlib.sha256(FAILED_MANIFEST_PATH.read_bytes()).hexdigest()
        == FAILED_MANIFEST_SHA256
    )


def test_preserved_d01_trace_is_not_reinterpreted_as_v1_1() -> None:
    """The stored v1.0 trace keeps its identity; only a diagnostic may use v1.1."""
    trace = load_trace(FAILED_TRACE_PATH)
    assert trace.task_version == "1.0"
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


def test_preserved_d01_failure_now_satisfies_all_three_answer_facts() -> None:
    trace = load_trace(FAILED_TRACE_PATH)
    # The trace is v1.0 and the installed catalogue is v1.3, so the current task
    # is passed explicitly. This is a diagnostic against the revised
    # specification, never a claim that the historical run used the current
    # version.
    task = find_task(D01_COMPARISON_TASK_ID)
    assert task.task_version == "1.3"
    replay = evaluate_trace(trace, task=task)
    evaluation = replay.evaluation

    results = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert results["plan_m_fee"]["passed"] is True
    assert results["plan_m_fee"]["matched_pattern"] == "59 元"
    assert results["plan_l_fee"]["passed"] is True
    assert results["plan_l_fee"]["matched_pattern"] == "99 元"
    assert results["fee_difference"]["passed"] is True
    assert results["fee_difference"]["matched_pattern"] == "40 元"

    checks = {check.name: check for check in evaluation.checks}
    assert checks["answer_required_plan_m_fee"].passed is True
    assert checks["answer_required_plan_l_fee"].passed is True
    assert checks["answer_required_fee_difference"].passed is True
    assert checks["no_policy_violations"].passed is True
    assert evaluation.policy_violations == []
    assert evaluation.success is True

    # The recorded evaluation is still the historical failure: this is a
    # read-only re-evaluation, not a rewrite of the preserved run.
    assert trace.evaluation.success is False
    assert set(trace.evaluation.failed_checks) == {
        "answer_required_plan_m_fee",
        "answer_required_plan_l_fee",
        "answer_required_fee_difference",
        "no_policy_violations",
    }
    assert replay.matches_recorded is False
    assert any(
        difference.startswith("success changed")
        for difference in replay.differences
    )
