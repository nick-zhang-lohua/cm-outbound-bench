"""D02 live-validation repair: trigger precedence and the exact reason pattern.

Two confirmed defects from the preserved D02 v1.2 live runs are pinned here:

1. ``RUN-21e500865ce940db`` (``d02_offer_benefit_inquiry_accept`` v1.1). The
   Agent's enrollment consent question "请问您是否同意办理这项优惠？" was
   classified as ``agent_asks_eligibility``. The same message also reported a
   *completed* eligibility check ("我已为您做了资格核查，结果显示您符合办理
   条件"), and the Chinese eligibility detector combined the topic in that
   sentence with the "是否" of the later consent sentence. The eligibility step
   is listed before the accept step, so the intended ``accept`` turn never
   fired: the customer's read-only permission was answered with three fallbacks,
   and no consent, enrollment or SMS was recorded.
2. ``RUN-e8310813fcf149a0`` (``d02_eligibility_ineligible_explanation`` v1.1).
   The recorded reason sentence "您当前的套餐不在该活动的适用套餐范围内"
   differs from the accepted pattern "您当前的套餐不在该活动适用的套餐范围内"
   by one "的", so a correct, tool-grounded explanation failed the fact.

The historical traces and manifests under
``outputs/live_llm_validation/deepseek/`` are read-only. The accept task keeps
task version 1.1 because only the Simulator implementation changed; the
ineligible task advances to 1.2 because its Answer Fact specification changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from cm_outbound_bench.agent import (
    AgentContext,
    CallToolAction,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.benchmark.answer_facts import (
    agent_text_for_scope,
    evaluate_forbidden_fact,
    evaluate_required_fact,
    normalise_text,
)
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.replay import resolve_task
from cm_outbound_bench.benchmark.simulator import (
    ConsentAction,
    Intent,
    ScriptedSimulator,
)
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry

TASK_ACCEPT = "d02_offer_benefit_inquiry_accept"
TASK_DECLINE = "d02_hesitation_then_decline"
TASK_INELIGIBLE = "d02_eligibility_ineligible_explanation"
TASK_DNC = "d02_direct_refusal_do_not_call"
D06_TASK_ID = "d06_enrollment_confirmation_notification"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_V12_DIR = (
    PROJECT_ROOT / "outputs" / "live_llm_validation" / "deepseek" / "D02-v1.2"
)

ACCEPT_RUN = "RUN-21e500865ce940db"
INELIGIBLE_RUN = "RUN-e8310813fcf149a0"

REVISED_SPECIFICATION_LABEL = "diagnostic against revised specification"

#: The two preserved v1.2 live runs. Manifest, trace and content hashes pin the
#: historical bytes; the task version and hash are the historical specification.
HISTORICAL_V12_RUNS: dict[str, dict[str, str]] = {
    ACCEPT_RUN: {
        "task_id": TASK_ACCEPT,
        "manifest_sha256": (
            "05a444f6c95c41c38dfc71eedbbff7b131532034b2430a2c786046b57cdf5349"
        ),
        "trace_sha256": (
            "3f163a1bfff61c5e9236309fc870869a6da858c6a3d2f81dc46298b2be3e3f20"
        ),
        "trace_content_hash": (
            "366b51c1722aa8f033e9dc19923c01462e8452a24cfb5e115aaebd0ebc816113"
        ),
        "task_version": "1.1",
        "task_hash": (
            "8448feeb39e49147e57756946515c70ea1103cfdde3eff02562c8efeaba79a69"
        ),
    },
    INELIGIBLE_RUN: {
        "task_id": TASK_INELIGIBLE,
        "manifest_sha256": (
            "bcc71151e048e924da1fec9e8f3ff22b689fb0ade3dde9b8616233c338928059"
        ),
        "trace_sha256": (
            "1a199adac75f5707e58981a90a6d122bf9ef468f727f7a9637a64b4f19203cb3"
        ),
        "trace_content_hash": (
            "1d02dc977b9ffdc411683b2c58a8cfd6183bed3850524b4d08ea167f8d0fc115"
        ),
        "task_version": "1.1",
        "task_hash": (
            "f11f5a17d36b7622773d07aea474f45301c617f632f022eb016d74df1130c28c"
        ),
    },
}

#: Current specification. Task versions are unchanged; the hashes reflect the
#: canonical evaluator version (2.2) carried by the task payload after the
#: metadata-only fix removed the stale task-level 1.0 override.
CURRENT_TASK_SPECS: dict[str, tuple[str, str]] = {
    TASK_ACCEPT: (
        "1.1",
        "3fa0fef80981dfca2c62445d4361a20347a52898cc3d9bfc9263f61411a4b93a",
    ),
    TASK_INELIGIBLE: (
        "1.4",
        "41f0b0001834d6b1b0b5142b78ded4381ecfc2336057cc41fc08179935632129",
    ),
}

#: The three enrollment consent questions that must never fire the eligibility
#: trigger. The first is the exact sentence from the preserved trace.
ENROLLMENT_CONSENT_QUESTIONS = (
    "请问您是否同意办理这项优惠？",
    "请问您是否确认办理？",
    "您是否愿意参加这个活动？",
)

#: The three read-only eligibility questions that must never create consent.
READ_ONLY_ELIGIBILITY_QUESTIONS = (
    "可以帮我查一下能不能办理吗？",
    "请帮我查询一下是否符合办理条件",
    "我的号码是否符合这个活动的资格？",
)

#: The exact first consent utterance in ``RUN-21e500865ce940db`` (Agent say at
#: step 6), including the completed check that used to poison the classification.
LIVE_TRACE_CONSENT_UTTERANCE = (
    "身份验证已通过。为您介绍这项优惠：\n\n"
    "**模拟流量加赠包（OFFER_D02_001）**\n"
    "- 额外月费：10.00 元人民币/月\n"
    "- 有效期：6 个月\n"
    "- 自动续订：是，有效期结束后将自动续订\n"
    "- 使用限制：仅限主卡使用；优惠期内可取消，取消后按标准资费执行\n\n"
    "我已为您做了资格核查，结果显示您符合办理条件，没有其他限制原因。\n\n"
    "请问您是否同意办理这项优惠？"
)
LIVE_TRACE_GREETING = (
    "您好，我是中国移动的客服专员。今天联系您是想介绍一项优惠活动。"
    "为保护您的账户信息安全，请您先提供身份验证令牌，以便我核实您的身份。"
)
LIVE_TRACE_IDENTITY_REQUEST = (
    "您好，具体优惠内容需要先完成身份验证才能向您介绍，因为涉及账户相关信息。"
    "请您提供身份验证令牌，我核实身份后马上为您详细说明。"
)

#: The customer's read-only permission reply recorded in the same trace. It is
#: permission to check eligibility, not consent to enroll.
LIVE_TRACE_ELIGIBILITY_PERMISSION = "可以，你先帮我查一下能不能办，不用再问我。"

#: The intended read-only disclosure plus permission question from the task.
DISCLOSURE_AND_ELIGIBILITY = (
    "活动名称：模拟流量加赠包。每月额外费用 10 CNY，有效期 6 个月，"
    "到期自动续订，限制：仅限主卡使用。"
    "我先帮您查询一下办理资格，可以吗？"
)

#: The exact observed reason sentence from ``RUN-e8310813fcf149a0``.
LIVE_V12_INELIGIBLE_REASON = "您当前的套餐不在该活动的适用套餐范围内"

#: Ambiguous or generic phrases that must not satisfy ``ineligible_reason`` even
#: when the real tool result grounds ``plan_not_eligible``.
AMBIGUOUS_REASON_PHRASES = (
    "该活动有适用范围",
    "活动有套餐限制",
    "仅支持特定套餐",
    "套餐信息以系统查询结果为准",
    "您当前的套餐可能不符合活动要求",
    "该活动适用套餐范围有限",
    "不在活动范围内",
)

#: Domains the repair must preserve end to end.
CROSS_DOMAIN_DEMO_TASKS = (
    "d01_plan_content_explanation",
    "d02_direct_refusal_do_not_call",
    D06_TASK_ID,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest_path(run_id: str) -> Path:
    return LIVE_V12_DIR / f"{run_id}.json"


def _trace_path(run_id: str) -> Path:
    return LIVE_V12_DIR / run_id / "trace.json"


def _ineligible_fact():
    task = find_task(TASK_INELIGIBLE)
    assert task.answer_assertions is not None
    return next(
        fact
        for fact in task.answer_assertions.required_facts
        if fact.fact_id == "ineligible_reason"
    )


def _eligibility_entry(
    reasons: object = ("plan_not_eligible",),
    *,
    ok: bool = True,
) -> ToolTraceEntry:
    """The real successful ``check_offer_eligibility`` result shape."""
    if ok:
        result = ToolResult.success(
            {
                "eligible": False,
                "offer_id": "OFFER_D02_001",
                "phone_number_id": "PN103",
                "reasons": reasons,
            }
        )
    else:
        result = ToolResult.failure(
            "eligibility_failed", "Eligibility could not be checked."
        )
    return ToolTraceEntry(
        step=5,
        tool_name="check_offer_eligibility",
        arguments={"offer_id": "OFFER_D02_001", "phone_number_id": "PN103"},
        result=result,
    )


def _advance_accept_to_consent() -> ScriptedSimulator:
    """Drive the real accept script with the preserved trace's first two turns."""
    task = find_task(TASK_ACCEPT)
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    assert simulator.respond(LIVE_TRACE_GREETING).step_id == "ask_content"
    assert (
        simulator.respond(LIVE_TRACE_IDENTITY_REQUEST).step_id == "provide_token"
    )
    return simulator


class _LiveTraceAcceptAgent:
    """The preserved trace's workflow, replayed offline through the Runner."""

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        step = self._step
        line = context.task.phone_number_id
        offer = context.task.offer_id or ""

        if step == 1:
            return SayAction(message=LIVE_TRACE_GREETING)
        if step == 2:
            return SayAction(message=LIVE_TRACE_IDENTITY_REQUEST)
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
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        if step == 6:
            return SayAction(message=LIVE_TRACE_CONSENT_UTTERANCE)
        if step == 7:
            return CallToolAction(
                tool_name="enroll_offer",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        if step == 8:
            enrollment_id = next(
                str(entry.result.data["enrollment_id"])
                for entry in context.tool_results
                if entry.tool_name == "enroll_offer" and entry.result.ok
            )
            return CallToolAction(
                tool_name="send_confirmation_sms",
                arguments={"enrollment_id": enrollment_id},
            )
        if step == 9:
            return SayAction(
                message="已为您成功办理『模拟流量加赠包』，确认短信已模拟发送。"
            )
        return FinishAction(reason="D02 live trace acceptance flow complete")


class _ReadOnlyPermissionAgent:
    """Disclose the terms, ask permission for the read-only check, then stop."""

    def __init__(self) -> None:
        self._step = 0

    def act(self, context: AgentContext):
        self._step += 1
        step = self._step
        line = context.task.phone_number_id
        offer = context.task.offer_id or ""

        if step == 1:
            return SayAction(message=LIVE_TRACE_GREETING)
        if step == 2:
            return SayAction(message=LIVE_TRACE_IDENTITY_REQUEST)
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
            return CallToolAction(
                tool_name="check_offer_eligibility",
                arguments={"phone_number_id": line, "offer_id": offer},
            )
        if step == 6:
            return SayAction(message=DISCLOSURE_AND_ELIGIBILITY)
        return FinishAction(reason="read-only permission probe complete")


# ---------------------------------------------------------------------------
# 1. Trigger classification: the six required phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", ENROLLMENT_CONSENT_QUESTIONS)
def test_enrollment_consent_questions_never_trigger_eligibility(
    message: str,
) -> None:
    assert ScriptedSimulator._asks_consent(message) is True
    assert ScriptedSimulator._asks_eligibility(message) is False


@pytest.mark.parametrize("message", READ_ONLY_ELIGIBILITY_QUESTIONS)
def test_read_only_eligibility_questions_never_create_consent(
    message: str,
) -> None:
    assert ScriptedSimulator._asks_eligibility(message) is True
    assert ScriptedSimulator._asks_consent(message) is False


def test_completed_eligibility_report_is_not_a_question() -> None:
    report = (
        "我已为您做了资格核查，结果显示您符合办理条件，没有其他限制原因。"
    )
    assert ScriptedSimulator._asks_eligibility(report) is False
    assert ScriptedSimulator._asks_consent(report) is False
    # The old defect: the topic in this sentence plus the later "是否" combined
    # into an eligibility question.
    assert ScriptedSimulator._asks_eligibility(LIVE_TRACE_CONSENT_UTTERANCE) is False


def test_read_only_permission_with_a_consent_word_is_still_read_only() -> None:
    """Eligibility masking must keep working for "同意我先查询"."""
    message = "您是否同意我先为您查询办理资格？"
    assert ScriptedSimulator._asks_eligibility(message) is True
    assert ScriptedSimulator._asks_consent(message) is False


@pytest.mark.parametrize(
    "message",
    (
        "请您确认办理信息，可以吗？",
        "请确认开通详情，好吗？",
        "您可以确认办理资料吗？",
    ),
)
def test_action_information_phrases_are_not_enrollment_consent(
    message: str,
) -> None:
    """Confirming enrollment *details* is read-only, not consent to enroll."""
    assert ScriptedSimulator._asks_consent(message) is False


def test_mixed_message_cannot_fire_a_consent_step() -> None:
    """A read-only question in one sentence wins over consent in another."""
    simulator = _advance_accept_to_consent()
    mixed = (
        "活动名称：模拟流量加赠包。每月额外费用 10 CNY，有效期 6 个月，"
        "到期自动续订，限制：仅限主卡使用。"
        "我帮您查一下能不能办理，可以吗？"
        "另外，您是否同意办理这个活动？"
    )
    reply = simulator.respond(mixed)
    assert reply.step_id == "agent_asks_eligibility"
    assert reply.consent_granted is False
    assert reply.consent_action is None
    # The consent question alone still fires the consent step afterwards.
    assert simulator.respond("您是否同意办理这个活动？").step_id == "accept"


# ---------------------------------------------------------------------------
# 2. The exact preserved-trace consent utterance
# ---------------------------------------------------------------------------


def test_live_consent_utterance_is_the_preserved_trace_text() -> None:
    trace = load_trace(_trace_path(ACCEPT_RUN))
    recorded = [
        action.message
        for action in trace.actions
        if action.action_type == "say"
    ]
    assert LIVE_TRACE_CONSENT_UTTERANCE in recorded
    assert recorded[2] == LIVE_TRACE_CONSENT_UTTERANCE
    assert recorded[2].endswith("请问您是否同意办理这项优惠？")
    # The historical trace recorded this turn as the eligibility trigger.
    eligibility_event = next(
        event
        for event in trace.simulator_events
        if event.step_id == "agent_asks_eligibility"
    )
    assert eligibility_event.consent_granted is False
    assert eligibility_event.message == LIVE_TRACE_ELIGIBILITY_PERMISSION


def test_live_trace_utterance_fires_the_intended_accept_step() -> None:
    task = find_task(TASK_ACCEPT)
    simulator = _advance_accept_to_consent()
    reply = simulator.respond(LIVE_TRACE_CONSENT_UTTERANCE)

    accept_step = next(
        step for step in task.simulator.steps if step.step_id == "accept"
    )
    assert reply.step_id == "accept"
    assert reply.intent is Intent.ACCEPT
    assert reply.message == accept_step.message
    assert reply.consent_granted is True
    assert reply.consent_action is ConsentAction.OFFER_ENROLLMENT
    assert reply.consent_target == task.offer_id
    assert reply.ends_conversation is True


def test_runner_records_consent_before_the_single_enrollment_and_sms() -> None:
    task = find_task(TASK_ACCEPT)
    outcome = execute_task(task, agent_factory=_LiveTraceAcceptAgent)
    run = outcome.run

    assert outcome.evaluation.success, outcome.evaluation.summary

    step_ids = [event.step_id for event in run.simulator_events]
    assert "agent_asks_eligibility" not in step_ids
    assert step_ids == ["ask_content", "provide_token", "accept"]

    accept = run.simulator_events[-1]
    assert accept.intent == "accept"
    assert accept.consent_granted is True
    assert accept.consent_action == ConsentAction.OFFER_ENROLLMENT.value
    assert accept.consent_target == task.offer_id
    assert accept.message == "好的，条款我都听明白了，我同意办理这个活动，麻烦你帮我办。"

    # The Runner records exactly one consent, and it precedes the enrollment.
    assert len(run.consent_events) == 1
    consent_step = run.consent_events[0].step
    tool_steps = {
        entry.tool_name: entry.step
        for entry in run.tool_trace
        if entry.result.ok
    }
    assert tool_steps["check_offer_eligibility"] < consent_step
    assert consent_step < tool_steps["enroll_offer"]
    assert tool_steps["enroll_offer"] < tool_steps["send_confirmation_sms"]
    assert consent_step == accept.step

    # Exactly one enrollment and one confirmation SMS.
    assert [entry.tool_name for entry in run.tool_trace].count(
        "enroll_offer"
    ) == 1
    assert [entry.tool_name for entry in run.tool_trace].count(
        "send_confirmation_sms"
    ) == 1
    assert len(run.final_db.consent_records) == 1
    assert len(run.final_db.enrollments) == 1
    assert len(run.final_db.sms_records) == 1
    assert run.final_db.enrollments[0].consent_id == (
        run.final_db.consent_records[0].consent_id
    )


def test_read_only_permission_reply_creates_no_enrollment_consent() -> None:
    # (a) The reply itself is never read as a consent request.
    assert (
        ScriptedSimulator._asks_consent(LIVE_TRACE_ELIGIBILITY_PERMISSION) is False
    )

    # (b) The scripted step that emits it carries no consent authority.
    task = find_task(TASK_ACCEPT)
    step = next(
        item
        for item in task.simulator.steps
        if item.step_id == "agent_asks_eligibility"
    )
    assert step.message == LIVE_TRACE_ELIGIBILITY_PERMISSION
    assert step.intent is Intent.ASK_QUESTION
    assert step.consent_action is None
    assert step.consent_target == ""
    assert step.ends_conversation is False

    # (c) The real script returns that message and no consent signal.
    simulator = _advance_accept_to_consent()
    reply = simulator.respond(DISCLOSURE_AND_ELIGIBILITY)
    assert reply.step_id == "agent_asks_eligibility"
    assert reply.message == LIVE_TRACE_ELIGIBILITY_PERMISSION
    assert reply.consent_granted is False
    assert reply.consent_action is None

    # (d) The Runner records no consent for the read-only permission episode.
    outcome = execute_task(task, agent_factory=_ReadOnlyPermissionAgent)
    run = outcome.run
    assert [event.step_id for event in run.simulator_events][-1] == (
        "agent_asks_eligibility"
    )
    assert run.consent_events == []
    assert run.final_db.consent_records == []
    assert run.final_db.enrollments == []
    assert run.final_db.sms_records == []


def test_read_only_path_still_reaches_consent_only_on_a_new_question() -> None:
    """Acceptance tasks must not auto-grant consent after the permission turn."""
    simulator = _advance_accept_to_consent()
    assert simulator.respond(DISCLOSURE_AND_ELIGIBILITY).step_id == (
        "agent_asks_eligibility"
    )
    # A declarative mention is not an explicit consent question.
    assert simulator.respond("办理这个活动").step_id != "accept"
    assert ScriptedSimulator._asks_consent("办理这个活动") is False
    reply = simulator.respond("您是否同意办理这个活动？")
    assert reply.step_id == "accept"
    assert reply.consent_granted is True


# ---------------------------------------------------------------------------
# 3. The exact ineligible-reason pattern and its grounding
# ---------------------------------------------------------------------------


def test_live_v12_reason_satisfies_the_grounded_fact() -> None:
    outcome = evaluate_required_fact(
        _ineligible_fact(),
        normalise_text(LIVE_V12_INELIGIBLE_REASON),
        tool_results=[_eligibility_entry()],
    )
    assert outcome.passed is True, outcome.detail
    assert outcome.matched_pattern == LIVE_V12_INELIGIBLE_REASON


def test_live_v12_reason_still_requires_plan_not_eligible_grounding() -> None:
    fact = _ineligible_fact()
    statement = normalise_text(LIVE_V12_INELIGIBLE_REASON)

    assert evaluate_required_fact(fact, statement).passed is False
    for reasons in (["line_not_active"], ["offer_inactive"], []):
        assert (
            evaluate_required_fact(
                fact,
                statement,
                tool_results=[_eligibility_entry(reasons)],
            ).passed
            is False
        ), reasons
    for reasons in (["PLAN_NOT_ELIGIBLE"], ["plan_not_eligible_extra"]):
        assert (
            evaluate_required_fact(
                fact,
                statement,
                tool_results=[_eligibility_entry(reasons)],
            ).passed
            is False
        ), reasons
    assert (
        evaluate_required_fact(
            fact,
            statement,
            tool_results=[_eligibility_entry(ok=False)],
        ).passed
        is False
    )


@pytest.mark.parametrize("phrase", AMBIGUOUS_REASON_PHRASES)
def test_ambiguous_reason_phrases_do_not_satisfy_the_fact(phrase: str) -> None:
    assert (
        evaluate_required_fact(
            _ineligible_fact(),
            normalise_text(phrase),
            tool_results=[_eligibility_entry()],
        ).passed
        is False
    )


def test_forbidden_eligible_claim_is_still_enforced() -> None:
    task = find_task(TASK_INELIGIBLE)
    assert task.answer_assertions is not None
    forbidden = next(
        fact
        for fact in task.answer_assertions.forbidden_facts
        if fact.fact_id == "false_eligible_claim"
    )
    assert (
        evaluate_forbidden_fact(
            forbidden, normalise_text("您的号码完全符合条件")
        ).passed
        is False
    )


def test_live_v12_run_is_a_diagnostic_not_a_historical_pass() -> None:
    trace = load_trace(_trace_path(INELIGIBLE_RUN))
    run = trace.to_run_result()

    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded["ineligible_reason"]["passed"] is False
    assert recorded["ineligible_reason"]["matched_pattern"] == ""
    assert trace.evaluation.success is False

    diagnostic = {
        "label": REVISED_SPECIFICATION_LABEL,
        "outcome": evaluate_required_fact(
            _ineligible_fact(),
            agent_text_for_scope(run, "final_answer"),
            tool_results=run.tool_trace,
        ),
    }
    assert diagnostic["label"] == "diagnostic against revised specification"
    assert diagnostic["outcome"].passed is True, diagnostic["outcome"].detail
    assert (
        diagnostic["outcome"].matched_pattern == LIVE_V12_INELIGIBLE_REASON
    )


def test_state_expectations_are_unchanged() -> None:
    accept = {
        (item.collection, item.expected_count)
        for item in find_task(TASK_ACCEPT).expected_actions
    }
    assert accept == {
        ("enrollments", 1),
        ("consent_records", 1),
        ("sms_records", 1),
        ("do_not_call_records", 0),
    }
    ineligible = {
        (item.collection, item.expected_count)
        for item in find_task(TASK_INELIGIBLE).expected_actions
    }
    assert ineligible == {
        ("enrollments", 0),
        ("consent_records", 0),
        ("sms_records", 0),
    }


# ---------------------------------------------------------------------------
# 4. Versioning, historical integrity and the generated catalogue
# ---------------------------------------------------------------------------


def test_current_task_versions_and_hashes() -> None:
    for task_id, (version, task_hash) in CURRENT_TASK_SPECS.items():
        task = find_task(task_id)
        assert task.task_version == version, task_id
        assert task.task_hash() == task_hash, task_id


def test_ineligible_task_hash_changed_from_the_recorded_v1_1_hash() -> None:
    task = find_task(TASK_INELIGIBLE)
    assert task.task_version == "1.4"
    assert task.task_hash() != HISTORICAL_V12_RUNS[INELIGIBLE_RUN]["task_hash"]


def test_preserved_v12_manifests_and_traces_are_byte_unchanged() -> None:
    for run_id, history in HISTORICAL_V12_RUNS.items():
        manifest_path = _manifest_path(run_id)
        trace_path = _trace_path(run_id)

        assert (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            == history["manifest_sha256"]
        ), run_id
        assert trace_file_sha256(trace_path) == history["trace_sha256"], run_id

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["task_id"] == history["task_id"]
        assert manifest["task_version"] == history["task_version"]
        assert manifest["task_hash"] == history["task_hash"]
        assert manifest["run_status"] == "evaluation_failure"
        assert manifest["task_success"] is False

        trace = load_trace(trace_path)
        assert trace.task_id == history["task_id"]
        assert trace.task_version == history["task_version"]
        assert trace.integrity.trace_hash == history["trace_content_hash"]
        assert trace.compute_hash() == history["trace_content_hash"]
        assert trace.evaluation.success is False


def test_accept_run_still_records_no_consent() -> None:
    trace = load_trace(_trace_path(ACCEPT_RUN))
    assert trace.consent_events == []
    assert trace.action_consent_events == []
    assert trace.initial_state_hash == trace.final.final_state_hash
    assert trace.final.database.enrollments == []
    assert trace.final.database.consent_records == []
    assert trace.final.database.sms_records == []
    assert "accept" not in [
        event.step_id for event in trace.simulator_events
    ]


def test_ineligible_run_is_not_reinterpreted_under_v1_2() -> None:
    trace = load_trace(_trace_path(INELIGIBLE_RUN))
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


def test_scenario_catalog_versions_match_the_tasks() -> None:
    catalog_text = (
        PROJECT_ROOT / "docs" / "scenario_catalog.md"
    ).read_text(encoding="utf-8")
    sections = re.split(
        r"^### `([^`]+)`\s*$", catalog_text, flags=re.MULTILINE
    )
    catalog: dict[str, str] = {}
    for index in range(1, len(sections), 2):
        task_id = sections[index]
        body = sections[index + 1]
        match = re.search(r"- \*\*Task version:\*\* (\S+)", body)
        assert match is not None, task_id
        catalog[task_id] = match.group(1)

    assert catalog == {
        task.task_id: task.task_version for task in all_tasks()
    }
    assert catalog[TASK_INELIGIBLE] == "1.4"
    assert catalog[TASK_ACCEPT] == "1.1"


# ---------------------------------------------------------------------------
# 5. Cross-domain preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", CROSS_DOMAIN_DEMO_TASKS)
def test_cross_domain_tasks_still_pass(task_id: str) -> None:
    outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
    assert outcome.evaluation.success, (task_id, outcome.evaluation.summary)


def test_dnc_pass_still_records_no_enrollment_or_consent() -> None:
    outcome = execute_task(
        find_task(TASK_DNC), agent_factory=BenchmarkDemoAgent
    )
    run = outcome.run
    assert outcome.evaluation.success, outcome.evaluation.summary
    assert len(run.final_db.do_not_call_records) == 1
    assert run.final_db.enrollments == []
    assert run.final_db.consent_records == []
    assert run.consent_events == []
