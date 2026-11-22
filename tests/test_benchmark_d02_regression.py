"""D02 regression: proactive eligibility, Chinese decline, reasons and the DNC gate.

Four confirmed D02 defects are pinned here:

1. the shared prompt did not say that a required read-only eligibility check must
   be executed directly, so the live agent asked for permission and then fell
   back three times;
2. the Chinese consent detector did not recognise the live enrollment decision
   questions, so the hesitation and decline steps never fired;
3. the valid Chinese eligibility-reason wording did not satisfy the reason fact,
   and tool-result grounding could not check a list-valued field;
4. the deterministic Final Response Gate reported a do-not-call registration in
   English and exposed internal record and line identifiers.

The four recorded v1.0 D02 live traces are read-only. Every evaluation against
the revised specification is labelled a **diagnostic against the revised
specification**, never a historical v1.0 PASS and never a formal v1.1 run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.agent import (
    AgentContext,
    CallToolAction,
    ConversationMessage,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.benchmark.answer_facts import (
    agent_text_for_scope,
    evaluate_required_fact,
    normalise_text,
)
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import find_task, policy_for_domain
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.replay import evaluate_trace
from cm_outbound_bench.benchmark.simulator import (
    DEFAULT_FALLBACK_LIMIT,
    Intent,
    ScriptedSimulator,
)
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.llm_agent import LLMBackedAgent, build_system_prompt
from cm_outbound_bench.providers.base import ProviderResponse, ProviderToolCall

TASK_ACCEPT = "d02_offer_benefit_inquiry_accept"
TASK_DECLINE = "d02_hesitation_then_decline"
TASK_INELIGIBLE = "d02_eligibility_ineligible_explanation"
TASK_DNC = "d02_direct_refusal_do_not_call"
D06_TASK_ID = "d06_enrollment_confirmation_notification"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
D02_LIVE_DIR = (
    PROJECT_ROOT / "outputs" / "live_llm_validation" / "deepseek" / "D02"
)

#: The four recorded v1.0 live runs that exposed the D02 defects. The trace and
#: manifest bytes must never change; the task hash is the historical v1.0 hash.
HISTORICAL_RUNS: dict[str, dict[str, str]] = {
    TASK_ACCEPT: {
        "run_id": "RUN-a188faadc560405d",
        "manifest_sha256": (
            "70df9b464f1f3ab22cc3df18e1ca77b1daa247d740cf2d543daa26a65aed0035"
        ),
        "trace_sha256": (
            "cb050de666aba4f0267bbb04cfbdb788193ab89220d2433e11c19453c9192884"
        ),
        "trace_content_hash": (
            "8fe49e837f22fef3a15cb8b17ef79946654e2fa0f62724a358b6e3ceb69c8817"
        ),
        "task_hash": (
            "44dc69add162bf3d6262701e9a6771ff9b25f4872e73474e96dd7fc5f00c3788"
        ),
    },
    TASK_DECLINE: {
        "run_id": "RUN-eef9ac4a21784bc1",
        "manifest_sha256": (
            "43fc4085cd31148005dd5c67dd9de83367483612f62b8584751ab05201a7265d"
        ),
        "trace_sha256": (
            "4354660bfc67b4ea661265c81d8b582b76774723ed030970236cb882e0833148"
        ),
        "trace_content_hash": (
            "d99da5a20772079cc3fea03c9520ed8aa7cbfee1a96d7021098b19ebe1704ee3"
        ),
        "task_hash": (
            "1a0dee3fbfd4ab472b92326073c9f8c2d58dffd2aa10076aa52d50b66db497f8"
        ),
    },
    TASK_INELIGIBLE: {
        "run_id": "RUN-17260393ae4247fe",
        "manifest_sha256": (
            "78105e97985db068ea8dfaf8ea8b5dc559c9f26affc0827c2d7eac881148826b"
        ),
        "trace_sha256": (
            "1f6c24ddbc57fe48d1f53e3fc489a9bef6bb65a359949bd5832bc944489bacc1"
        ),
        "trace_content_hash": (
            "292ea26aecbad6ce84fa3f8e87b69d58eb9198a6272c18ca9c08375cee8e8af7"
        ),
        "task_hash": (
            "b74546bed9672ff500e383933e597a407e94ce4caefe461e33dee88fc6f5923e"
        ),
    },
    TASK_DNC: {
        "run_id": "RUN-3bfb2341276f45fd",
        "manifest_sha256": (
            "4dafacc29969137ed71fc9c4e41709b272eccde055875aad3bfc781f43530d0e"
        ),
        "trace_sha256": (
            "c6b02dec5306321855a38be8c6fcb0706606957cc784e62642f2b732e4e41450"
        ),
        "trace_content_hash": (
            "49733aca97b0d07c8b8eeb8b3f996b30b344e9fa20b68169ae167cf66a6b14cd"
        ),
        "task_hash": (
            "e91d424a8841f4ae65e6ff2576b7ce1f415a9561f522168134fdf03802cc88ba"
        ),
    },
}

REVISED_SPECIFICATION_LABEL = "diagnostic against revised specification"

#: The three Chinese enrollment decision questions the live D02 agent actually
#: sent in ``RUN-eef9ac4a21784bc1``.
LIVE_ZH_ENROLLMENT_QUESTIONS = (
    "请问您是否愿意参加这个活动？",
    "请问您希望参加这个活动吗？",
    "请问您现在是想参加，还是先不参加呢？",
)

#: Existing and paraphrased decision questions that must keep working.
ZH_ENROLLMENT_DECISION_QUESTIONS = LIVE_ZH_ENROLLMENT_QUESTIONS + (
    "您希望参加这个活动吗？",
    "您现在是想参加，还是先不参加呢？",
    "您是否同意办理这个活动？",
    "您想现在办理，还是先不办呢？",
    "我帮您办理这个活动，可以吗？",
    "需要我为您办理吗？",
)

#: Eligibility permission questions are read-only and are never consent.
ZH_READONLY_ELIGIBILITY_QUESTIONS = (
    "请问您是否希望我为您查询一下这条线路是否符合办理条件？",
    "我可以帮您查询一下是否符合办理条件吗？",
    "需要我帮您确认一下办理资格吗？",
    "要不要先帮您查询办理资格？",
    "需要我帮您查一下能不能办理吗？",
)

#: Generic enrollment mentions and non-decision statements must not fire.
ZH_NON_CONSENT_MENTIONS = (
    "我再想想",
    "我再想想，你还有什么要说明的吗？",
    "这个活动支持办理。",
    "这个活动支持办理吗？",
    "办理这个活动需要您同意。",
    "您同意办理这个活动。",
    "您可以办理这个活动。",
    "您的号码符合办理条件。",
    "如果您愿意了解，请提供您的验证令牌。",
    "如果要确认您这个号码能不能办理，需要先验证您的身份。",
)

#: Exact live ineligibility wording from ``RUN-17260393ae4247fe``.
LIVE_INELIGIBLE_REASON = "您当前的套餐不在该活动适用的套餐范围内"

#: Precise reason expressions that must satisfy the reason fact.
PRECISE_REASON_PATTERNS = (
    "plan_not_eligible",
    "当前套餐不在该活动适用范围",
    "您当前的套餐不在该活动适用的套餐范围内",
    "套餐不属于该活动的适用套餐",
    "该套餐不在活动指定的套餐范围内",
    "套餐不符合活动要求",
    "仅适用于指定套餐",
)

#: A generic denial is not a usable reason code.
GENERIC_DENIALS = (
    "您不符合资格",
    "参加条件",
)

#: The four disclosed offer terms, used by the Task 1 scripted workflow.
DISCLOSURE_AND_ELIGIBILITY = (
    "活动名称：模拟流量加赠包。每月额外费用 10 CNY，有效期 6 个月，"
    "到期自动续订，限制：仅限主卡使用。"
    "我先帮您查询一下办理资格，可以吗？"
)
DISCLOSURE_ACCEPT = (
    "活动名称：模拟流量加赠包。每月额外费用 10 CNY，有效期 6 个月，"
    "到期自动续订，限制：仅限主卡使用。"
)
DISCLOSURE_DECLINE = (
    "活动名称：模拟语音加赠包。每月额外费用 15 CNY，有效期 12 个月，"
    "到期不自动续订，限制：仅限家庭套餐主卡。"
)
DECLINE_ACKNOWLEDGEMENT = "好的，尊重您的选择，这次不办理。感谢接听，再见。"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(task_id: str):
    return find_task(task_id)


def _assertions(task_id: str):
    assertions = _task(task_id).answer_assertions
    assert assertions is not None
    return assertions


def _required_fact(fact_id: str, task_id: str):
    return next(
        fact
        for fact in _assertions(task_id).required_facts
        if fact.fact_id == fact_id
    )


def _manifest_path(task_id: str) -> Path:
    run_id = HISTORICAL_RUNS[task_id]["run_id"]
    return D02_LIVE_DIR / f"{run_id}.json"


def _trace_path(task_id: str) -> Path:
    run_id = HISTORICAL_RUNS[task_id]["run_id"]
    return D02_LIVE_DIR / run_id / "trace.json"


def _entry(
    tool_name: str,
    data: dict | None = None,
    *,
    ok: bool = True,
    code: str = "",
    message: str = "",
    step: int = 2,
) -> ToolTraceEntry:
    result = (
        ToolResult.success(data or {})
        if ok
        else ToolResult.failure(code, message)
    )
    return ToolTraceEntry(
        step=step,
        tool_name=tool_name,
        arguments={},
        result=result,
    )


def _eligibility_entry(reasons, *, ok: bool = True) -> ToolTraceEntry:
    return _entry(
        "check_offer_eligibility",
        {"eligible": False, "reasons": reasons},
        ok=ok,
        code="eligibility_failed" if not ok else "",
        message="Eligibility could not be checked." if not ok else "",
    )


def _agent_context(
    task_id: str,
    traces: list[ToolTraceEntry],
    *,
    messages: list[ConversationMessage] | None = None,
) -> AgentContext:
    task = _task(task_id)
    return AgentContext(
        task=task.agent_view(),
        policy="Synthetic test policy.",
        tools=build_toolkit(CmOutboundDB()).list_tools(),
        messages=messages
        or [
            ConversationMessage(step=1, role="agent", content="Hello."),
            ConversationMessage(step=1, role="user", content="好的。"),
        ],
        tool_results=traces,
        step=10,
    )


class _FinishClient:
    """A provider that immediately asks to finish, like the recorded live run."""

    def __init__(self) -> None:
        self.messages: list = []

    def complete(self, *, messages, tools):
        self.messages = list(messages)
        return ProviderResponse(finish_requested=True)


class _ScriptedProvider:
    """Return a fixed provider response sequence; never touches the network."""

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)
        self._index = 0

    def complete(self, *, messages, tools) -> ProviderResponse:
        del messages, tools
        response = self._responses[self._index]
        self._index += 1
        return response


def _advance_task2_to_decision() -> ScriptedSimulator:
    """Drive the real D02 decline script up to its first decision question."""
    task = _task(TASK_DECLINE)
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    assert simulator.respond("您好。").step_id == "ask_what"
    assert (
        simulator.respond("为了核对机主身份，请提供您的测试身份令牌。").step_id
        == "provide_token"
    )
    # All four terms are disclosed in one turn, so the customer's remaining
    # question steps are skipped and the next decision question is reachable.
    assert simulator.respond(DISCLOSURE_DECLINE).step_id == "fallback"
    assert simulator.terms_explained == {
        "monthly_fee",
        "validity",
        "renewal",
        "restrictions",
    }
    return simulator


class _EligibilityPermissionProbeAgent:
    """Verify identity, disclose the terms and ask permission to check eligibility."""

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        step = self._step
        line = context.task.phone_number_id
        offer = context.task.offer_id or ""
        if step == 1:
            return SayAction(message="您好，我是中国移动客服。")
        if step == 2:
            return SayAction(message="为了核对机主身份，请提供您的测试身份令牌。")
        if step == 3:
            return CallToolAction(
                tool_name="verify_customer_identity",
                arguments={
                    "phone_number_id": line,
                    "identity_token": "TEST_IDENTITY_C101",
                },
            )
        if step == 4:
            return CallToolAction(
                tool_name="get_offer_details", arguments={"offer_id": offer}
            )
        if step == 5:
            return SayAction(message=DISCLOSURE_AND_ELIGIBILITY)
        if step == 6:
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        return FinishAction(reason="eligibility permission probe complete")


class _AcceptWorkflowAgent:
    """Run the complete required D02 enrollment workflow in the correct order."""

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        step = self._step
        line = context.task.phone_number_id
        offer = context.task.offer_id or ""

        if step == 1:
            return SayAction(message="您好，我是中国移动客服，想介绍一项流量活动。")
        if step == 2:
            return SayAction(message="为了核对机主身份，请提供您的测试身份令牌。")
        if step == 3:
            return CallToolAction(
                tool_name="verify_customer_identity",
                arguments={
                    "phone_number_id": line,
                    "identity_token": "TEST_IDENTITY_C101",
                },
            )
        if step == 4:
            return CallToolAction(
                tool_name="get_offer_details", arguments={"offer_id": offer}
            )
        if step == 5:
            return SayAction(message=DISCLOSURE_AND_ELIGIBILITY)
        if step == 6:
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        if step == 7:
            return SayAction(
                message="您是否同意办理这个活动？需要您明确同意后我才能提交。"
            )
        if step == 8:
            return CallToolAction(
                tool_name="enroll_offer",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        if step == 9:
            enrollment_id = next(
                str(entry.result.data["enrollment_id"])
                for entry in context.tool_results
                if entry.tool_name == "enroll_offer" and entry.result.ok
            )
            return CallToolAction(
                tool_name="send_confirmation_sms",
                arguments={"enrollment_id": enrollment_id},
            )
        if step == 10:
            return SayAction(
                message="已为您成功办理『模拟流量加赠包』，确认短信已模拟发送。"
            )
        return FinishAction(reason="D02 accept workflow complete")


class _DeclineWorkflowAgent:
    """Ask two Chinese decision questions, receive the decline and acknowledge it."""

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        step = self._step
        line = context.task.phone_number_id
        offer = context.task.offer_id or ""

        if step == 1:
            return SayAction(message="您好，我是中国移动客服，想介绍一项流量活动。")
        if step == 2:
            return SayAction(message="为了核对机主身份，请提供您的测试身份令牌。")
        if step == 3:
            return CallToolAction(
                tool_name="verify_customer_identity",
                arguments={
                    "phone_number_id": line,
                    "identity_token": "TEST_IDENTITY_C102",
                },
            )
        if step == 4:
            return CallToolAction(
                tool_name="get_offer_details", arguments={"offer_id": offer}
            )
        if step == 5:
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        if step == 6:
            return SayAction(message=DISCLOSURE_DECLINE)
        if step == 7:
            return SayAction(message="请问您是否愿意参加这个活动？")
        if step == 8:
            return SayAction(message="您现在是想参加，还是先不参加呢？")
        if step == 9:
            return SayAction(message=DECLINE_ACKNOWLEDGEMENT)
        return FinishAction(reason="D02 decline workflow complete")


# ---------------------------------------------------------------------------
# 1. Shared prompt and D02 policy
# ---------------------------------------------------------------------------


def test_shared_prompt_requires_direct_read_only_execution() -> None:
    prompt = build_system_prompt(
        _task(TASK_ACCEPT).agent_view(), "Synthetic test policy."
    )
    assert "Execute a required read-only check directly" in prompt
    assert "Do not ask the customer for permission to run it" in prompt
    assert "do not describe a read-only check as enrollment" in prompt
    assert "never enrollment consent" in prompt
    # The rule stays generic: no domain tool name is hard-coded in the prompt.
    assert "check_offer_eligibility" not in prompt
    assert "enroll_offer" not in prompt


def test_d02_policy_specifies_the_required_business_sequence() -> None:
    policy = policy_for_domain("D02")
    flat = " ".join(policy.split())
    ordered = [
        "Verify the customer's identity.",
        "Retrieve the offer details.",
        "Disclose every required offer term listed above.",
        "Execute `check_offer_eligibility` directly.",
        "Explain the returned eligibility result",
        "Ask for explicit enrollment consent only when the result is eligible.",
        "Enroll only after the runner has recorded that consent.",
        "Send the requested confirmation SMS only after a successful enrollment.",
        "Report the actual result.",
    ]
    positions = [flat.index(phrase) for phrase in ordered]
    assert positions == sorted(positions)
    assert "do not ask the customer for permission" in flat
    assert "never enrollment consent" in flat


# ---------------------------------------------------------------------------
# 1. Task 1: dedicated eligibility step and the consent boundary
# ---------------------------------------------------------------------------


def test_eligibility_step_is_defensive_and_never_grants_consent() -> None:
    step = next(
        item
        for item in _task(TASK_ACCEPT).simulator.steps
        if item.step_id == "agent_asks_eligibility"
    )
    assert step.trigger == "agent_asks_eligibility"
    assert step.intent is Intent.ASK_QUESTION
    assert step.consent_action is None
    assert step.consent_target == ""
    assert step.requires_token_shared is True
    assert set(step.required_terms) == {
        "monthly_fee",
        "validity",
        "renewal",
        "restrictions",
    }
    assert step.ends_conversation is False


def test_eligibility_permission_request_gets_a_non_consent_reply() -> None:
    task = _task(TASK_ACCEPT)
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)

    assert simulator.respond("您好。").step_id == "ask_content"
    assert (
        simulator.respond("为了核对机主身份，请提供您的测试身份令牌。").step_id
        == "provide_token"
    )
    reply = simulator.respond(DISCLOSURE_AND_ELIGIBILITY)

    assert reply.step_id == "agent_asks_eligibility"
    assert reply.message == "可以，你先帮我查一下能不能办，不用再问我。"
    assert reply.intent is Intent.ASK_QUESTION
    assert reply.consent_granted is False
    assert reply.consent_action is None
    assert reply.consent_target == ""
    assert reply.ends_conversation is False
    assert simulator.token_shared is True


def test_eligibility_permission_reply_creates_no_consent_record() -> None:
    outcome = execute_task(
        _task(TASK_ACCEPT), agent_factory=_EligibilityPermissionProbeAgent
    )
    run = outcome.run

    eligibility_event = next(
        event
        for event in run.simulator_events
        if event.step_id == "agent_asks_eligibility"
    )
    assert eligibility_event.consent_granted is False
    assert eligibility_event.ends_conversation is False

    assert run.consent_events == []
    assert run.final_db.consent_records == []
    assert run.final_db.enrollments == []
    assert run.final_db.sms_records == []

    # The read-only check itself is still allowed and executed.
    eligibility_call = next(
        entry
        for entry in run.tool_trace
        if entry.tool_name == "check_offer_eligibility"
    )
    assert eligibility_call.result.ok is True
    assert eligibility_call.result.data["eligible"] is True


def test_explicit_enrollment_question_still_activates_the_accept_step() -> None:
    task = _task(TASK_ACCEPT)
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)

    simulator.respond("您好。")
    simulator.respond("为了核对机主身份，请提供您的测试身份令牌。")
    simulator.respond(DISCLOSURE_AND_ELIGIBILITY)
    reply = simulator.respond("您是否同意办理这个活动？")

    assert reply.step_id == "accept"
    assert reply.intent is Intent.ACCEPT
    assert reply.consent_granted is True
    assert reply.consent_action is not None
    assert reply.consent_action.value == "OFFER_ENROLLMENT"
    assert reply.consent_target == task.offer_id
    assert reply.ends_conversation is True


def test_complete_offline_accept_workflow_orders_every_step() -> None:
    outcome = execute_task(_task(TASK_ACCEPT), agent_factory=_AcceptWorkflowAgent)
    run = outcome.run
    evaluation = outcome.evaluation

    assert evaluation.success, evaluation.summary
    tool_steps = {
        entry.tool_name: entry.step
        for entry in run.tool_trace
        if entry.result.ok
    }
    ordered = [
        "verify_customer_identity",
        "get_offer_details",
        "check_offer_eligibility",
        "enroll_offer",
        "send_confirmation_sms",
    ]
    assert [tool_steps[name] for name in ordered] == sorted(
        tool_steps[name] for name in ordered
    )

    step_ids = [event.step_id for event in run.simulator_events]
    assert "agent_asks_eligibility" in step_ids
    assert "accept" in step_ids

    assert len(run.consent_events) == 1
    consent_step = run.consent_events[0].step
    assert tool_steps["check_offer_eligibility"] < consent_step
    assert consent_step < tool_steps["enroll_offer"]
    assert tool_steps["enroll_offer"] < tool_steps["send_confirmation_sms"]

    assert len(run.final_db.consent_records) == 1
    assert len(run.final_db.enrollments) == 1
    assert len(run.final_db.sms_records) == 1
    assert run.final_db.enrollments[0].consent_id == (
        run.final_db.consent_records[0].consent_id
    )


def test_recorded_repeated_fallback_eligibility_permission_now_answers() -> None:
    """Protect against ``RUN-a188faadc560405d``'s three repeated fallbacks."""
    trace = load_trace(_trace_path(TASK_ACCEPT))

    # The historical run is the defect: the eligibility permission question was
    # never answered by a scripted step.
    recorded_steps = [event.step_id for event in trace.simulator_events]
    assert recorded_steps.count("fallback") >= 3
    assert "agent_asks_eligibility" not in recorded_steps

    task = _task(TASK_ACCEPT)
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    replies = [
        simulator.respond(action.message)
        for action in trace.actions
        if action.action_type == "say"
    ]

    assert [reply.step_id for reply in replies[:3]] == [
        "ask_content",
        "provide_token",
        "agent_asks_eligibility",
    ]
    eligibility = replies[2]
    assert eligibility.consent_granted is False
    assert eligibility.consent_action is None
    assert eligibility.ends_conversation is False
    assert eligibility.message == "可以，你先帮我查一下能不能办，不用再问我。"
    # The first eligibility question no longer falls back.
    assert all(
        reply.step_id != "fallback"
        for reply in replies[:3]
    )


def test_d02_accept_task_still_passes_with_the_baseline_agent() -> None:
    outcome = execute_task(_task(TASK_ACCEPT), agent_factory=BenchmarkDemoAgent)
    assert outcome.evaluation.success, outcome.evaluation.summary


# ---------------------------------------------------------------------------
# 2. Task 2: Chinese hesitation and decline detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", ZH_ENROLLMENT_DECISION_QUESTIONS)
def test_chinese_enrollment_decision_questions_request_consent(
    question: str,
) -> None:
    assert ScriptedSimulator._asks_consent(question) is True


@pytest.mark.parametrize("question", LIVE_ZH_ENROLLMENT_QUESTIONS)
def test_each_live_question_triggers_the_hesitation_step(question: str) -> None:
    simulator = _advance_task2_to_decision()
    reply = simulator.respond(question)
    assert reply.step_id == "hesitate"
    assert reply.intent is Intent.HESITATION
    assert reply.consent_granted is False
    assert reply.consent_action is None
    assert reply.consent_target == ""
    assert reply.ends_conversation is False


def test_first_question_hesitates_then_second_question_declines() -> None:
    simulator = _advance_task2_to_decision()

    first = simulator.respond(LIVE_ZH_ENROLLMENT_QUESTIONS[0])
    assert first.step_id == "hesitate"
    assert first.consent_granted is False

    second = simulator.respond(LIVE_ZH_ENROLLMENT_QUESTIONS[1])
    assert second.step_id == "decline"
    assert second.intent is Intent.REFUSAL
    assert second.consent_granted is False
    assert second.consent_action is None
    assert second.consent_target == ""
    assert second.ends_conversation is True


@pytest.mark.parametrize("question", LIVE_ZH_ENROLLMENT_QUESTIONS)
def test_each_live_question_can_trigger_the_decline_step(question: str) -> None:
    simulator = _advance_task2_to_decision()

    first = simulator.respond("您是否同意办理这个活动？")
    assert first.step_id == "hesitate"
    second = simulator.respond(question)
    assert second.step_id == "decline"
    assert second.consent_granted is False
    assert second.consent_action is None
    assert second.ends_conversation is True


def test_hesitation_and_decline_steps_carry_no_consent() -> None:
    steps = {
        step.step_id: step
        for step in _task(TASK_DECLINE).simulator.steps
    }
    for step_id in ("hesitate", "decline"):
        step = steps[step_id]
        assert step.trigger == "agent_asks_consent"
        assert step.requires_token_shared is True
        assert step.consent_action is None
        assert step.consent_target == ""
    assert steps["hesitate"].intent is Intent.HESITATION
    assert steps["decline"].intent is Intent.REFUSAL


@pytest.mark.parametrize("message", ZH_NON_CONSENT_MENTIONS)
def test_generic_mentions_and_non_decision_questions_are_not_consent(
    message: str,
) -> None:
    assert ScriptedSimulator._asks_consent(message) is False


@pytest.mark.parametrize("message", ZH_READONLY_ELIGIBILITY_QUESTIONS)
def test_read_only_eligibility_questions_are_not_consent(message: str) -> None:
    assert ScriptedSimulator._asks_consent(message) is False


def test_wo_zai_xiang_xiang_alone_never_grants_consent() -> None:
    for message in ("我再想想", "我再想想，你还有什么要说明的吗？"):
        assert ScriptedSimulator._asks_consent(message) is False
        reply = ScriptedSimulator(
            _task(TASK_DECLINE).simulator, offer_id="OFFER_D02_002"
        ).respond(message)
        assert reply.consent_granted is False
        assert reply.step_id != "accept"


def test_decline_workflow_creates_no_consent_enrollment_or_sms() -> None:
    outcome = execute_task(_task(TASK_DECLINE), agent_factory=_DeclineWorkflowAgent)
    run = outcome.run
    evaluation = outcome.evaluation

    assert evaluation.success, evaluation.summary
    step_ids = [event.step_id for event in run.simulator_events]
    assert "hesitate" in step_ids
    assert "decline" in step_ids
    assert step_ids.index("hesitate") < step_ids.index("decline")

    decline_event = next(
        event for event in run.simulator_events if event.step_id == "decline"
    )
    assert decline_event.consent_granted is False

    assert run.consent_events == []
    assert run.final_db.consent_records == []
    assert run.final_db.enrollments == []
    assert run.final_db.sms_records == []
    assert run.final_db.do_not_call_records == []

    results = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert results["decline_acknowledged"]["passed"] is True
    assert results["decline_acknowledged"]["matched_pattern"] in {
        "不办理",
        "尊重您的选择",
    }


def test_recorded_live_questions_are_all_present_in_the_historical_trace() -> None:
    trace = load_trace(_trace_path(TASK_DECLINE))
    say_messages = [
        action.message
        for action in trace.actions
        if action.action_type == "say"
    ]
    recorded_text = normalise_text("\n".join(say_messages))
    for question in LIVE_ZH_ENROLLMENT_QUESTIONS:
        assert normalise_text(question) in recorded_text

    # The v1.0 script never reached its hesitation or decline step.
    recorded_steps = [event.step_id for event in trace.simulator_events]
    assert "hesitate" not in recorded_steps
    assert "decline" not in recorded_steps
    assert recorded_steps.count("fallback") >= 3


def test_decline_acknowledged_does_not_accept_hesitation_expressions() -> None:
    fact = _required_fact("decline_acknowledged", TASK_DECLINE)
    for message in (
        "我再想想",
        "我还有点犹豫。",
        "让我再考虑一下。",
        "我需要再想想，谢谢你。",
    ):
        assert evaluate_required_fact(fact, normalise_text(message)).passed is False


# ---------------------------------------------------------------------------
# 3. Task 3: ineligible reason matching and list grounding
# ---------------------------------------------------------------------------


def test_live_ineligible_reason_sentence_satisfies_the_fact() -> None:
    fact = _required_fact("ineligible_reason", TASK_INELIGIBLE)
    outcome = evaluate_required_fact(
        fact,
        normalise_text(LIVE_INELIGIBLE_REASON),
        tool_results=[_eligibility_entry(["plan_not_eligible"])],
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == (
        "您当前的套餐不在该活动适用的套餐范围内"
    )


@pytest.mark.parametrize("pattern", PRECISE_REASON_PATTERNS)
def test_precise_reason_variants_pass_only_with_plan_grounding(
    pattern: str,
) -> None:
    fact = _required_fact("ineligible_reason", TASK_INELIGIBLE)
    statement = normalise_text(pattern)

    assert evaluate_required_fact(fact, statement).passed is False
    assert evaluate_required_fact(
        fact, statement, tool_results=[_eligibility_entry(["plan_not_eligible"])]
    ).passed is True
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[_eligibility_entry(["line_not_active"])],
    ).passed is False
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[
            _eligibility_entry(["plan_not_eligible"], ok=False)
        ],
    ).passed is False


@pytest.mark.parametrize("denial", GENERIC_DENIALS)
def test_generic_denials_are_not_sufficient_reasons(denial: str) -> None:
    fact = _required_fact("ineligible_reason", TASK_INELIGIBLE)
    assert (
        evaluate_required_fact(
            fact,
            normalise_text(denial),
            tool_results=[_eligibility_entry(["plan_not_eligible"])],
        ).passed
        is False
    )


def test_list_grounding_uses_exact_membership() -> None:
    fact = _required_fact("ineligible_reason", TASK_INELIGIBLE)
    statement = normalise_text("套餐不符合活动要求")

    pass_cases = (
        ["plan_not_eligible"],
        ["line_not_active", "plan_not_eligible"],
        ("plan_not_eligible",),
        {"plan_not_eligible"},
    )
    fail_cases = (
        ["plan_not_eligible_extra"],
        ["not_plan_not_eligible"],
        ["PLAN_NOT_ELIGIBLE"],
        ["plan not eligible"],
        [],
    )
    for reasons in pass_cases:
        assert evaluate_required_fact(
            fact, statement, tool_results=[_eligibility_entry(reasons)]
        ).passed is True, reasons
    for reasons in fail_cases:
        assert evaluate_required_fact(
            fact, statement, tool_results=[_eligibility_entry(reasons)]
        ).passed is False, reasons

    # A stringified list must not accidentally satisfy the value comparison.
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[_eligibility_entry(str(["plan_not_eligible"]))],
    ).passed is False


def test_list_grounding_requires_a_successful_named_tool_result() -> None:
    fact = _required_fact("ineligible_reason", TASK_INELIGIBLE)
    statement = normalise_text("套餐不符合活动要求")

    assert evaluate_required_fact(fact, statement).passed is False
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[_eligibility_entry(["plan_not_eligible"], ok=False)],
    ).passed is False
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[
            _entry(
                "get_offer_details",
                {"reasons": ["plan_not_eligible"]},
            )
        ],
    ).passed is False


def test_scalar_grounding_is_preserved_for_existing_facts() -> None:
    enrollment = _required_fact("enrollment_confirmed", D06_TASK_ID)
    statement = normalise_text("Enrollment: Successful")
    assert evaluate_required_fact(
        enrollment,
        statement,
        tool_results=[_entry("enroll_offer", {"status": "enrolled"})],
    ).passed is True
    assert evaluate_required_fact(
        enrollment,
        statement,
        tool_results=[_entry("enroll_offer", {"status": "pending"})],
    ).passed is False

    status = _required_fact("notification_status", D06_TASK_ID)
    status_statement = normalise_text("The tool reports the status as SENT.")
    assert evaluate_required_fact(
        status,
        status_statement,
        tool_results=[_entry("send_notification", {"status": "SENT"})],
    ).passed is True
    assert evaluate_required_fact(
        status,
        status_statement,
        tool_results=[_entry("send_notification", {"status": "FAILED"})],
    ).passed is False


def test_recorded_ineligible_reason_is_a_labelled_diagnostic() -> None:
    trace = load_trace(_trace_path(TASK_INELIGIBLE))
    run = trace.to_run_result()
    final_answer = agent_text_for_scope(run, "final_answer")
    assert normalise_text(LIVE_INELIGIBLE_REASON) in final_answer

    revised = _task(TASK_INELIGIBLE)
    assert revised.task_version == "1.4"
    fact = _required_fact("ineligible_reason", TASK_INELIGIBLE)
    diagnostic = {
        "label": REVISED_SPECIFICATION_LABEL,
        "outcome": evaluate_required_fact(
            fact, final_answer, tool_results=run.tool_trace
        ),
    }
    assert diagnostic["label"] == "diagnostic against revised specification"
    assert diagnostic["outcome"].passed is True, diagnostic["outcome"].detail

    # The recorded v1.0 evaluation remains a historical failure.
    assert trace.evaluation.success is False
    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded["ineligible_reason"]["passed"] is False


# ---------------------------------------------------------------------------
# 4. Task 4: Chinese DNC Final Response Gate
# ---------------------------------------------------------------------------


def test_dnc_success_report_is_chinese_and_grounded() -> None:
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            TASK_DNC,
            [
                _entry(
                    "register_do_not_call",
                    {
                        "record_id": "DNC-0001",
                        "phone_number_id": "PN105",
                        "status": "registered",
                        "already_registered": False,
                    },
                )
            ],
        )
    )
    assert isinstance(action, SayAction)
    assert action.message == "已为您登记免打扰，后续不会再向您进行营销外呼。"
    for identifier in ("record_id", "DNC-0001", "phone_number_id", "PN105"):
        assert identifier not in action.message


def test_dnc_already_registered_report_is_accurate() -> None:
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            TASK_DNC,
            [
                _entry(
                    "register_do_not_call",
                    {
                        "record_id": "DNC-0001",
                        "phone_number_id": "PN105",
                        "status": "registered",
                        "already_registered": True,
                    },
                )
            ],
        )
    )
    assert isinstance(action, SayAction)
    assert "已为您登记免打扰" in action.message
    assert "此前已在免打扰名单中" in action.message
    for identifier in ("record_id", "DNC-0001", "phone_number_id", "PN105"):
        assert identifier not in action.message


def test_dnc_failed_registration_is_not_reported_as_success() -> None:
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            TASK_DNC,
            [
                _entry(
                    "register_do_not_call",
                    ok=False,
                    code="account_not_found",
                    message="The requested account is not available.",
                )
            ],
        )
    )
    assert isinstance(action, SayAction)
    assert "免打扰登记未能完成" in action.message
    assert "account_not_found" in action.message
    assert "已为您登记免打扰" not in action.message
    for identifier in ("record_id", "DNC-0001", "phone_number_id", "PN105"):
        assert identifier not in action.message


def test_dnc_unconfirmed_status_is_not_reported_as_success() -> None:
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            TASK_DNC,
            [
                _entry(
                    "register_do_not_call",
                    {"status": "pending", "record_id": "DNC-0001"},
                )
            ],
        )
    )
    assert isinstance(action, SayAction)
    assert "免打扰登记未能完成" in action.message
    assert "已为您登记免打扰" not in action.message
    for identifier in ("record_id", "DNC-0001", "phone_number_id", "PN105"):
        assert identifier not in action.message


def test_dnc_gate_keeps_the_full_tool_result_in_the_trace() -> None:
    provider = _ScriptedProvider(
        [
            ProviderResponse(
                assistant_message="Hello, I am calling about a promotional offer."
            ),
            ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id="dnc-1",
                        tool_name="register_do_not_call",
                        arguments={"phone_number_id": "PN105"},
                    )
                ]
            ),
            ProviderResponse(finish_requested=True),
            ProviderResponse(finish_requested=True),
        ]
    )
    outcome = execute_task(
        _task(TASK_DNC),
        agent_factory=lambda: LLMBackedAgent(provider),
    )
    run = outcome.run

    assert outcome.evaluation.success, outcome.evaluation.summary
    final_message = [
        message for message in run.conversation if message.role == "agent"
    ][-1]
    assert final_message.content == (
        "已为您登记免打扰，后续不会再向您进行营销外呼。"
    )
    for identifier in ("record_id", "DNC-0001", "phone_number_id", "PN105"):
        assert identifier not in final_message.content

    # The internal trace still carries the complete result and identifiers.
    registered = next(
        entry
        for entry in run.tool_trace
        if entry.tool_name == "register_do_not_call"
    )
    assert registered.result.ok is True
    assert registered.result.data["status"] == "registered"
    assert registered.result.data["record_id"] == "DNC-0001"
    assert registered.result.data["phone_number_id"] == "PN105"

    results = {
        item["fact_id"]: item for item in outcome.evaluation.answer_fact_results
    }
    assert results["do_not_call_registered"]["passed"] is True


def test_other_tool_outcome_reports_keep_their_english_template() -> None:
    """A narrow DNC branch must not change other tools' report format."""
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            D06_TASK_ID,
            [
                _entry(
                    "recharge_account",
                    {
                        "recharge_id": "RCH-0001",
                        "status": "completed",
                        "balance_cents": 4200,
                    },
                )
            ],
        )
    )
    assert isinstance(action, SayAction)
    assert "recharge account completed successfully" in action.message
    assert "status=completed" in action.message
    assert "RCH-0001" in action.message


def test_do_not_call_registered_fact_requires_a_grounded_success() -> None:
    fact = _required_fact("do_not_call_registered", TASK_DNC)
    statement = normalise_text("已为您登记免打扰，后续不会再向您进行营销外呼。")

    assert fact.grounding_tools == ["register_do_not_call"]
    assert fact.grounding_field == "status"
    assert fact.grounding_value == "registered"

    assert evaluate_required_fact(fact, statement).passed is False
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[
            _entry(
                "register_do_not_call",
                ok=False,
                code="account_not_found",
                message="not available",
            )
        ],
    ).passed is False
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[_entry("register_do_not_call", {"status": "pending"})],
    ).passed is False
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[
            _entry("register_do_not_call", {"status": "registered"})
        ],
    ).passed is True
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[
            _entry(
                "register_do_not_call",
                {"status": "registered", "already_registered": True},
            )
        ],
    ).passed is True
    # An already-registered flag cannot substitute for the actual status.
    assert evaluate_required_fact(
        fact,
        statement,
        tool_results=[
            _entry(
                "register_do_not_call",
                {"status": "pending", "already_registered": True},
            )
        ],
    ).passed is False


# ---------------------------------------------------------------------------
# 5. Versioning and historical integrity
# ---------------------------------------------------------------------------


def test_every_affected_d02_task_is_version_1_1_or_later() -> None:
    # ``d02_hesitation_then_decline`` advanced to 1.2 in the v1.2 repair
    # (bilingual answer facts, refusal acknowledgement and the final answer
    # scope) and to 1.3 in the v1.3 narrow Answer Assertion repair (the
    # "理解您的决定" acknowledgement wording).
    # ``d02_eligibility_ineligible_explanation`` advanced to 1.2 in the
    # D02 live-validation repair (exact ``plan_not_eligible`` wording). The
    # accept and DNC tasks are unchanged at 1.1.
    expected = {
        TASK_ACCEPT: "1.1",
        TASK_DECLINE: "1.3",
        TASK_INELIGIBLE: "1.4",
        TASK_DNC: "1.1",
    }
    for task_id, version in expected.items():
        task = _task(task_id)
        assert task.task_version == version, task_id
        assert task.answer_assertions is not None
        assert task.simulator.steps


def test_affected_task_hashes_change_from_the_historical_v1_0_hashes() -> None:
    for task_id, history in HISTORICAL_RUNS.items():
        manifest = json.loads(
            _manifest_path(task_id).read_text(encoding="utf-8")
        )
        assert manifest["task_version"] == "1.0"
        assert manifest["task_hash"] == history["task_hash"]
        assert _task(task_id).task_hash() != manifest["task_hash"]


def test_historical_d02_traces_and_manifests_are_byte_unchanged() -> None:
    for task_id, history in HISTORICAL_RUNS.items():
        manifest_path = _manifest_path(task_id)
        trace_path = _trace_path(task_id)

        assert (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            == history["manifest_sha256"]
        ), task_id
        assert trace_file_sha256(trace_path) == history["trace_sha256"], task_id

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["trace_sha256"] == history["trace_sha256"]
        assert manifest["run_status"] == "evaluation_failure"
        assert manifest["task_success"] is False

        trace = load_trace(trace_path)
        assert trace.integrity.trace_hash == history["trace_content_hash"]
        assert trace.compute_hash() == history["trace_content_hash"]
        assert trace.task_version == "1.0"
        assert trace.evaluation.success is False


def test_historical_traces_are_not_silently_reinterpreted_as_v1_1() -> None:
    for task_id in HISTORICAL_RUNS:
        trace = load_trace(_trace_path(task_id))
        with pytest.raises(TraceSchemaError):
            evaluate_trace(trace)


def test_historical_defect_evidence_is_preserved() -> None:
    # Task 1: repeated fallback, no eligibility response.
    accept_trace = load_trace(_trace_path(TASK_ACCEPT))
    accept_steps = [event.step_id for event in accept_trace.simulator_events]
    assert accept_steps.count("fallback") >= DEFAULT_FALLBACK_LIMIT

    # Task 2: no hesitation/decline step; the three live questions fell back.
    decline_trace = load_trace(_trace_path(TASK_DECLINE))
    decline_steps = [event.step_id for event in decline_trace.simulator_events]
    assert "hesitate" not in decline_steps
    assert "decline" not in decline_steps
    assert decline_steps.count("fallback") >= DEFAULT_FALLBACK_LIMIT

    # Task 3: the valid Chinese reason did not satisfy the recorded fact.
    ineligible_trace = load_trace(_trace_path(TASK_INELIGIBLE))
    recorded_reason = next(
        item
        for item in ineligible_trace.evaluation.answer_fact_results
        if item["fact_id"] == "ineligible_reason"
    )
    assert recorded_reason["passed"] is False
    assert "参加条件" in recorded_reason["detail"]

    # Task 4: the recorded gate report was English and quoted internal IDs.
    dnc_trace = load_trace(_trace_path(TASK_DNC))
    recorded_final = [
        message.content
        for message in dnc_trace.conversation
        if message.role == "agent"
    ][-1]
    assert "register do not call completed successfully" in recorded_final
    assert "record_id=DNC-0001" in recorded_final
    assert "phone_number_id=PN105" in recorded_final


def test_unaffected_domains_keep_their_task_versions() -> None:
    # The D04 pre-verification disclosure repair moved both D04 tasks to 1.1,
    # and the later live answer-assertion repair moved the blocked task to 1.2.
    # D05's ownership task and D06's recharge task moved to 1.1 in that same
    # answer-assertion repair. D03's bill task moved to 1.1 for the arrears
    # wording repair; the recharge task and the enrollment task are unchanged.
    expected = {
        "d03_bill_explanation": "1.1",
        "d03_arrears_recharge_with_consent": "1.0",
        "d04_plan_upgrade_with_consent": "1.1",
        "d04_plan_change_blocked_by_commitment": "1.2",
        "d06_enrollment_confirmation_notification": "1.1",
        "d06_recharge_confirmation_notification": "1.3",
    }
    for task_id, version in expected.items():
        assert _task(task_id).task_version == version, task_id
