"""D01 comparison Simulator fallback repair: ``done`` follows the comparison.

The preserved system-v4 run
``outputs/live_llm_validation/deepseek/D01-system-v4-regression-20261122-r1/
RUN-a95ed9e2040f407e`` recorded ``d01_plan_comparison`` v1.0 as four customer
turns: ``ask_compare`` -> ``provide_token`` -> ``fallback`` -> ``done``. The
agent had already delivered the complete comparison on the third customer turn,
but the generic ``done`` step was gated at ``min_turn=4``. Of the steps declared
before it, ``ask_compare`` (``first``) and ``provide_token``
(``agent_asks_identity``) had already fired and ``decline_change``
(``agent_asks_consent``) did not match, so the completed comparison matched
nothing and the Simulator repeated its fallback.

The repair is one task-level field: ``done.min_turn`` is 3, the turn on which a
completed comparison actually arrives after the two scripted customer turns. The
specific ``decline_change`` step is declared before ``done`` and the Simulator
takes the first matching step, so an agent that asks for plan-change consent
still receives the refusal instead of the generic sign-off. No Simulator code,
global trigger or other task changed.

Every test here is deterministic: it drives the packaged script, the packaged
reference agent or a canned agent. No provider is constructed and no LLM call is
made.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cm_outbound_bench.agent import AgentAction, AgentContext, FinishAction, SayAction
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import execute_task, load_initial_state
from cm_outbound_bench.benchmark.replay import replay_trace, resolve_task
from cm_outbound_bench.benchmark.simulator import Intent, ScriptedSimulator
from cm_outbound_bench.benchmark.state import hash_database
from cm_outbound_bench.benchmark.trace import (
    TraceSchemaError,
    load_trace,
    trace_file_sha256,
)
from cm_outbound_bench.runner import StopReason

TASK_ID = "d01_plan_comparison"
CONTENT_TASK_ID = "d01_plan_content_explanation"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIVE_RUN_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "live_llm_validation"
    / "deepseek"
    / "D01-system-v4-regression-20261122-r1"
)
LIVE_RUN_ID = "RUN-a95ed9e2040f407e"
LIVE_MANIFEST_PATH = LIVE_RUN_DIR / f"{LIVE_RUN_ID}.json"
LIVE_TRACE_PATH = LIVE_RUN_DIR / LIVE_RUN_ID / "trace.json"

#: The v1.0 hash that this repair supersedes: the canonical payload of the
#: unchanged v1.0 task under evaluator 2.2, which is the hash every v1.0-aware
#: pin held before the repair. It stays a historical value.
V1_0_TASK_HASH = (
    "ec0c2a1c9b825aae9f5084cd1bc85c85783ac1a6e9a7268871087168e6b1f2d8"
)
#: The evaluator label that produced every hash in this module's history. The
#: current evaluator is 2.4, whose only effect on a task hash is that the label is
#: part of the canonical payload.
HISTORICAL_EVALUATOR_VERSION = "2.2"
#: The evaluator label that produced the v1.2 and v1.3 hashes of this task.
EVALUATOR_2_3 = "2.3"
#: The recorded hash of the older preserved run. That run predates the 2.2
#: promotion (`evaluator_version = 2.1`), when the same v1.0 task produced a
#: different canonical payload, so both historical hashes must survive.
RECORDED_V1_0_TASK_HASH = (
    "1cc1a70e272df07a6b94d59893fc6618f6941e3dfa70a30e412b7015d2745a76"
)
#: The repaired comparison task under the current evaluator label and scope.
#: This is the v1.2 hash: ``any_agent_message`` assertion scope, no explicit
#: identity criterion. It stays a historical value after the v1.3 repair.
V1_2_TASK_HASH = (
    "6d60b32b1385fbb3246364bf48b177e413299ee06c8a651ecbf01e07baeda5f6"
)
#: The current v1.3 hash: the task's ``require_identity_verification`` is now an
#: explicit ``false`` because a public catalogue comparison never needs account
#: access. The protected-account and protected-disclosure checks stay active.
V1_3_TASK_HASH = (
    "1f021086f2097ad88dec161b5e43ddc4355e3b867709d049b6e5d612ba482632"
)
#: The evaluator-2.2 v1.1 hash. It stays a historical value.
V1_1_TASK_HASH_EVALUATOR_2_2 = (
    "22789339d3479870ac500b274c01214cd24d537e4d54a428a81b3ae06d1e3178"
)
#: The evaluator-2.3 v1.1 hash, from before the assertion-scope repair.
V1_1_TASK_HASH_EVALUATOR_2_3_SCOPE_FINAL_ANSWER = (
    "8a6157dc51c7d52ae279197a839a79d8d14a2d80379bfc98a7b946bc878626e9"
)
#: The other D01 task is deliberately out of scope. Its task script and version
#: do not move; its hash follows the evaluator 2.3 metadata promotion.
CONTENT_TASK_HASH = (
    "d525266380bd0e835b97209f58de74ed36d12652a327c3026ff33ce62f4735ae"
)

GREETING = "您好，我是中国移动客服。"
IDENTITY_REQUEST = "为了核对机主身份，请提供您的测试身份令牌。"
COMPARISON_ANSWER = (
    "身份验证已通过，为您对比这两个套餐：\n\n"
    "**模拟畅享套餐（PLAN_M）**：月费 59 元，流量 20000 MB。\n"
    "**模拟尊享套餐（PLAN_L）**：月费 99 元，流量 60000 MB。\n"
    "两者月费相差 40 元。本次仅为比较，没有为您办理任何套餐变更。"
)
CHINESE_CONSENT_REQUEST = "您是否同意我为您办理套餐变更？"
ENGLISH_CONSENT_REQUEST = "Would you like me to change your plan to PLAN_L?"
#: Messages that ask for plan-change consent while reaching the same turn on
#: which the generic ``done`` step has become eligible.
CONSENT_MESSAGES = (
    COMPARISON_ANSWER + "\n\n" + CHINESE_CONSENT_REQUEST,
    CHINESE_CONSENT_REQUEST,
    ENGLISH_CONSENT_REQUEST,
)


def _task():
    return find_task(TASK_ID)


def _simulator() -> ScriptedSimulator:
    task = _task()
    return ScriptedSimulator(task.simulator, offer_id=task.offer_id)


def _canonical_hash(payload: dict) -> str:
    """Hash a canonical task payload exactly like ``BenchmarkTask.task_hash``."""
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


class _ConsentAskingAgent:
    """Canned deterministic agent that asks for plan-change consent.

    It mirrors the live agent shape that exposes the precedence rule: greet and
    request the identity twice (the second request is answered with the token),
    then deliver the substantive comparison and ask for consent in the same
    turn. It never calls a tool and never mutates business state.
    """

    def __init__(self) -> None:
        self._messages = [
            IDENTITY_REQUEST,
            IDENTITY_REQUEST,
            COMPARISON_ANSWER + "\n\n" + CHINESE_CONSENT_REQUEST,
        ]
        self._index = 0

    def act(self, context: AgentContext) -> AgentAction:
        del context
        if self._index < len(self._messages):
            message = self._messages[self._index]
            self._index += 1
            return SayAction(message=message)
        return FinishAction(reason="customer declined the plan change")


# ---------------------------------------------------------------------------
# 1-2. A normal comparison closes on the next customer turn without a fallback
# ---------------------------------------------------------------------------


def test_completed_comparison_triggers_done_on_the_third_customer_turn() -> None:
    simulator = _simulator()
    assert simulator.respond(GREETING).step_id == "ask_compare"
    assert simulator.respond(IDENTITY_REQUEST).step_id == "provide_token"

    reply = simulator.respond(COMPARISON_ANSWER)

    assert reply.step_id == "done"
    assert reply.message == "好，那我再想想，谢谢你。"
    assert reply.metadata["turn"] == 3
    assert reply.intent is Intent.END
    assert reply.ends_conversation is True
    assert reply.consent_granted is False


def test_completed_comparison_no_longer_produces_a_fallback() -> None:
    simulator = _simulator()
    replies = [
        simulator.respond(message)
        for message in (GREETING, IDENTITY_REQUEST, COMPARISON_ANSWER)
    ]

    assert [reply.step_id for reply in replies] == [
        "ask_compare",
        "provide_token",
        "done",
    ]
    assert "fallback" not in {reply.step_id for reply in replies}
    assert simulator.fallback_count == 0


def test_normal_comparison_episode_closes_naturally_and_passes() -> None:
    """The packaged reference agent completes the task on the third turn."""
    outcome = execute_task(_task(), agent_factory=BenchmarkDemoAgent)
    run = outcome.run
    events = [event.step_id for event in run.simulator_events]

    assert outcome.evaluation.success, outcome.evaluation.summary
    assert outcome.evaluation.failed_checks == []
    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.customer_conversation_closed is True
    assert events == ["ask_compare", "provide_token", "done"]
    assert "fallback" not in events
    # The customer's sign-off is the last turn and the comparison remains the
    # substantive final answer.
    assert run.conversation[-1].role == "user"
    facts = {item["fact_id"]: item for item in outcome.evaluation.answer_fact_results}
    assert facts["plan_m_fee"]["matched_pattern"] == "59 元"
    assert facts["plan_l_fee"]["matched_pattern"] == "99 元"
    assert facts["fee_difference"]["matched_pattern"] == "40 元"
    assert run.final_db.plan_change_requests == []
    assert run.final_db.action_consents == []


# ---------------------------------------------------------------------------
# 3. The consent-triggered step still outranks the generic completion step
# ---------------------------------------------------------------------------


def test_decline_change_is_declared_before_the_generic_done_step() -> None:
    steps = _task().simulator.steps
    step_ids = [step.step_id for step in steps]

    assert step_ids.index("decline_change") < step_ids.index("done")
    decline = steps[step_ids.index("decline_change")]
    done = steps[step_ids.index("done")]
    assert decline.trigger == "agent_asks_consent"
    assert decline.consent_action is None
    # ``done`` is eligible on the comparison turn, so the earlier consent step is
    # the only thing that outranks it.
    assert done.trigger == "always"
    assert done.min_turn == 3


def test_step_selection_is_first_match_not_trigger_specificity() -> None:
    """A reversed copy proves declaration order is what decides the turn."""
    task = _task()
    script = task.simulator.model_copy(deep=True)
    step_ids = [step.step_id for step in script.steps]
    decline_index = step_ids.index("decline_change")
    done_index = step_ids.index("done")
    script.steps[decline_index], script.steps[done_index] = (
        script.steps[done_index],
        script.steps[decline_index],
    )

    simulator = ScriptedSimulator(script, offer_id=task.offer_id)
    simulator.respond(GREETING)
    simulator.respond(IDENTITY_REQUEST)
    reordered = simulator.respond(CHINESE_CONSENT_REQUEST)

    assert reordered.step_id == "done"


@pytest.mark.parametrize("consent_message", CONSENT_MESSAGES)
def test_consent_question_reaches_decline_change_before_done(
    consent_message: str,
) -> None:
    simulator = _simulator()
    assert simulator.respond(GREETING).step_id == "ask_compare"
    assert simulator.respond(IDENTITY_REQUEST).step_id == "provide_token"

    reply = simulator.respond(consent_message)

    assert reply.step_id == "decline_change"
    assert reply.message == "先不换套餐，我只是想比较一下，谢谢。"
    assert reply.intent is Intent.REFUSAL
    assert reply.ends_conversation is True
    assert reply.consent_granted is False
    assert reply.consent_action is None
    assert reply.consent_target == ""
    assert simulator.fallback_count == 0


# ---------------------------------------------------------------------------
# 4. Declining a plan change grants no consent and mutates no state
# ---------------------------------------------------------------------------


def test_declined_plan_change_grants_no_consent_and_changes_no_state() -> None:
    task = _task()
    initial = load_initial_state(task)
    outcome = execute_task(task, agent_factory=_ConsentAskingAgent)
    run = outcome.run

    assert [event.step_id for event in run.simulator_events] == [
        "ask_compare",
        "provide_token",
        "decline_change",
    ]
    decline = run.simulator_events[-1]
    assert decline.intent == "refusal"
    assert decline.consent_granted is False
    assert decline.consent_action is None
    assert decline.ends_conversation is True

    assert run.stop_reason is StopReason.AGENT_FINISHED
    assert run.customer_conversation_closed is True
    assert run.consent_events == []
    assert run.action_consent_events == []
    assert run.final_db.plan_change_requests == []
    assert run.final_db.action_consents == []
    assert run.final_db.consent_records == []
    assert hash_database(run.final_db) == initial.state_hash


# ---------------------------------------------------------------------------
# 5. The other D01 task is untouched
# ---------------------------------------------------------------------------


def test_content_explanation_task_and_script_are_unchanged() -> None:
    task = find_task(CONTENT_TASK_ID)

    assert task.task_version == "1.1"
    assert task.task_hash() == CONTENT_TASK_HASH
    assert [
        (step.step_id, step.trigger, step.min_turn)
        for step in task.simulator.steps
    ] == [
        ("ask_content", "first", 0),
        ("provide_token", "agent_asks_identity", 0),
        ("done", "always", 4),
    ]

    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    assert outcome.evaluation.success, outcome.evaluation.summary
    assert outcome.run.final_db.plan_change_requests == []
    assert outcome.run.final_db.recharges == []


# ---------------------------------------------------------------------------
# 6. The new task version and hash, and the preserved historical identity
# ---------------------------------------------------------------------------


def test_task_version_carries_the_repairs_and_the_task_hash_is_updated() -> None:
    task = _task()

    assert task.task_version == "1.3"
    assert task.task_hash() == V1_3_TASK_HASH
    assert task.task_hash() != V1_2_TASK_HASH
    assert task.task_hash() != V1_0_TASK_HASH


def test_repair_changed_only_the_version_the_scope_and_the_done_min_turn() -> None:
    """Restoring the historical fields must reproduce the historical hashes."""
    payload = _task().canonical_payload()
    assert _canonical_hash(payload) == V1_3_TASK_HASH

    # Reverting the explicit identity criterion, the version and the evaluator
    # label reproduces the v1.2 hash recorded under evaluator 2.3.
    payload["task_version"] = "1.2"
    payload["success_criteria"]["require_identity_verification"] = None
    payload["evaluator_version"] = EVALUATOR_2_3
    assert _canonical_hash(payload) == V1_2_TASK_HASH

    # Reverting only the assertion scope reproduces the v1.1 hash under the
    # evaluator 2.3 label.
    payload["task_version"] = "1.1"
    payload["answer_assertions"]["scope"] = "final_answer"
    assert (
        _canonical_hash(payload)
        == V1_1_TASK_HASH_EVALUATOR_2_3_SCOPE_FINAL_ANSWER
    )

    # Reverting only the evaluator label reproduces the 2.2-era v1.1 hash: the
    # metadata promotion is the only reason that hash moved.
    payload["evaluator_version"] = HISTORICAL_EVALUATOR_VERSION
    assert _canonical_hash(payload) == V1_1_TASK_HASH_EVALUATOR_2_2

    # Reverting the task version and the repaired min_turn reproduces the
    # evaluator-2.2 v1.0 hash this repair superseded.
    payload["task_version"] = "1.0"
    for step in payload["simulator"]["steps"]:
        if step["step_id"] == "done":
            step["min_turn"] = 4

    assert _canonical_hash(payload) == V1_0_TASK_HASH


def test_preserved_v1_0_run_keeps_its_historical_identity_and_defect() -> None:
    manifest = json.loads(LIVE_MANIFEST_PATH.read_text(encoding="utf-8"))
    # The preserved run recorded evaluator 2.1, so its v1.0 task hash belongs to
    # that era. Neither it nor the 2.2-era v1.0 hash is rewritten by this repair.
    assert manifest["evaluator_version"] == "2.1"
    assert manifest["task_version"] == "1.0"
    assert manifest["task_hash"] == RECORDED_V1_0_TASK_HASH
    assert manifest["task_hash"] != V1_0_TASK_HASH
    assert manifest["task_hash"] != _task().task_hash()
    assert trace_file_sha256(LIVE_TRACE_PATH) == manifest["trace_sha256"]

    trace = load_trace(LIVE_TRACE_PATH)
    assert trace.task_version == "1.0"
    # The recorded defect is preserved, not rewritten: the fallback really sits
    # between the comparison and the sign-off in the historical trace.
    assert [event.step_id for event in trace.simulator_events] == [
        "ask_compare",
        "provide_token",
        "fallback",
        "done",
    ]
    # A v1.0 trace is never silently resolved against the v1.1 catalogue.
    with pytest.raises(TraceSchemaError):
        resolve_task(trace)


def test_preserved_action_stream_now_closes_on_the_comparison_turn() -> None:
    """The recorded live actions, replayed against v1.1, lose the fallback turn.

    The recorded actions are re-executed unchanged; only the Simulator's replies
    and the downstream conversation differ. This is a labelled diagnostic against
    the repaired specification, never a rewrite of the preserved artifact.
    """
    trace = load_trace(LIVE_TRACE_PATH)
    replay = replay_trace(trace, task=_task())

    assert [event.step_id for event in replay.replayed_run.simulator_events] == [
        "ask_compare",
        "provide_token",
        "done",
    ]
    # The agent's recorded actions, tool calls and final state still reproduce.
    # The only intended divergence is the removed fallback customer turn.
    assert "action_sequence_mismatch" not in replay.divergences
    assert "tool_trace_mismatch" not in replay.divergences
    assert "final_state_mismatch" not in replay.divergences
    assert "simulator_event_mismatch" in replay.divergences
    assert replay.replayed_evaluation.success is True

    # The preserved run's recorded evaluation stays the historical failure.
    assert trace.evaluation.success is False
