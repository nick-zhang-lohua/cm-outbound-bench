"""Stage 8, 9 and 11: lifecycle, budgets, tool hardening and observation safety."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cm_outbound_bench.agent import (
    AgentContext,
    CallToolAction,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.state import StateStore
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.environment import CmOutboundEnvironment
from cm_outbound_bench.paths import data_path
from cm_outbound_bench.runner import StopReason


class ChattyAgent:
    def act(self, context: AgentContext):
        return SayAction(message="我还在确认，请稍等。")


class LoopingToolAgent:
    def __init__(self, tool_name: str, arguments: dict) -> None:
        self._tool_name = tool_name
        self._arguments = arguments

    def act(self, context: AgentContext):
        return CallToolAction(tool_name=self._tool_name, arguments=self._arguments)


def _with_criteria(task_id: str, **criteria):
    task = find_task(task_id)
    updated = task.success_criteria.model_copy(update=criteria)
    return task.model_copy(update={"success_criteria": updated})


# ---------------------------------------------------------------------------
# Customer conversation closure versus episode termination
# ---------------------------------------------------------------------------


def test_customer_closure_stops_further_simulator_turns() -> None:
    task = find_task("d02_direct_refusal_do_not_call")
    outcome = execute_task(task, agent_factory=ChattyAgent)

    assert outcome.run.customer_conversation_closed is True
    # Exactly one scripted customer turn; no further simulator reply after closure.
    assert len(outcome.run.simulator_events) == 1
    assert outcome.run.stop_reason is StopReason.POST_CLOSURE_LIMIT
    assert outcome.run.error is not None
    assert outcome.run.error.code == "post_closure_action_limit"


def test_post_conversation_business_operation_still_runs() -> None:
    """The customer hangs up, then the agent records the opt-out and finishes."""

    class PostCallAgent:
        def __init__(self) -> None:
            self._step = 0

        def act(self, context: AgentContext):
            self._step += 1
            if self._step == 1:
                return CallToolAction(tool_name="get_offer_details", arguments={
                    "offer_id": context.task.offer_id or "",
                })
            if self._step == 2:
                return SayAction(message="您好，我是中国移动客服。")
            if self._step == 3:
                return CallToolAction(
                    tool_name="register_do_not_call",
                    arguments={"phone_number_id": context.task.phone_number_id},
                )
            if self._step == 4:
                return SayAction(message="已为您登记免打扰，感谢接听。")
            return FinishAction(reason="post-call work complete")

    task = find_task("d02_direct_refusal_do_not_call")
    outcome = execute_task(task, agent_factory=PostCallAgent)

    assert outcome.run.customer_conversation_closed is True
    assert len(outcome.run.simulator_events) == 1
    assert outcome.run.stop_reason is StopReason.AGENT_FINISHED
    assert outcome.run.episode_completed is True
    assert len(outcome.run.final_db.do_not_call_records) == 1
    assert outcome.run.post_closure_actions == 2
    assert outcome.evaluation.success, outcome.evaluation.summary


def test_baseline_tasks_report_episode_completed() -> None:
    for task_id in (
        "d02_offer_benefit_inquiry_accept",
        "d06_recharge_confirmation_notification",
        "d01_plan_comparison",
    ):
        outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
        assert outcome.run.stop_reason is StopReason.AGENT_FINISHED
        assert outcome.run.episode_completed is True
        assert outcome.run.error is None


# ---------------------------------------------------------------------------
# Hard budgets
# ---------------------------------------------------------------------------


def test_tool_call_budget_is_enforced_not_only_scored() -> None:
    task = _with_criteria("d01_plan_comparison", max_tool_calls=2)
    outcome = execute_task(
        task,
        agent_factory=lambda: LoopingToolAgent("list_plans", {}),
    )
    assert outcome.run.stop_reason is StopReason.TOOL_BUDGET_EXCEEDED
    assert outcome.run.tool_call_count == 2
    assert outcome.run.error is not None
    assert not outcome.evaluation.success
    failed = {check.name for check in outcome.evaluation.checks if not check.passed}
    assert "tool_budget" in failed
    assert "agent_finished" in failed


def test_turn_budget_is_enforced_not_only_scored() -> None:
    task = _with_criteria("d02_hesitation_then_decline", max_turns=2)
    outcome = execute_task(task, agent_factory=ChattyAgent)
    assert outcome.run.stop_reason is StopReason.TURN_BUDGET_EXCEEDED
    assert outcome.run.conversation_turn_count > 2
    failed = {check.name for check in outcome.evaluation.checks if not check.passed}
    assert "turn_budget" in failed


def test_tool_error_budget_is_enforced() -> None:
    task = _with_criteria("d01_plan_comparison", max_tool_errors=0)
    outcome = execute_task(
        task,
        agent_factory=lambda: LoopingToolAgent("get_plan_details", {}),
    )
    assert outcome.run.stop_reason is StopReason.TOOL_ERROR_BUDGET_EXCEEDED
    assert outcome.run.tool_trace
    assert not outcome.run.tool_trace[0].result.ok


def test_wall_clock_deadline_is_enforced_between_actions() -> None:
    task = _with_criteria("d01_plan_comparison", deadline_seconds=1e-9)
    outcome = execute_task(task, agent_factory=ChattyAgent)
    assert outcome.run.stop_reason is StopReason.DEADLINE_EXCEEDED
    assert outcome.run.steps_executed == 0
    assert outcome.run.error is not None
    assert outcome.run.error.code == "episode_deadline_exceeded"


def test_step_limit_remains_the_last_resort() -> None:
    outcome = execute_task(
        find_task("d02_hesitation_then_decline"),
        agent_factory=ChattyAgent,
        max_steps=3,
    )
    assert outcome.run.stop_reason is StopReason.STEP_LIMIT
    assert outcome.run.steps_executed == 3


# ---------------------------------------------------------------------------
# Unsupported actions
# ---------------------------------------------------------------------------


def test_unsupported_action_is_a_structured_failure_not_a_crash() -> None:
    class BadAgent:
        def act(self, context: AgentContext):
            return "this is not an action"

    outcome = execute_task(
        find_task("d01_plan_comparison"), agent_factory=BadAgent
    )
    assert outcome.run.stop_reason is StopReason.INVALID_ACTION
    assert outcome.run.error is not None
    assert outcome.run.error.code == "unsupported_agent_action"
    assert "str" in outcome.run.error.message
    assert not outcome.evaluation.success
    finished = next(
        check
        for check in outcome.evaluation.checks
        if check.name == "agent_finished"
    )
    assert not finished.passed
    assert "invalid_action" in finished.detail


def test_unsupported_action_does_not_abort_the_rest_of_a_batch(
    tmp_path, monkeypatch, capsys
) -> None:
    from cm_outbound_bench.benchmark import cli as benchmark_cli
    from cm_outbound_bench.cli import main

    real_execute = benchmark_cli.execute_task

    def factory(*args, **kwargs):
        raise AssertionError("unused")

    assert factory  # the real agent factory is selected by the CLI

    class _Raising:
        def __init__(self) -> None:
            self._done = False

        def act(self, context):
            if self._done:
                raise AssertionError("unexpected extra step")
            self._done = True
            return 12345

    monkeypatch.setattr(
        benchmark_cli,
        "_build_agent",
        lambda args, seed=None: (
            _Raising,
            "bad_agent",
            {"prompt_version": "test"},
        ),
    )
    exit_code = main(
        [
            "benchmark",
            "run",
            "--domain",
            "D01",
            "--output-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "INCOMPLETE" in captured.out
    from cm_outbound_bench.benchmark.metrics import load_manifests

    manifests = load_manifests(tmp_path)
    assert len(manifests) == 2
    assert all(item.run_status == "incomplete" for item in manifests)
    assert real_execute  # silence linters


# ---------------------------------------------------------------------------
# Environment-level tool allow-list
# ---------------------------------------------------------------------------


def _environment(allowed: frozenset[str] | None) -> CmOutboundEnvironment:
    store = StateStore.from_file(
        data_path("synthetic", "states", "d02_promotion.json")
    )
    state = store.get("STATE-D02-004")
    return CmOutboundEnvironment(
        state.instantiate(),
        "policy",
        toolkit_factory=build_toolkit,
        allowed_tools=allowed,
    )


def test_environment_rejects_a_tool_outside_the_allow_list() -> None:
    env = _environment(frozenset({"get_offer_details"}))
    before = env.db.model_dump(mode="json")

    result = env.execute_tool(
        "enroll_offer",
        {"phone_number_id": "PN105", "offer_id": "OFFER_D02_001"},
        step=1,
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "tool_not_allowed"
    assert env.db.model_dump(mode="json") == before
    assert len(env.tool_trace) == 1
    assert env.tool_trace[0].result.error.code == "tool_not_allowed"


def test_environment_allows_a_listed_tool() -> None:
    env = _environment(frozenset({"get_offer_details"}))
    result = env.execute_tool(
        "get_offer_details", {"offer_id": "OFFER_D02_001"}, step=1
    )
    assert result.ok


def test_environment_without_an_allow_list_is_unrestricted() -> None:
    env = _environment(None)
    assert env.is_tool_allowed("submit_cancellation")


def test_environment_result_objects_are_independent_copies() -> None:
    env = _environment(frozenset({"get_offer_details"}))
    first = env.execute_tool(
        "get_offer_details", {"offer_id": "OFFER_D02_001"}, step=1
    )
    first.data["name"] = "mutated by the agent"
    assert env.tool_trace[0].result.data["name"] != "mutated by the agent"


# ---------------------------------------------------------------------------
# Idempotency and duplicate semantics
# ---------------------------------------------------------------------------


@pytest.fixture
def recharge_toolkit():
    store = StateStore.from_file(
        data_path("synthetic", "states", "d03_billing.json")
    )
    toolkit = build_toolkit(store.get("STATE-D03-002").instantiate())
    toolkit.execute(
        "verify_customer_identity",
        {"phone_number_id": "PN302", "identity_token": "TEST_IDENTITY_C302"},
    )
    toolkit.record_action_consent("PN302", "RECHARGE", "BA-PN302")
    return toolkit


def test_same_operation_id_does_not_duplicate_a_recharge(recharge_toolkit) -> None:
    toolkit = recharge_toolkit
    first = toolkit.execute(
        "recharge_account",
        {"phone_number_id": "PN302", "amount_cents": 5000, "operation_id": "OP-1"},
    )
    second = toolkit.execute(
        "recharge_account",
        {"phone_number_id": "PN302", "amount_cents": 5000, "operation_id": "OP-1"},
    )
    assert first.ok and second.ok
    assert second.data["idempotent_replay"] is True
    assert second.data["recharge_id"] == first.data["recharge_id"]
    assert len(toolkit.legacy.db.recharges) == 1
    assert toolkit.legacy.db.billing_accounts[0].balance_cents == 4200


def test_distinct_operation_ids_are_distinct_recharges(recharge_toolkit) -> None:
    toolkit = recharge_toolkit
    first = toolkit.execute(
        "recharge_account",
        {"phone_number_id": "PN302", "amount_cents": 1000, "operation_id": "OP-A"},
    )
    second = toolkit.execute(
        "recharge_account",
        {"phone_number_id": "PN302", "amount_cents": 1000, "operation_id": "OP-B"},
    )
    assert first.data["recharge_id"] != second.data["recharge_id"]
    assert len(toolkit.legacy.db.recharges) == 2


def test_recharge_without_an_operation_id_still_creates_a_new_transaction(
    recharge_toolkit,
) -> None:
    toolkit = recharge_toolkit
    toolkit.execute(
        "recharge_account", {"phone_number_id": "PN302", "amount_cents": 1000}
    )
    toolkit.execute(
        "recharge_account", {"phone_number_id": "PN302", "amount_cents": 1000}
    )
    assert len(toolkit.legacy.db.recharges) == 2


@pytest.fixture
def notification_toolkit():
    store = StateStore.from_file(
        data_path("synthetic", "states", "d06_notification.json")
    )
    toolkit = build_toolkit(store.get("STATE-D06-001").instantiate())
    toolkit.execute(
        "verify_customer_identity",
        {"phone_number_id": "PN601", "identity_token": "TEST_IDENTITY_C601"},
    )
    return toolkit


def test_duplicate_notification_is_rejected(notification_toolkit) -> None:
    toolkit = notification_toolkit
    first = toolkit.execute(
        "send_notification",
        {"phone_number_id": "PN601", "kind": "CONFIRMATION", "related_ref": "REF-1"},
    )
    second = toolkit.execute(
        "send_notification",
        {"phone_number_id": "PN601", "kind": "CONFIRMATION", "related_ref": "REF-1"},
    )
    assert first.ok
    assert not second.ok
    assert second.error.code == "notification_already_sent"
    assert len(toolkit.legacy.db.notifications) == 1


def test_notification_operation_id_replay_is_idempotent(notification_toolkit) -> None:
    toolkit = notification_toolkit
    first = toolkit.execute(
        "send_notification",
        {
            "phone_number_id": "PN601",
            "kind": "CONFIRMATION",
            "related_ref": "REF-2",
            "operation_id": "NOTIFY-1",
        },
    )
    second = toolkit.execute(
        "send_notification",
        {
            "phone_number_id": "PN601",
            "kind": "CONFIRMATION",
            "related_ref": "REF-2",
            "operation_id": "NOTIFY-1",
        },
    )
    assert first.ok and second.ok
    assert second.data["idempotent_replay"] is True
    assert len(toolkit.legacy.db.notifications) == 1


@pytest.fixture
def cancellation_toolkit():
    store = StateStore.from_file(
        data_path("synthetic", "states", "d06_notification.json")
    )
    toolkit = build_toolkit(store.get("STATE-D06-001").instantiate())
    toolkit.execute(
        "verify_customer_identity",
        {"phone_number_id": "PN601", "identity_token": "TEST_IDENTITY_C601"},
    )
    toolkit.record_action_consent("PN601", "CANCELLATION", "OFFER_D02_001")
    return toolkit


def test_duplicate_cancellation_does_not_create_a_second_request(
    cancellation_toolkit,
) -> None:
    toolkit = cancellation_toolkit
    first = toolkit.execute(
        "submit_cancellation",
        {"phone_number_id": "PN601", "target_type": "OFFER", "target_id": "OFFER_D02_001"},
    )
    second = toolkit.execute(
        "submit_cancellation",
        {"phone_number_id": "PN601", "target_type": "OFFER", "target_id": "OFFER_D02_001"},
    )
    assert first.ok and second.ok
    assert second.data["already_requested"] is True
    assert second.data["request_id"] == first.data["request_id"]
    assert len(toolkit.legacy.db.cancellation_requests) == 1


def test_enrollment_and_sms_remain_duplicate_safe() -> None:
    store = StateStore.from_file(
        data_path("synthetic", "states", "d02_promotion.json")
    )
    toolkit = build_toolkit(store.get("STATE-D02-001").instantiate())
    toolkit.execute(
        "verify_customer_identity",
        {"phone_number_id": "PN101", "identity_token": "TEST_IDENTITY_C101"},
    )
    toolkit.record_simulator_consent("PN101", "OFFER_D02_001")
    enrollment = toolkit.execute(
        "enroll_offer", {"phone_number_id": "PN101", "offer_id": "OFFER_D02_001"}
    )
    assert enrollment.ok
    duplicate = toolkit.execute(
        "enroll_offer", {"phone_number_id": "PN101", "offer_id": "OFFER_D02_001"}
    )
    assert duplicate.error.code == "already_enrolled"
    sms = toolkit.execute(
        "send_confirmation_sms", {"enrollment_id": enrollment.data["enrollment_id"]}
    )
    assert sms.ok
    again = toolkit.execute(
        "send_confirmation_sms", {"enrollment_id": enrollment.data["enrollment_id"]}
    )
    assert again.error.code == "confirmation_already_sent"


# ---------------------------------------------------------------------------
# Observation safety
# ---------------------------------------------------------------------------


def test_agent_context_is_frozen() -> None:
    captured: list[AgentContext] = []

    class Capture(BenchmarkDemoAgent):
        def act(self, context: AgentContext):
            captured.append(context)
            return super().act(context)

    execute_task(find_task("d01_plan_comparison"), agent_factory=Capture)
    context = captured[0]
    with pytest.raises(ValidationError):
        context.step = 99
    with pytest.raises(ValidationError):
        context.task = context.task.model_copy(update={"objective": "changed"})


def test_agent_cannot_rewrite_the_runner_conversation() -> None:
    class MutatingAgent:
        def __init__(self) -> None:
            self._step = 0

        def act(self, context: AgentContext):
            self._step += 1
            if self._step == 1:
                return SayAction(message="您好，我是客服代表。")
            # In-place mutation of the observation must not reach the runner.
            context.messages.clear()
            context.tool_results.clear()
            return FinishAction(reason="mutated the observation")

    outcome = execute_task(
        find_task("d02_offer_benefit_inquiry_accept"), agent_factory=MutatingAgent
    )
    assert [message.content for message in outcome.run.conversation] == [
        "您好，我是客服代表。",
        "你说是流量活动？具体是什么内容，你先讲讲。",
    ]
    assert outcome.run.simulator_events[0].message


def test_agent_cannot_mutate_tool_history_it_received() -> None:
    class MutatingToolAgent:
        def __init__(self) -> None:
            self._step = 0

        def act(self, context: AgentContext):
            self._step += 1
            if self._step == 1:
                return CallToolAction(tool_name="list_plans", arguments={})
            assert context.tool_results
            context.tool_results.clear()
            from cm_outbound_bench.agent import ConversationMessage

            context.messages.append(
                ConversationMessage(step=1, role="agent", content="tampered")
            )
            return FinishAction(reason="mutated")

    outcome = execute_task(
        find_task("d01_plan_comparison"), agent_factory=MutatingToolAgent
    )
    assert len(outcome.run.tool_trace) == 1
    assert "tampered" not in [item.content for item in outcome.run.conversation]


def test_agent_cannot_rewrite_the_recorded_tool_trace() -> None:
    class TraceMutator:
        def __init__(self) -> None:
            self._step = 0

        def act(self, context: AgentContext):
            self._step += 1
            if self._step == 1:
                return CallToolAction(
                    tool_name="list_plans",
                    arguments={},
                )
            if context.tool_results:
                # Attempt to rewrite the recorded result through the observation.
                context.tool_results[-1].result.data["plans"] = []
            return FinishAction(reason="mutated")

    outcome = execute_task(
        find_task("d01_plan_comparison"), agent_factory=TraceMutator
    )
    recorded = outcome.run.tool_trace[0].result.data["plans"]
    assert recorded, "the recorded tool result must not be rewritten by the agent"


def test_agent_usage_is_propagated_into_the_outcome() -> None:
    class UsageAgent(BenchmarkDemoAgent):
        def usage_snapshot(self) -> dict[str, int]:
            return {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    outcome = execute_task(
        find_task("d01_plan_comparison"), agent_factory=UsageAgent
    )
    assert outcome.token_usage["prompt_tokens"] == 11
    assert outcome.token_usage["total_tokens"] == 18
    assert outcome.run.agent_usage["completion_tokens"] == 7
