"""D02 v1.2: the live-validation repair for ``d02_hesitation_then_decline``.

Four confirmed defects from the preserved v1.1 live runs
(``outputs/live_llm_validation/deepseek/D02-v1.1/``) are pinned here:

1. ``offer_validity`` and ``offer_no_auto_renewal`` only recognised Chinese
   expressions, so a correct English disclosure failed both facts
   (``RUN-0c1e8989b66f42d2``, ``RUN-c6f4babebdf5460c``);
2. the agent finished immediately after the customer's terminal refusal without
   a final acknowledgement, so ``decline_acknowledged`` could never pass;
3. the shared prompt did not tell the agent to answer in the customer's
   language, which is why the D02 scenario ran in English at all;
4. ``decline_acknowledged`` had no wording for a natural closing sentence.

The historical v1.1 traces and manifests are read-only and their recorded
evaluations stay failures. Every re-evaluation against v1.2 is labelled a
**diagnostic against the revised specification**.
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
from cm_outbound_bench.benchmark.execution import (
    execute_task,
    load_initial_state,
)
from cm_outbound_bench.benchmark.replay import resolve_task
from cm_outbound_bench.benchmark.simulator import Intent, ScriptedSimulator
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.llm_agent import LLMBackedAgent
from cm_outbound_bench.providers.base import ProviderResponse, ProviderToolCall
from cm_outbound_bench.runner import StopReason

TASK_DECLINE = "d02_hesitation_then_decline"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_V11_DIR = (
    PROJECT_ROOT / "outputs" / "live_llm_validation" / "deepseek" / "D02-v1.1"
)

REVISED_SPECIFICATION_LABEL = "diagnostic against revised specification"

#: The two preserved v1.1 decline runs that failed the same three facts. The
#: trace and manifest bytes must never change.
HISTORICAL_V11_RUNS: dict[str, dict[str, str]] = {
    "RUN-0c1e8989b66f42d2": {
        "manifest_sha256": (
            "a48d4019f6aa35044da443d4ba0c77bd95b87b681d0bfedb338ab900b4d6d657"
        ),
        "trace_sha256": (
            "43d572118f7a80c1b336e64d3111a1fad23059e6acca57a63425da3a6d1d114a"
        ),
        "task_hash": (
            "307005dd2416a166b394d889c7a89f01bbf3c1952e21e0b87896400a479654e9"
        ),
    },
    "RUN-c6f4babebdf5460c": {
        "manifest_sha256": (
            "6b9a9fbf65c1117c5194f6b7daa93c9b6810a4d2693c6ee8bb10ebfde8f83280"
        ),
        "trace_sha256": (
            "69dde8c928fa9fd023aa6e55b969e49158b4b434570882ccf4a8b07884be6932"
        ),
        "task_hash": (
            "307005dd2416a166b394d889c7a89f01bbf3c1952e21e0b87896400a479654e9"
        ),
    },
}

#: The exact Chinese terms disclosure the scripted agent sends, followed by an
#: enrollment decision question so the hesitation step fires. It states all four
#: disclosure dimensions, so the customer's remaining question steps are skipped.
DISCLOSURE_AND_CONSENT = (
    "『模拟语音加赠包』的月费是 15 CNY，有效期 12 个月，到期后不自动续订，"
    "限制：仅限家庭套餐主卡。您是否同意办理这个活动？"
)
GREETING = "您好，我是中国移动客服，想向您介绍一项语音加赠包活动。"
IDENTITY_REQUEST = "为了核对机主身份，请提供您的测试身份令牌。"
SECOND_DECISION_QUESTION = "您现在是想参加，还是先不参加呢？"

#: A natural closing sentence, the one the fix produces for this scenario.
DECLINE_ACKNOWLEDGEMENT = "好的，明白您的决定。这次就不为您办理了，感谢您的时间。"

#: Tasks this D02 repair must not disturb. Their scenario content is unchanged;
#: the pinned hashes reflect the canonical evaluator version (2.2) carried by the
#: task payload after the metadata-only fix removed the stale task-level 1.0
#: override. ``d01_plan_comparison`` advanced to 1.1 later, in the D01 Simulator
#: fallback repair, without changing anything this module protects. The
#: ineligible-explanation task is absent because the later D02 live-validation
#: repair advanced it to 1.2; its historical v1.1 hash is pinned in
#: ``tests/test_benchmark_d02_live_validation_repair.py``.
PINNED_TASK_VERSIONS: dict[str, tuple[str, str]] = {
    "d02_offer_benefit_inquiry_accept": (
        "1.1",
        "3fa0fef80981dfca2c62445d4361a20347a52898cc3d9bfc9263f61411a4b93a",
    ),
    "d02_direct_refusal_do_not_call": (
        "1.1",
        "37ac9068499fa9898ddded8f8ba65e5ae32f3b2d5239b0ae7a120b3ce95beadc",
    ),
    "d01_plan_comparison": (
        "1.3",
        "1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632",
    ),
    "d06_enrollment_confirmation_notification": (
        "1.1",
        "9326e6ceea9e0e91f17437689e3712c562e7888aef26b740cc96e38b7d3399e5",
    ),
    "d06_recharge_confirmation_notification": (
        "1.3",
        "1676e4ced13f9ea304b36e514762c4c3291f3576e84f5c6d3fd2cea3d02dbe74",
    ),
}

#: Domains the repair must preserve end to end.
CROSS_DOMAIN_DEMO_TASKS = (
    "d01_plan_content_explanation",
    "d01_plan_comparison",
    "d02_offer_benefit_inquiry_accept",
    "d02_eligibility_ineligible_explanation",
    "d02_direct_refusal_do_not_call",
    "d06_enrollment_confirmation_notification",
    "d06_recharge_confirmation_notification",
)

#: State collections a decline episode must leave untouched.
STATE_COLLECTIONS = (
    "enrollments",
    "consent_records",
    "sms_records",
    "do_not_call_records",
    "notifications",
    "recharges",
    "plan_change_requests",
    "cancellation_requests",
    "charges",
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


class _ScriptedProvider:
    """Return a fixed provider response sequence; never touches the network."""

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, *, messages, tools) -> ProviderResponse:
        del messages, tools
        response = self._responses[self.calls]
        self.calls += 1
        return response


class _FinishClient:
    def complete(self, *, messages, tools):
        return ProviderResponse(finish_requested=True)


def _tool(tool_name: str, **arguments) -> ProviderResponse:
    return ProviderResponse(
        tool_calls=[
            ProviderToolCall(
                call_id=f"call-{tool_name}",
                tool_name=tool_name,
                arguments=arguments,
            )
        ]
    )


def _decline_episode_responses() -> list[ProviderResponse]:
    """The scripted decline episode: one provider decision per runner step."""
    return [
        # 1: greet; the simulator's "first" step asks what the offer is.
        ProviderResponse(assistant_message=GREETING),
        # 2: ask for the token; the customer shares it.
        ProviderResponse(assistant_message=IDENTITY_REQUEST),
        # 3-5: the three required read-only tools.
        _tool(
            "verify_customer_identity",
            phone_number_id="PN102",
            identity_token="TEST_IDENTITY_C102",
        ),
        _tool("get_offer_details", offer_id="OFFER_D02_002"),
        _tool(
            "check_offer_eligibility",
            phone_number_id="PN102",
            offer_id="OFFER_D02_002",
        ),
        # 6: disclose every term and ask for consent; the customer hesitates.
        ProviderResponse(assistant_message=DISCLOSURE_AND_CONSENT),
        # 7: ask the enrollment decision again; the customer declines and hangs up.
        ProviderResponse(assistant_message=SECOND_DECISION_QUESTION),
        # 8: the model wants to finish; the gate answers the refusal first.
        ProviderResponse(finish_requested=True),
        # 9: the acknowledgement has been sent, so finishing is now correct.
        ProviderResponse(finish_requested=True),
    ]


def _run_decline_episode():
    provider = _ScriptedProvider(_decline_episode_responses())
    outcome = execute_task(
        find_task(TASK_DECLINE),
        agent_factory=lambda: LLMBackedAgent(provider),
        agent_name="llm",
    )
    return provider, outcome


def _entry(
    tool_name: str,
    data: dict | None = None,
    *,
    step: int = 2,
) -> ToolTraceEntry:
    return ToolTraceEntry(
        step=step,
        tool_name=tool_name,
        arguments={},
        result=ToolResult.success(data or {}),
    )


def _agent_context(
    messages: list[ConversationMessage],
    traces: list[ToolTraceEntry] | None = None,
) -> AgentContext:
    task = find_task(TASK_DECLINE)
    return AgentContext(
        task=task.agent_view(),
        policy="Synthetic test policy.",
        tools=build_toolkit(CmOutboundDB()).list_tools(),
        messages=messages,
        tool_results=list(traces or []),
        step=9,
    )


def _advance_to_consent_question() -> ScriptedSimulator:
    """Drive the real D02 decline script up to the first decision question."""
    task = find_task(TASK_DECLINE)
    simulator = ScriptedSimulator(task.simulator, offer_id=task.offer_id)
    assert simulator.respond(GREETING).step_id == "ask_what"
    assert simulator.respond(IDENTITY_REQUEST).step_id == "provide_token"
    return simulator


# ---------------------------------------------------------------------------
# 1. Versioning and historical integrity
# ---------------------------------------------------------------------------


def test_decline_task_is_v1_3_after_the_narrow_answer_assertion_repair() -> None:
    """The v1.2 content is preserved; only the acknowledgement list grew.

    The v1.3 repair added ``理解您的决定`` to ``decline_acknowledged`` and bumped
    the task version. Every v1.2 behaviour this module pins still holds; the
    test below also proves the hash no longer matches any historical version.
    """
    task = find_task(TASK_DECLINE)
    assert task.task_version == "1.3"
    for history in HISTORICAL_V11_RUNS.values():
        assert task.task_hash() != history["task_hash"]


def test_historical_v1_1_traces_and_manifests_are_byte_unchanged() -> None:
    for run_id, history in HISTORICAL_V11_RUNS.items():
        manifest_path = LIVE_V11_DIR / f"{run_id}.json"
        trace_path = LIVE_V11_DIR / run_id / "trace.json"

        assert (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            == history["manifest_sha256"]
        ), run_id
        assert trace_file_sha256(trace_path) == history["trace_sha256"], run_id

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["task_version"] == "1.1"
        assert manifest["task_hash"] == history["task_hash"]
        assert manifest["run_status"] == "evaluation_failure"
        assert manifest["task_success"] is False

        trace = load_trace(trace_path)
        assert trace.task_version == "1.1"
        assert trace.evaluation.success is False


def test_historical_v1_1_traces_are_not_reinterpreted_as_v1_2() -> None:
    for run_id in HISTORICAL_V11_RUNS:
        trace = load_trace(LIVE_V11_DIR / run_id / "trace.json")
        with pytest.raises(TraceSchemaError):
            resolve_task(trace)


def test_historical_failure_evidence_is_preserved() -> None:
    trace = load_trace(LIVE_V11_DIR / "RUN-0c1e8989b66f42d2" / "trace.json")
    recorded = {
        item["fact_id"]: item for item in trace.evaluation.answer_fact_results
    }
    assert recorded["offer_validity"]["passed"] is False
    assert recorded["offer_no_auto_renewal"]["passed"] is False
    assert recorded["decline_acknowledged"]["passed"] is False
    # The conversation really ended on the customer's refusal.
    assert trace.conversation[-1].role == "user"
    assert trace.stop.customer_conversation_closed is True
    assert trace.stop.post_closure_actions == 0


def test_other_tasks_keep_their_versions_and_hashes() -> None:
    for task_id, (version, task_hash) in PINNED_TASK_VERSIONS.items():
        task = find_task(task_id)
        assert task.task_version == version, task_id
        assert task.task_hash() == task_hash, task_id


def test_d02_policy_requires_an_acknowledgement() -> None:
    flat = " ".join(policy_for_domain("D02").split())
    assert "acknowledge the decision in one short closing message" in flat
    assert "do not repeat the pitch" in flat
    assert "do not ask for enrollment again" in flat


# ---------------------------------------------------------------------------
# 2. Recorded-transcript diagnostic against the revised specification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fact_id", "expected_pattern"),
    (
        ("offer_validity", "12 months"),
        ("offer_no_auto_renewal", "does not renew automatically"),
    ),
)
def test_recorded_english_transcript_now_satisfies_the_facts(
    fact_id: str, expected_pattern: str
) -> None:
    trace = load_trace(LIVE_V11_DIR / "RUN-0c1e8989b66f42d2" / "trace.json")
    run = trace.to_run_result()
    task = find_task(TASK_DECLINE)
    fact = next(
        item
        for item in task.answer_assertions.required_facts
        if item.fact_id == fact_id
    )

    diagnostic = {
        "label": REVISED_SPECIFICATION_LABEL,
        "outcome": evaluate_required_fact(
            fact,
            agent_text_for_scope(run, task.answer_assertions.scope),
            tool_results=run.tool_trace,
        ),
    }
    assert diagnostic["label"] == "diagnostic against revised specification"
    assert diagnostic["outcome"].passed is True, diagnostic["outcome"].detail
    assert diagnostic["outcome"].matched_pattern == expected_pattern


def test_recorded_transcript_still_has_no_refusal_acknowledgement() -> None:
    """The historical run is a failure; only the v1.2 agent answers the refusal."""
    trace = load_trace(LIVE_V11_DIR / "RUN-0c1e8989b66f42d2" / "trace.json")
    run = trace.to_run_result()
    task = find_task(TASK_DECLINE)
    fact = next(
        item
        for item in task.answer_assertions.required_facts
        if item.fact_id == "decline_acknowledged"
    )
    outcome = evaluate_required_fact(
        fact, agent_text_for_scope(run, task.answer_assertions.scope)
    )
    assert outcome.passed is False


def test_decline_acknowledgement_is_scoped_to_the_final_answer() -> None:
    """The acknowledgement must come after the refusal, not anywhere earlier."""
    task = find_task(TASK_DECLINE)
    fact = next(
        item
        for item in task.answer_assertions.required_facts
        if item.fact_id == "decline_acknowledged"
    )
    assert fact.scope == "final_answer"

    trace = load_trace(LIVE_V11_DIR / "RUN-0c1e8989b66f42d2" / "trace.json")
    run = trace.to_run_result()
    transcript = agent_text_for_scope(run, task.answer_assertions.scope)
    final_answer = agent_text_for_scope(run, "final_answer")

    last_agent = [
        message.content for message in run.conversation if message.role == "agent"
    ][-1]
    assert final_answer == normalise_text(last_agent)
    # The terms disclosure is in the transcript but not in the last message, so
    # the two scopes really are different texts.
    assert "模拟语音加赠包" in transcript
    assert "模拟语音加赠包" not in final_answer
    # The historical run never acknowledged the refusal anywhere.
    assert "明白您的决定" not in transcript


def test_tool_grounding_is_still_required() -> None:
    """Bilingual patterns must not weaken tool grounding."""
    fact = next(
        item
        for item in find_task("d02_eligibility_ineligible_explanation")
        .answer_assertions.required_facts
        if item.fact_id == "ineligible_reason"
    )
    text = normalise_text("当前套餐不在该活动适用范围")
    assert evaluate_required_fact(fact, text).passed is False
    assert (
        evaluate_required_fact(
            fact,
            text,
            tool_results=[
                _entry("check_offer_eligibility", {"reasons": ["plan_not_eligible"]})
            ],
        ).passed
        is True
    )
    assert (
        evaluate_required_fact(
            fact,
            text,
            tool_results=[
                _entry("check_offer_eligibility", {"reasons": ["line_not_active"]})
            ],
        ).passed
        is False
    )


# ---------------------------------------------------------------------------
# 3. Simulator: hesitation then refusal
# ---------------------------------------------------------------------------


def test_hesitation_step_fires() -> None:
    simulator = _advance_to_consent_question()
    reply = simulator.respond(DISCLOSURE_AND_CONSENT)
    assert reply.step_id == "hesitate"
    assert reply.intent is Intent.HESITATION
    assert reply.ends_conversation is False
    assert reply.consent_granted is False


def test_refusal_step_fires_and_closes_the_conversation() -> None:
    simulator = _advance_to_consent_question()
    assert simulator.respond(DISCLOSURE_AND_CONSENT).step_id == "hesitate"
    reply = simulator.respond(SECOND_DECISION_QUESTION)
    assert reply.step_id == "decline"
    assert reply.intent is Intent.REFUSAL
    assert reply.ends_conversation is True
    assert reply.consent_granted is False
    assert reply.consent_action is None


def test_decline_steps_still_carry_no_consent() -> None:
    steps = {step.step_id: step for step in find_task(TASK_DECLINE).simulator.steps}
    for step_id in ("hesitate", "decline"):
        assert steps[step_id].consent_action is None
        assert steps[step_id].consent_target == ""


# ---------------------------------------------------------------------------
# 4. Live episode: acknowledge the refusal, then finish
# ---------------------------------------------------------------------------


def test_episode_fires_both_scripted_steps() -> None:
    _, outcome = _run_decline_episode()
    step_ids = [event.step_id for event in outcome.run.simulator_events]
    assert "hesitate" in step_ids
    assert "decline" in step_ids
    assert step_ids.index("hesitate") < step_ids.index("decline")
    decline = next(
        event
        for event in outcome.run.simulator_events
        if event.step_id == "decline"
    )
    assert decline.intent == "refusal"
    assert decline.ends_conversation is True
    assert decline.consent_granted is False


def test_agent_acknowledges_the_refusal_and_then_finishes() -> None:
    provider, outcome = _run_decline_episode()
    run = outcome.run

    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.episode_completed is True

    agent_messages = [
        message for message in run.conversation if message.role == "agent"
    ]
    assert agent_messages[-1].content == DECLINE_ACKNOWLEDGEMENT
    assert run.conversation[-1].role == "agent"

    # The acknowledgement is the one and only post-closure agent turn, and the
    # episode ends on the very next provider decision.
    assert run.customer_conversation_closed is True
    assert run.post_closure_actions == 1
    assert provider.calls == 9
    assert [action.action_type for action in run.actions[-2:]] == ["say", "finish"]
    assert run.actions[-2].outcome == "post_conversation_message"


def test_acknowledgement_does_not_repeat_the_offer_or_persuade() -> None:
    _, outcome = _run_decline_episode()
    acknowledgement = [
        message.content
        for message in outcome.run.conversation
        if message.role == "agent"
    ][-1]
    for persuasion in ("您是否", "要不要", "再考虑", "优惠", "办理这个活动"):
        assert persuasion not in acknowledgement


def test_no_enrollment_consent_sms_or_dnc_after_a_decline() -> None:
    _, outcome = _run_decline_episode()
    db = outcome.run.final_db
    assert db.enrollments == []
    assert db.consent_records == []
    assert db.action_consents == []
    assert db.sms_records == []
    assert db.do_not_call_records == []
    assert outcome.run.consent_events == []
    assert outcome.run.action_consent_events == []
    assert outcome.run.episode_completed is True


def test_evaluation_passes_under_the_v1_2_specification() -> None:
    _, outcome = _run_decline_episode()
    evaluation = outcome.evaluation
    assert evaluation.success, evaluation.summary
    results = {item["fact_id"]: item for item in evaluation.answer_fact_results}
    assert results["offer_validity"]["passed"] is True
    assert results["offer_no_auto_renewal"]["passed"] is True
    assert results["decline_acknowledged"]["passed"] is True
    assert results["decline_acknowledged"]["matched_pattern"] == "明白您的决定"
    assert results["false_enrollment_claim"]["passed"] is True


def test_no_state_change_is_introduced_by_the_acknowledgement() -> None:
    """The final acknowledgement must not itself touch business state."""
    _, outcome = _run_decline_episode()
    run = outcome.run
    initial = load_initial_state(find_task(TASK_DECLINE)).instantiate()
    for collection in STATE_COLLECTIONS:
        assert list(getattr(run.final_db, collection)) == list(
            getattr(initial, collection)
        ), collection


# ---------------------------------------------------------------------------
# 5. The refusal gate in isolation
# ---------------------------------------------------------------------------


def test_gate_emits_one_acknowledgement_then_finishes() -> None:
    agent = LLMBackedAgent(_FinishClient())
    refusal = [
        ConversationMessage(step=7, role="agent", content=SECOND_DECISION_QUESTION),
        ConversationMessage(step=8, role="user", content="那算了，我这次先不办了。"),
    ]

    first = agent.act(_agent_context(refusal))
    assert isinstance(first, SayAction)
    assert first.message == DECLINE_ACKNOWLEDGEMENT

    acknowledged = [
        *refusal,
        ConversationMessage(step=9, role="agent", content=DECLINE_ACKNOWLEDGEMENT),
    ]
    second = agent.act(_agent_context(acknowledged))
    assert isinstance(second, FinishAction)


def test_gate_does_not_fire_for_hesitation_or_a_neutral_close() -> None:
    agent = LLMBackedAgent(_FinishClient())
    for message in (
        "嗯……我还有点犹豫，这个到期以后会不会取消不了？",
        "哦，明白了，那这次就算了，谢谢你帮我查。",
        "先不换套餐，我只是想比较一下，谢谢。",
        "好的，听明白了，谢谢你解释。",
        "如果我不办理会怎样？",
    ):
        action = agent.act(
            _agent_context(
                [ConversationMessage(step=8, role="user", content=message)]
            )
        )
        assert isinstance(action, FinishAction), message


def test_gate_does_not_swallow_a_pending_outcome_report() -> None:
    """The existing Final Response Gate keeps priority over the refusal gate."""
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            [
                ConversationMessage(step=7, role="agent", content="正在为您办理。"),
                ConversationMessage(step=8, role="user", content="那算了，先不办了。"),
            ],
            traces=[
                _entry(
                    "enroll_offer",
                    {"enrollment_id": "ENR-0001", "status": "enrolled"},
                    step=8,
                )
            ],
        )
    )
    assert isinstance(action, SayAction)
    assert "enroll offer completed successfully" in action.message
    assert "ENR-0001" in action.message
    assert "明白您的决定" not in action.message


def test_gate_does_not_add_a_second_closing_line_after_an_outcome_report() -> None:
    """A delivered business outcome already closes the call."""
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            [
                ConversationMessage(
                    step=3, role="agent", content="已为您登记免打扰。"
                ),
                ConversationMessage(
                    step=4, role="user", content="不需要了，请不要再联系我。"
                ),
            ],
            traces=[
                _entry("register_do_not_call", {"status": "registered"}, step=2)
            ],
        )
    )
    assert isinstance(action, FinishAction)


def test_gate_still_fires_when_only_read_only_work_happened() -> None:
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            [
                ConversationMessage(step=3, role="agent", content="您是否同意办理？"),
                ConversationMessage(step=4, role="user", content="那就算了，先不办了。"),
            ],
            traces=[_entry("get_offer_details", {"offer_id": "OFFER_D02_002"}, step=2)],
        )
    )
    assert isinstance(action, SayAction)


def test_lifecycle_allows_a_final_message_after_the_terminal_customer_turn() -> None:
    """The runner grants the turn; the LLM gate only makes the agent use it."""

    class _ClosingAgent:
        def __init__(self) -> None:
            self._step = 0

        def act(self, context: AgentContext):
            self._step += 1
            step = self._step
            line = context.task.phone_number_id
            offer = context.task.offer_id or ""
            if step == 1:
                return SayAction(message=GREETING)
            if step == 2:
                return SayAction(message=IDENTITY_REQUEST)
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
                return SayAction(message=DISCLOSURE_AND_CONSENT)
            if step == 7:
                return SayAction(message=SECOND_DECISION_QUESTION)
            if step == 8:
                # The customer has just declined and closed the conversation.
                return SayAction(message=DECLINE_ACKNOWLEDGEMENT)
            return FinishAction(reason="closing message sent")

    outcome = execute_task(
        find_task(TASK_DECLINE), agent_factory=_ClosingAgent
    )
    run = outcome.run
    assert run.customer_conversation_closed is True
    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.post_closure_actions == 1
    assert run.conversation[-2].role == "user"
    assert run.conversation[-2].content.startswith("那算了")
    assert run.conversation[-1].role == "agent"
    assert run.conversation[-1].content == DECLINE_ACKNOWLEDGEMENT


# ---------------------------------------------------------------------------
# 6. Cross-domain regression
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_id", CROSS_DOMAIN_DEMO_TASKS)
def test_baseline_agent_still_completes_regression_tasks(task_id: str) -> None:
    outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
    assert outcome.run.stop_reason is StopReason.AGENT_FINISHED
    assert outcome.evaluation.success, (task_id, outcome.evaluation.summary)


def test_d01_identity_and_read_only_flow_is_preserved() -> None:
    outcome = execute_task(
        find_task("d01_plan_content_explanation"), agent_factory=BenchmarkDemoAgent
    )
    run = outcome.run
    assert outcome.evaluation.success, outcome.evaluation.summary
    tool_names = run.successful_tool_names()
    assert tool_names.index("verify_customer_identity") < tool_names.index(
        "get_customer_account"
    )
    assert "get_plan_details" in tool_names
    assert run.final_db.plan_change_requests == []


def test_d06_final_response_gate_report_is_unchanged() -> None:
    """A mutating D06-style result is still reported before the finish."""
    action = LLMBackedAgent(_FinishClient()).act(
        _agent_context(
            [
                ConversationMessage(step=1, role="agent", content="您好。"),
                ConversationMessage(step=2, role="user", content="我同意办理。"),
            ],
            traces=[
                _entry("send_notification", {"status": "SENT", "kind": "CONFIRMATION"})
            ],
        )
    )
    assert isinstance(action, SayAction)
    assert "send notification completed successfully" in action.message
    assert "status=SENT" in action.message


def test_decline_task_declares_the_same_safety_expectations() -> None:
    """The repair must not relax any negative state expectation."""
    task = find_task(TASK_DECLINE)
    expected = {
        (item.collection, item.expected_count) for item in task.expected_actions
    }
    assert expected == {
        ("enrollments", 0),
        ("consent_records", 0),
        ("sms_records", 0),
        ("do_not_call_records", 0),
    }
    assert task.required_tools == [
        "verify_customer_identity",
        "get_offer_details",
        "check_offer_eligibility",
    ]
    forbidden = {
        item.fact_id: item for item in task.answer_assertions.forbidden_facts
    }
    assert "false_enrollment_claim" in forbidden
    assert forbidden["false_enrollment_claim"].forbidden_patterns
