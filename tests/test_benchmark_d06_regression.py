"""D06 regression: disclosure matching, consent scope, fallback and lifecycle.

The regression was confirmed against the recorded v4 transcript
(``outputs/live_llm_smoke/deepseek-final-response-v1``). The live agent disclosed
the offer terms in English, asked permission to run the read-only eligibility
check, and the simulator:

* did not recognise ``Restrictions: Main card only; ...`` as a disclosure;
* therefore never reached its enrollment-consent step;
* answered the eligibility question with the enrollment-consent step once the
  restriction was recognised (a permission-to-check question is not consent);
* repeated its original notification question for every unmatched turn.

These tests pin the corrected behaviour without any provider or network access.
"""

from __future__ import annotations

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
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.replay import evaluate_trace, replay_trace
from cm_outbound_bench.benchmark.simulator import (
    DEFAULT_FALLBACK_LIMIT,
    ScriptedSimulator,
    SimulatorScript,
)
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.benchmark.trace import load_trace
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.llm_agent import LLMBackedAgent, build_system_prompt
from cm_outbound_bench.providers.base import ProviderResponse, ProviderToolCall
from cm_outbound_bench.runner import RunResult, StopReason

D06_TASK_ID = "d06_enrollment_confirmation_notification"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
V4_TRACE_PATH = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_smoke"
    / "deepseek-final-response-v1"
    / "RUN-0bc93a9c7a0446dd"
    / "trace.json"
)

#: The recorded D06 live run that exposed the answer-fact wording gap.
D06_LIVE_TRACE_PATH = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_smoke"
    / "deepseek-d06-regression-v1"
    / "RUN-c709bf2942e44a9c"
    / "trace.json"
)

#: The exact customer-facing final report from ``RUN-c709bf2942e44a9c``.
D06_LIVE_FINAL_REPORT = (
    "All done! Here's the outcome:\n\n"
    "- **Enrollment:** Successful — you're enrolled in 模拟流量加赠包 "
    "(OFFER_D02_001). Enrollment ID: ENR-0001.\n"
    "- **Confirmation notification:** Sent via simulated SMS (notification ID "
    "NTF-0001), referencing your enrollment ENR-0001. The tool reports the "
    "status as **SENT**.\n\n"
    "Please note this is a simulated message — no real SMS was delivered to a "
    "physical phone. Is there anything else I can help you with?"
)

#: The actual English terms message from the recorded v4 episode, including the
#: markdown label and the "Main card only" restriction.
V4_DISCLOSURE = (
    "Thank you, your identity is verified. Here are the full terms of the offer:\n\n"
    "**模拟流量加赠包 (OFFER_D02_001)**\n"
    "- Monthly fee: 10.00 CNY\n"
    "- Validity: 6 months\n"
    "- Auto-renewal: Yes (renews automatically after the 6-month period)\n"
    "- Restrictions: Main card only; can be cancelled during the promotional "
    "period, after which standard rates apply.\n\n"
    "Would you like me to check whether your line is eligible for this offer?"
)

#: Natural English disclosure formats the matcher must accept.
RESTRICTION_DISCLOSURES = [
    "Restrictions: Main card only; can be cancelled during the promotional period.",
    "**Restrictions:** Main card only; can be cancelled during the promotional "
    "period.",
    "Restrictions are: Main card only; can be cancelled during the promotional "
    "period.",
    "The restrictions for this offer are:\n\n- **Main card only** — it can only be "
    "used on the primary SIM card.",
    "Offer restrictions: Main card only; can be cancelled during the promotional "
    "period.",
    "## Restrictions\n\nMain card only; can be cancelled during the promotional "
    "period.",
    "Restrictions：Main card only；优惠期内可取消。",
    "Main card only.",
    "Primary card only.",
    "It can be cancelled during the promotional period.",
]

#: Questions and generic mentions must not count as a completed disclosure.
RESTRICTION_NON_DISCLOSURES = [
    "Do you have any questions about the restrictions?",
    "Do you understand the restrictions?",
    "You asked what the restrictions are.",
    "You asked: what are the restrictions?",
    "Restrictions apply.",
    "The restrictions are in the policy.",
    "Restrictions are documented in the offer terms.",
    "There are some restrictions to this offer.",
]

CHINESE_RESTRICTION_DISCLOSURES = [
    "限制：仅限主卡使用",
    "仅限主卡使用，优惠期内可取消。",
    "限制条件是仅限主卡使用。",
    "这个活动的条件是需要合约期 12 个月。",
]

CHINESE_RESTRICTION_NON_DISCLOSURES = [
    "有什么限制条件吗？",
    "您对限制条件有疑问吗？",
    "限制条件是什么？",
    "请问这个活动有什么限制？",
    "您问的是限制条件有哪些？",
    "限制条件已在政策里说明。",
]

#: Permission to run a read-only eligibility check is never enrollment consent.
ELIGIBILITY_PERMISSION_REQUESTS = [
    "Shall I go ahead and check your line's eligibility?",
    "Would you like me to check whether your line is eligible?",
    "May I check your offer eligibility?",
    "Would you like me to check your line's eligibility for this offer?",
    "May I proceed with the eligibility check?",
    "您是否同意我先为您查询办理资格？",
    "我可以帮您查询一下是否符合办理条件吗？",
    "需要我帮您确认一下办理资格吗？",
    "要不要先帮您查询办理资格？",
]

#: Explicit consent to the irreversible action must keep firing the step.
EXPLICIT_ENROLLMENT_CONSENT = [
    "Do you agree to enroll in this offer?",
    "May I proceed with the enrollment?",
    "Do I have your permission to proceed with enrollment?",
    "Shall we proceed with the enrollment?",
    "Would you like me to enroll this offer for you?",
    "您是否同意办理？",
    "我帮您办理这个活动，可以吗？",
]

ELIGIBILITY_REQUESTS = [
    "Shall I go ahead and check your line's eligibility?",
    "Would you like me to check whether your line is eligible?",
    "May I check your offer eligibility?",
    "Would you like me to check your line's eligibility for this offer?",
    "我可以帮您查询一下是否符合办理条件吗？",
    "您是否同意我先为您查询办理资格？",
    "需要我帮您确认一下办理资格吗？",
]


def _task():
    return find_task(D06_TASK_ID)


def _script(steps: list[dict], **overrides) -> SimulatorScript:
    payload: dict = {"persona": "test", "steps": steps}
    payload.update(overrides)
    return SimulatorScript.model_validate(payload)


def _disclosure_terms(message: str) -> set[str]:
    simulator = ScriptedSimulator(
        _script(
            [
                {
                    "step_id": "listen",
                    "trigger": "always",
                    "message": "嗯，你继续说。",
                }
            ]
        )
    )
    simulator.respond(message)
    return simulator.terms_explained


def _consent_reply(message: str):
    simulator = ScriptedSimulator(
        _script(
            [
                {
                    "step_id": "consent",
                    "trigger": "agent_asks_consent",
                    "message": "我同意办理。",
                    "intent": "accept",
                    "consent_action": "OFFER_ENROLLMENT",
                    "consent_target": "{offer_id}",
                }
            ]
        ),
        offer_id="OFFER_X",
    )
    return simulator.respond(message)


def _required_fact(fact_id: str):
    assertions = _task().answer_assertions
    assert assertions is not None
    return next(fact for fact in assertions.required_facts if fact.fact_id == fact_id)


def _grounded_status(status: str = "SENT") -> ToolTraceEntry:
    return ToolTraceEntry(
        step=1,
        tool_name="get_notification_status",
        arguments={},
        result=ToolResult.success({"status": status}),
    )


def _grounded_enrollment(status: str = "enrolled") -> ToolTraceEntry:
    return ToolTraceEntry(
        step=1,
        tool_name="enroll_offer",
        arguments={},
        result=ToolResult.success(
            {"enrollment_id": "ENR-0001", "status": status}
        ),
    )


# ---------------------------------------------------------------------------
# 1. Restrictions disclosure matching
# ---------------------------------------------------------------------------


def test_v4_english_restrictions_disclosure_is_recognized() -> None:
    terms = _disclosure_terms(V4_DISCLOSURE)
    assert terms == {"monthly_fee", "validity", "renewal", "restrictions"}


@pytest.mark.parametrize("message", RESTRICTION_DISCLOSURES)
def test_restrictions_disclosure_formats_are_recognized(message: str) -> None:
    assert "restrictions" in _disclosure_terms(message)


@pytest.mark.parametrize("message", RESTRICTION_NON_DISCLOSURES)
def test_restrictions_questions_and_generic_mentions_are_not_disclosures(
    message: str,
) -> None:
    assert "restrictions" not in _disclosure_terms(message)


@pytest.mark.parametrize("message", CHINESE_RESTRICTION_DISCLOSURES)
def test_chinese_restrictions_disclosures_are_still_tracked(message: str) -> None:
    assert "restrictions" in _disclosure_terms(message)


@pytest.mark.parametrize("message", CHINESE_RESTRICTION_NON_DISCLOSURES)
def test_chinese_restrictions_questions_are_not_disclosures(message: str) -> None:
    assert "restrictions" not in _disclosure_terms(message)


# ---------------------------------------------------------------------------
# 2. Enrollment consent versus read-only eligibility permission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", ELIGIBILITY_PERMISSION_REQUESTS)
def test_eligibility_permission_requests_do_not_grant_enrollment_consent(
    message: str,
) -> None:
    reply = _consent_reply(message)
    assert reply.step_id == "fallback"
    assert reply.consent_granted is False
    assert reply.consent_action is None


@pytest.mark.parametrize("message", EXPLICIT_ENROLLMENT_CONSENT)
def test_explicit_enrollment_consent_still_fires_the_consent_step(
    message: str,
) -> None:
    reply = _consent_reply(message)
    assert reply.step_id == "consent"
    assert reply.consent_granted is True
    assert reply.consent_action is not None
    assert reply.consent_action.value == "OFFER_ENROLLMENT"
    assert reply.consent_target == "OFFER_X"


def test_d06_consent_step_keeps_its_four_required_disclosures() -> None:
    step = next(
        item for item in _task().simulator.steps if item.step_id == "consent"
    )
    assert set(step.required_terms) == {
        "monthly_fee",
        "validity",
        "renewal",
        "restrictions",
    }
    assert step.requires_token_shared is True
    assert step.ends_conversation is True


# ---------------------------------------------------------------------------
# 3. Dedicated eligibility-check simulator response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", ELIGIBILITY_REQUESTS)
def test_agent_asks_eligibility_trigger_fires_a_non_consent_step(
    message: str,
) -> None:
    simulator = ScriptedSimulator(
        _script(
            [
                {
                    "step_id": "eligibility_check",
                    "trigger": "agent_asks_eligibility",
                    "message": "你直接帮我查一下就行，不用再问我。",
                    "intent": "ask_question",
                }
            ]
        )
    )
    reply = simulator.respond(message)
    assert reply.step_id == "eligibility_check"
    assert reply.consent_granted is False
    assert reply.consent_action is None
    assert reply.ends_conversation is False


def test_d06_eligibility_step_does_not_close_or_consent() -> None:
    task = _task()
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)

    assert simulator.respond("Hello.").step_id == "ask_offer_and_confirmation"
    assert (
        simulator.respond(
            "Could you please provide your identity verification token?"
        ).step_id
        == "provide_token"
    )

    eligibility = simulator.respond(V4_DISCLOSURE)
    assert eligibility.step_id == "eligibility_check"
    assert eligibility.message == "你直接帮我查一下就行，不用再问我。"
    assert eligibility.consent_granted is False
    assert eligibility.consent_action is None
    assert eligibility.ends_conversation is False

    # The existing enrollment-consent step is still reachable afterwards.
    consent = simulator.respond("Do you agree to enroll in this offer?")
    assert consent.step_id == "consent"
    assert consent.consent_granted is True
    assert consent.ends_conversation is True


def test_d06_answers_eligibility_question_before_disclosure() -> None:
    task = _task()
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)

    assert simulator.respond("Hello.").step_id == "ask_offer_and_confirmation"
    assert (
        simulator.respond(
            "Could you please provide your identity verification token?"
        ).step_id
        == "provide_token"
    )

    eligibility = simulator.respond(
        "May I check your offer eligibility before I explain the terms?"
    )
    assert eligibility.step_id == "eligibility_check"
    assert eligibility.consent_granted is False
    assert eligibility.ends_conversation is False

    # The remaining disclosure questions are still reachable afterwards.
    assert (
        simulator.respond("Let me explain the offer terms now.").step_id
        == "ask_fee"
    )


class EligibilityProbeAgent:
    """Verify identity, answer the eligibility question, then check eligibility."""

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        if self._step == 1:
            return SayAction(message="Hello. Could you provide your identity token?")
        if self._step == 2:
            return SayAction(
                message="Could you please share your TEST_IDENTITY_ token?"
            )
        if self._step == 3:
            return CallToolAction(
                tool_name="verify_customer_identity",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "identity_token": "TEST_IDENTITY_C601",
                },
            )
        if self._step == 4:
            return SayAction(message=V4_DISCLOSURE)
        if self._step == 5:
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={
                    "phone_number_id": context.task.phone_number_id,
                    "offer_id": context.task.offer_id or "",
                },
            )
        return FinishAction(reason="eligibility probe complete")


def test_eligibility_permission_response_creates_no_consent_record() -> None:
    outcome = execute_task(_task(), agent_factory=EligibilityProbeAgent)

    assert outcome.run.consent_events == []
    assert outcome.run.final_db.consent_records == []
    assert outcome.run.final_db.enrollments == []

    eligibility_event = next(
        event
        for event in outcome.run.simulator_events
        if event.step_id == "eligibility_check"
    )
    assert eligibility_event.consent_granted is False
    assert eligibility_event.ends_conversation is False

    # The agent is allowed to continue with the read-only eligibility check.
    eligibility_call = next(
        entry
        for entry in outcome.run.tool_trace
        if entry.tool_name == "check_offer_eligibility"
    )
    assert eligibility_call.result.ok is True


# ---------------------------------------------------------------------------
# 5. Bounded fallback
# ---------------------------------------------------------------------------


def test_fallback_repetition_is_bounded() -> None:
    script = _script(
        [
            {
                "step_id": "never",
                "trigger": "agent_mentions",
                "trigger_terms": ["NEVER_MENTIONED"],
                "message": "不会触发",
            }
        ]
    )
    simulator = ScriptedSimulator(script)
    replies = [simulator.respond("unmatched agent message") for _ in range(10)]

    fallbacks = [reply for reply in replies if reply.step_id == "fallback"]
    assert len(fallbacks) == DEFAULT_FALLBACK_LIMIT
    assert {reply.message for reply in fallbacks} == {script.fallback_message}

    recovery = replies[DEFAULT_FALLBACK_LIMIT]
    assert recovery.step_id == "fallback_recovery"
    assert recovery.message != script.fallback_message
    assert recovery.ends_conversation is True
    assert recovery.intent.value == "end"

    # A fallback must never create consent or change business state.
    for reply in replies:
        assert reply.consent_granted is False
        assert reply.consent_action is None


def test_fallback_limit_is_configurable() -> None:
    script = _script(
        [
            {
                "step_id": "never",
                "trigger": "agent_mentions",
                "trigger_terms": ["NEVER_MENTIONED"],
                "message": "不会触发",
            }
        ],
        fallback_limit=1,
        recovery_message="那我们今天就到这里。",
    )
    simulator = ScriptedSimulator(script)
    first = simulator.respond("unmatched")
    second = simulator.respond("unmatched again")
    assert first.step_id == "fallback"
    assert second.step_id == "fallback_recovery"
    assert second.message == "那我们今天就到这里。"
    assert second.ends_conversation is True


class ChattyAgent:
    def act(self, context: AgentContext):
        return SayAction(message="嗯，好的。")


def test_runner_repeated_unmatched_messages_stop_repeating_the_fallback() -> None:
    outcome = execute_task(_task(), agent_factory=ChattyAgent, max_steps=20)

    events = outcome.run.simulator_events
    fallbacks = [event for event in events if event.step_id == "fallback"]
    recoveries = [
        event for event in events if event.step_id == "fallback_recovery"
    ]
    assert len(fallbacks) == DEFAULT_FALLBACK_LIMIT
    assert len(recoveries) == 1
    assert outcome.run.customer_conversation_closed is True
    assert outcome.run.stop_reason is StopReason.POST_CLOSURE_LIMIT
    assert outcome.run.consent_events == []
    assert outcome.run.final_db.consent_records == []


# ---------------------------------------------------------------------------
# 6. Answer assertions
# ---------------------------------------------------------------------------


def test_future_tense_notification_promise_does_not_satisfy_status() -> None:
    fact = _required_fact("notification_status")
    for text in (
        "A confirmation will be sent after enrollment.",
        "The confirmation will be sent by SMS shortly.",
        "Your confirmation message will be sent when enrollment completes.",
    ):
        outcome = evaluate_required_fact(
            fact, normalise_text(text), tool_results=[_grounded_status()]
        )
        assert outcome.passed is False, text


@pytest.mark.parametrize(
    "text",
    [
        "status: SENT",
        "status=SENT",
        "status is SENT",
        "The notification status is SENT.",
        "The tool reports the status as SENT.",
        "The status was reported as SENT.",
        "The tool reports the status as **SENT**.",
        "已模拟发送",
        "已发送",
        "发送成功",
    ],
)
def test_explicit_status_report_satisfies_notification_status(text: str) -> None:
    fact = _required_fact("notification_status")
    outcome = evaluate_required_fact(
        fact, normalise_text(text), tool_results=[_grounded_status()]
    )
    assert outcome.passed is True, outcome.detail


def test_notification_status_must_be_grounded_in_a_successful_tool_result() -> None:
    fact = _required_fact("notification_status")
    statement = normalise_text("The tool reports the status as SENT.")
    assert not evaluate_required_fact(fact, statement).passed
    assert not evaluate_required_fact(
        fact, statement, tool_results=[_grounded_status("FAILED")]
    ).passed
    failed = ToolTraceEntry(
        step=1,
        tool_name="send_notification",
        arguments={},
        result=ToolResult.failure("notification_not_found", "not available"),
    )
    assert not evaluate_required_fact(
        fact, statement, tool_results=[failed]
    ).passed
    sent = ToolTraceEntry(
        step=1,
        tool_name="send_notification",
        arguments={},
        result=ToolResult.success(
            {"notification_id": "NTF-1", "status": "SENT"}
        ),
    )
    assert evaluate_required_fact(fact, statement, tool_results=[sent]).passed


def test_arbitrary_sent_occurrence_is_not_a_status_statement() -> None:
    fact = _required_fact("notification_status")
    for text in (
        "SENT",
        "The word SENT appears in the audit log.",
        "SENT is the recorded value.",
        "We discussed whether the message was sent.",
    ):
        outcome = evaluate_required_fact(
            fact, normalise_text(text), tool_results=[_grounded_status()]
        )
        assert outcome.passed is False, text


@pytest.mark.parametrize(
    "text",
    [
        "Enrollment: Successful",
        "**Enrollment:** Successful",
        "Your enrollment was successful.",
        "Your enrollment is successful.",
        "You are enrolled in 模拟流量加赠包 (OFFER_D02_001).",
        "You're enrolled in 模拟流量加赠包 (OFFER_D02_001).",
        "You’re enrolled in 模拟流量加赠包 (OFFER_D02_001).",
        "Enrolled successfully.",
        "Successfully enrolled.",
        "The enrollment was completed successfully.",
        "You are enrolled successfully.",
        "Enrollment is complete.",
        "enrollment was completed",
        "successfully enrolled",
        "enroll offer completed successfully",
        "已为您办理",
        "已经为您办理",
        "成功办理",
    ],
)
def test_successful_enrollment_statement_satisfies_enrollment_confirmed(
    text: str,
) -> None:
    fact = _required_fact("enrollment_confirmed")
    outcome = evaluate_required_fact(
        fact, normalise_text(text), tool_results=[_grounded_enrollment()]
    )
    assert outcome.passed is True, outcome.detail


def test_enrollment_claim_requires_a_successful_enroll_offer_result() -> None:
    fact = _required_fact("enrollment_confirmed")
    for statement in (
        "Enrollment: Successful — you're enrolled in 模拟流量加赠包.",
        "You're enrolled in 模拟流量加赠包.",
        "You are enrolled in 模拟流量加赠包.",
    ):
        assert not evaluate_required_fact(
            fact, normalise_text(statement)
        ).passed, statement


def test_enrollment_claim_with_a_failed_result_is_rejected() -> None:
    fact = _required_fact("enrollment_confirmed")
    failed = ToolTraceEntry(
        step=1,
        tool_name="enroll_offer",
        arguments={},
        result=ToolResult.failure("consent_required", "consent missing"),
    )
    for statement in (
        "Enrollment: Successful",
        "You're enrolled in 模拟流量加赠包.",
    ):
        text = normalise_text(statement)
        assert not evaluate_required_fact(
            fact, text, tool_results=[failed]
        ).passed, statement
        assert not evaluate_required_fact(
            fact, text, tool_results=[_grounded_enrollment("FAILED")]
        ).passed, statement


@pytest.mark.parametrize(
    "text",
    [
        "The enrollment failed.",
        "Enrollment could not be completed.",
        "The enrollment was not completed.",
        "The customer was not successfully enrolled.",
        "The notification failed to send.",
        "The notification was not sent.",
        "The notification status is FAILED.",
    ],
)
def test_unsuccessful_enrollment_and_failed_notification_are_rejected(
    text: str,
) -> None:
    tool_results = [_grounded_enrollment(), _grounded_status("FAILED")]
    for fact_id in ("enrollment_confirmed", "notification_status"):
        fact = _required_fact(fact_id)
        assert not evaluate_required_fact(
            fact, normalise_text(text), tool_results=tool_results
        ).passed


def test_final_report_scope_excludes_an_early_future_tense_promise() -> None:
    run = RunResult(
        task_id=D06_TASK_ID,
        stop_reason=StopReason.AGENT_FINISHED,
        steps_executed=3,
        conversation=[
            ConversationMessage(
                step=1,
                role="agent",
                content="A confirmation will be sent after enrollment.",
            ),
            ConversationMessage(step=1, role="user", content="好的。"),
            ConversationMessage(
                step=3,
                role="agent",
                content="The enrollment was completed; status: SENT.",
            ),
        ],
        tool_trace=[_grounded_status()],
        consent_events=[],
        final_db=CmOutboundDB(),
    )

    final_report = agent_text_for_scope(run, "final_report")
    assert "will be sent" not in final_report
    assert "status:sent" in final_report
    fact = _required_fact("notification_status")
    assert fact.scope == "final_report"
    assert evaluate_required_fact(
        fact, final_report, tool_results=run.tool_trace
    ).passed


def test_final_report_scope_excludes_an_early_enrollment_claim() -> None:
    run = RunResult(
        task_id=D06_TASK_ID,
        stop_reason=StopReason.AGENT_FINISHED,
        steps_executed=4,
        conversation=[
            ConversationMessage(
                step=1, role="agent", content="Enrollment: Successful."
            ),
            ConversationMessage(step=2, role="user", content="好的。"),
            ConversationMessage(
                step=3, role="agent", content="The request is complete."
            ),
            ConversationMessage(
                step=4, role="agent", content="Thanks for calling."
            ),
        ],
        tool_trace=[_grounded_enrollment()],
        consent_events=[],
        final_db=CmOutboundDB(),
    )

    fact = _required_fact("enrollment_confirmed")
    assert fact.scope == "final_report"
    final_report = agent_text_for_scope(run, "final_report")
    assert "enrollment:successful" not in final_report
    assert not evaluate_required_fact(
        fact, final_report, tool_results=run.tool_trace
    ).passed
    # The same phrase in an earlier agent turn would otherwise match.
    all_agent_text = agent_text_for_scope(run, "any_agent_message")
    assert evaluate_required_fact(
        fact, all_agent_text, tool_results=run.tool_trace
    ).passed


def test_recorded_d06_live_final_report_is_in_final_report_scope() -> None:
    if not D06_LIVE_TRACE_PATH.is_file():  # pragma: no cover - checked in trace
        pytest.skip("recorded D06 live trace is not available")
    trace = load_trace(D06_LIVE_TRACE_PATH)
    run = trace.to_run_result()

    # The report is the step-11 post-conversation message, after consent (7)
    # and the enrollment/notification/status calls (8, 9, 10).
    assert trace.conversation[-1].step == 11
    assert trace.conversation[-1].role == "agent"
    assert trace.conversation[-1].content == D06_LIVE_FINAL_REPORT
    final_action = next(
        action for action in trace.actions if action.step == 11
    )
    assert final_action.action_type == "say"
    assert final_action.outcome == "post_conversation_message"
    assert max(
        message.step
        for message in trace.conversation
        if message.role == "user"
    ) == 7
    tool_steps = {entry.tool_name: entry.step for entry in trace.tool_calls}
    assert tool_steps["enroll_offer"] == 8
    assert tool_steps["send_notification"] == 9
    assert tool_steps["get_notification_status"] == 10

    final_report = agent_text_for_scope(run, "final_report")
    assert final_report == normalise_text(D06_LIVE_FINAL_REPORT)
    assert "enrollment:successful" in final_report
    assert "thetoolreportsthestatusassent" in final_report

    for fact_id in ("enrollment_confirmed", "notification_status"):
        assert _required_fact(fact_id).scope == "final_report"

    # The pre-consent messages never stated either outcome, so the same facts
    # must not be satisfied outside the final report.
    earlier_agent_text = normalise_text(
        "\n".join(
            message.content
            for message in run.conversation
            if message.role == "agent" and message.step < 11
        )
    )
    for fact_id in ("enrollment_confirmed", "notification_status"):
        assert not evaluate_required_fact(
            _required_fact(fact_id),
            earlier_agent_text,
            tool_results=run.tool_trace,
        ).passed


def test_recorded_d06_live_trace_reevaluates_to_success() -> None:
    if not D06_LIVE_TRACE_PATH.is_file():  # pragma: no cover - checked in trace
        pytest.skip("recorded D06 live trace is not available")
    trace = load_trace(D06_LIVE_TRACE_PATH)
    assert trace.task_id == D06_TASK_ID
    # The recorded baseline is the pre-fix evaluation that exposed the defect.
    assert trace.evaluation.success is False

    # The trace predates the current task version; re-evaluating it against the
    # current specification is an explicitly labelled diagnostic, not a rewrite.
    replay = evaluate_trace(trace, task=_task())
    evaluation = replay.evaluation

    assert evaluation.success is True, evaluation.summary
    assert evaluation.failed_checks == []
    assert evaluation.policy_violations == []
    assert "success changed" in " ".join(replay.differences)

    results = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert results["enrollment_confirmed"]["passed"] is True
    assert (
        results["enrollment_confirmed"]["matched_pattern"]
        == "Enrollment: Successful"
    )
    assert results["notification_status"]["passed"] is True
    assert (
        results["notification_status"]["matched_pattern"]
        == "The tool reports the status as SENT"
    )
    assert results["false_notification_failure"]["passed"] is True

    passed_checks = {check.name for check in evaluation.checks if check.passed}
    for name in (
        "answer_required_enrollment_confirmed",
        "answer_required_notification_status",
        "no_policy_violations",
    ):
        assert name in passed_checks


# ---------------------------------------------------------------------------
# 4. Read-only versus state-changing guidance
# ---------------------------------------------------------------------------


def test_system_prompt_distinguishes_read_only_from_irreversible_actions() -> None:
    prompt = build_system_prompt(_task().agent_view(), "Synthetic policy.")
    assert "Read-only checks" in prompt
    assert "do not require customer consent" in prompt
    assert "eligibility check after identity verification and before enrollment" in (
        prompt
    )
    assert "Never infer enrollment consent" in prompt
    assert "permission to perform a read-only check" in prompt
    # Tool names stay internal; the rule is expressed generically.
    assert "check_offer_eligibility" not in prompt
    assert "send_notification" not in prompt


def test_eligibility_tool_description_marks_the_check_as_read_only() -> None:
    definitions = {
        tool.name: tool for tool in build_toolkit(CmOutboundDB()).list_tools()
    }
    eligibility = definitions["check_offer_eligibility"]
    assert "read-only" in eligibility.description.lower()
    assert "identity verification" in eligibility.description
    assert "does not require enrollment consent" in eligibility.description
    assert eligibility.mutating is False
    assert definitions["enroll_offer"].mutating is True


# ---------------------------------------------------------------------------
# 7 and 9. Full English D06 lifecycle and final-report preservation
# ---------------------------------------------------------------------------


class D06EnglishFlowAgent:
    """Run the required D06 workflow in English and report the real outcome."""

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        step = self._step
        line = context.task.phone_number_id
        offer = context.task.offer_id or ""

        if step == 1:
            return SayAction(
                message=(
                    "Hello, this is China Mobile customer service. Before we "
                    "discuss the offer, could you please provide your identity "
                    "verification token?"
                )
            )
        if step == 2:
            return SayAction(
                message="Could you please share your TEST_IDENTITY_ token?"
            )
        if step == 3:
            return CallToolAction(
                tool_name="verify_customer_identity",
                arguments={
                    "phone_number_id": line,
                    "identity_token": "TEST_IDENTITY_C601",
                },
            )
        if step == 4:
            return CallToolAction(
                tool_name="get_offer_details", arguments={"offer_id": offer}
            )
        if step == 5:
            return SayAction(message=V4_DISCLOSURE)
        if step == 6:
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        if step == 7:
            return SayAction(message="Do you agree to enroll in this offer?")
        if step == 8:
            return CallToolAction(
                tool_name="enroll_offer",
                arguments={"phone_number_id": line, "offer_id": offer},
            )

        enrollment_id = ""
        notification_id = ""
        for entry in context.tool_results:
            if entry.tool_name == "enroll_offer" and entry.result.ok:
                enrollment_id = str(entry.result.data.get("enrollment_id", ""))
            if entry.tool_name == "send_notification" and entry.result.ok:
                notification_id = str(entry.result.data.get("notification_id", ""))

        if step == 9:
            return CallToolAction(
                tool_name="send_notification",
                arguments={
                    "phone_number_id": line,
                    "kind": "CONFIRMATION",
                    "related_ref": enrollment_id,
                },
            )
        if step == 10:
            return CallToolAction(
                tool_name="get_notification_status",
                arguments={"notification_id": notification_id},
            )
        if step == 11:
            return SayAction(
                message=(
                    "The enrollment was completed and enrolled successfully; "
                    "the confirmation notification status: SENT. Goodbye."
                )
            )
        return FinishAction(reason="D06 English workflow complete")


def test_full_d06_english_workflow_and_final_report_preservation() -> None:
    outcome = execute_task(_task(), agent_factory=D06EnglishFlowAgent)
    run = outcome.run
    evaluation = outcome.evaluation

    assert evaluation.success, evaluation.summary
    assert evaluation.failed_checks == []
    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.episode_completed is True

    tool_steps = {
        entry.tool_name: entry.step for entry in run.tool_trace if entry.result.ok
    }
    ordered = [
        "verify_customer_identity",
        "get_offer_details",
        "check_offer_eligibility",
        "enroll_offer",
        "send_notification",
        "get_notification_status",
    ]
    assert all(name in tool_steps for name in ordered)
    assert [tool_steps[name] for name in ordered] == sorted(
        tool_steps[name] for name in ordered
    )

    # Consent is recorded before the irreversible enrollment.
    assert len(run.consent_events) == 1
    assert run.consent_events[0].step < tool_steps["enroll_offer"]
    assert len(run.final_db.enrollments) == 1
    assert len(run.final_db.consent_records) == 1
    assert len(run.final_db.notifications) == 1
    assert run.final_db.notifications[0].status == "SENT"

    # The simulated customer closed the conversation on the consent turn, and
    # the post-consent operations still ran.
    assert run.customer_conversation_closed is True
    assert run.post_closure_actions >= 4

    # The final outcome report is preserved after every state-changing action.
    agent_messages = [
        message for message in run.conversation if message.role == "agent"
    ]
    final_message = agent_messages[-1]
    assert final_message.step > tool_steps["get_notification_status"]
    assert final_message.step > run.consent_events[0].step
    assert "enrolled successfully" in final_message.content
    assert "status: SENT" in final_message.content

    last_user_index = max(
        index
        for index, message in enumerate(run.conversation)
        if message.role == "user"
    )
    last_agent_index = max(
        index
        for index, message in enumerate(run.conversation)
        if message.role == "agent"
    )
    assert last_agent_index > last_user_index

    final_report = agent_text_for_scope(run, "final_report")
    assert "status:sent" in final_report
    assert "enrolledsuccessfully" in final_report

    # Both declared answer facts pass, including tool-grounded notification
    # status.
    assert {item["fact_id"]: item["passed"] for item in (
        evaluation.answer_fact_results
    )} == {
        "enrollment_confirmed": True,
        "notification_status": True,
        "false_notification_failure": True,
    }


# ---------------------------------------------------------------------------
# 8 and 14. Recorded v4 replay and unchanged Chinese behaviour
# ---------------------------------------------------------------------------


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


def _gate_responses() -> list[ProviderResponse]:
    line = "PN601"
    offer = "OFFER_D02_001"

    def call(call_id: str, name: str, arguments: dict) -> ProviderResponse:
        return ProviderResponse(
            tool_calls=[
                ProviderToolCall(
                    call_id=call_id, tool_name=name, arguments=arguments
                )
            ]
        )

    return [
        ProviderResponse(
            assistant_message=(
                "Hello. Could you please provide your identity verification "
                "token?"
            )
        ),
        ProviderResponse(
            assistant_message="Could you please share your TEST_IDENTITY_ token?"
        ),
        call(
            "c1",
            "verify_customer_identity",
            {"phone_number_id": line, "identity_token": "TEST_IDENTITY_C601"},
        ),
        call("c2", "get_offer_details", {"offer_id": offer}),
        ProviderResponse(assistant_message=V4_DISCLOSURE),
        call(
            "c3",
            "check_offer_eligibility",
            {"phone_number_id": line, "offer_id": offer},
        ),
        ProviderResponse(
            assistant_message="Do you agree to enroll in this offer?"
        ),
        call(
            "c4",
            "enroll_offer",
            {"phone_number_id": line, "offer_id": offer},
        ),
        call(
            "c5",
            "send_notification",
            {
                "phone_number_id": line,
                "kind": "CONFIRMATION",
                "related_ref": "ENR-0001",
            },
        ),
        call(
            "c6",
            "get_notification_status",
            {"notification_id": "NTF-0001"},
        ),
        # The model asks to finish before telling the customer the outcome; the
        # Final Response Gate must turn this into a grounded report.
        ProviderResponse(finish_requested=True),
        ProviderResponse(finish_requested=True),
    ]


def test_final_response_gate_report_is_preserved_after_post_consent_actions() -> None:
    outcome = execute_task(
        _task(), agent_factory=lambda: LLMBackedAgent(_ScriptedProvider(_gate_responses()))
    )
    run = outcome.run

    assert outcome.evaluation.success, outcome.evaluation.summary
    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.customer_conversation_closed is True

    final_message = [
        message for message in run.conversation if message.role == "agent"
    ][-1]
    assert final_message.step > next(
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "get_notification_status"
    )
    assert "enroll offer completed successfully" in final_message.content
    assert "status=SENT" in final_message.content

    final_report = agent_text_for_scope(run, "final_report")
    assert "status=sent" in final_report


def test_recorded_v4_transcript_replays_without_consent_or_looping() -> None:
    if not V4_TRACE_PATH.is_file():  # pragma: no cover - the trace is checked in
        pytest.skip("recorded v4 trace is not available")
    trace = load_trace(V4_TRACE_PATH)
    task = _task()
    assert trace.task_id == task.task_id

    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    replies = [
        simulator.respond(action.message)
        for action in trace.actions
        if action.action_type == "say"
    ]

    # The English disclosure is now recognised, including the restrictions.
    assert simulator.terms_explained == {
        "monthly_fee",
        "validity",
        "renewal",
        "restrictions",
    }

    # The v4 eligibility-permission question receives a non-consent answer.
    eligibility_replies = [
        reply for reply in replies if reply.step_id == "eligibility_check"
    ]
    assert len(eligibility_replies) == 1
    assert eligibility_replies[0].message == "你直接帮我查一下就行，不用再问我。"
    assert eligibility_replies[0].consent_granted is False
    assert eligibility_replies[0].ends_conversation is False
    assert all(reply.consent_granted is False for reply in replies)
    assert "consent" not in [reply.step_id for reply in replies]

    # The repeated fallback is bounded and ends in the distinct recovery turn.
    longest_run = 0
    current_run = 0
    for reply in replies:
        if reply.step_id == "fallback":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    assert longest_run <= DEFAULT_FALLBACK_LIMIT
    assert "fallback_recovery" in [reply.step_id for reply in replies]

    replayed = replay_trace(trace, task=task)
    assert replayed.replayed_run.consent_events == []
    assert replayed.replayed_run.final_db.consent_records == []
    assert replayed.replayed_run.final_db.enrollments == []
    assert replayed.replayed_run.customer_conversation_closed is True
    assert replayed.replayed_run.stop_reason is StopReason.AGENT_FINISHED
    assert all(
        event.consent_granted is False
        for event in replayed.replayed_run.simulator_events
    )

    # The recorded v4 evaluation contained the false positive: a future-tense
    # "will be sent" promise satisfied notification_status. Re-evaluating the
    # same transcript against the current (later-versioned) specification is an
    # explicitly labelled diagnostic and must reject it.
    recorded_status = next(
        item
        for item in trace.evaluation.answer_fact_results
        if item["fact_id"] == "notification_status"
    )
    assert recorded_status["passed"] is True
    assert recorded_status["matched_pattern"] == "SENT"
    reevaluated = evaluate_trace(trace, task=task)
    new_status = next(
        item
        for item in reevaluated.evaluation.answer_fact_results
        if item["fact_id"] == "notification_status"
    )
    assert new_status["passed"] is False


def test_chinese_d06_workflow_remains_unchanged() -> None:
    outcome = execute_task(_task(), agent_factory=BenchmarkDemoAgent)
    run = outcome.run

    assert outcome.evaluation.success, outcome.evaluation.summary
    assert len(run.consent_events) == 1
    consent_event = next(
        event for event in run.simulator_events if event.step_id == "consent"
    )
    assert consent_event.consent_granted is True
    assert consent_event.ends_conversation is True
    assert len(run.final_db.enrollments) == 1
    assert len(run.final_db.notifications) == 1
    assert run.final_db.notifications[0].status == "SENT"
    assert run.conversation[-1].role == "agent"
    assert "已为您办理" in run.conversation[-1].content
