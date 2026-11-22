import json

import pytest

from cm_outbound_bench.agent import (
    AgentContext,
    CallToolAction,
    DemoAgent,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.domain.data_model import (
    CmOutboundDB,
    ConsentRecord,
    Enrollment,
)
from cm_outbound_bench.environment import CmOutboundEnvironment
from cm_outbound_bench.evaluator import evaluate_task
from cm_outbound_bench.runner import (
    MarketingRunner,
    RunResult,
    StopReason,
)
from cm_outbound_bench.tasks import MarketingTask, find_task


def _run_task(
    task: MarketingTask,
    database: CmOutboundDB,
    policy: str,
    *,
    max_steps: int = 24,
    agent_factory=DemoAgent,
):
    runner = MarketingRunner(
        initial_db=database,
        policy=policy,
        agent_factory=agent_factory,
        max_steps=max_steps,
    )
    run = runner.run(task)
    return run, evaluate_task(task, run)


@pytest.mark.parametrize(
    ("task_id", "expected_dnc", "expected_enrollment", "expected_verification"),
    [
        ("marketing_direct_refusal", True, False, False),
        ("marketing_price_inquiry", False, False, True),
        ("marketing_not_owner", False, False, False),
        ("marketing_scam_suspicion", False, False, True),
        ("marketing_hesitant_acceptance", False, True, True),
    ],
)
def test_demo_agent_reaches_expected_outcome(
    task_id,
    expected_dnc,
    expected_enrollment,
    expected_verification,
    database,
    policy,
    marketing_tasks,
):
    task = find_task(task_id, marketing_tasks)
    run, evaluation = _run_task(task, database, policy)

    assert evaluation.success, evaluation.summary
    assert len(run.final_db.do_not_call_records) == int(expected_dnc)
    assert len(run.final_db.enrollments) == int(expected_enrollment)
    assert len(run.final_db.sms_records) == int(expected_enrollment)
    assert len(run.final_db.consent_records) == int(expected_enrollment)
    verified = any(
        entry.tool_name == "verify_customer_identity" and entry.result.ok
        for entry in run.tool_trace
    )
    assert verified is expected_verification
    for check in evaluation.checks:
        assert check.passed, check.name


def test_hesitant_acceptance_orders_consent_eligibility_and_sms(
    database, policy, marketing_tasks
):
    task = find_task("marketing_hesitant_acceptance", marketing_tasks)
    run, evaluation = _run_task(task, database, policy)

    assert evaluation.success
    assert len(run.consent_events) == 1
    enrollment = run.final_db.enrollments[0]
    enroll_step = next(
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "enroll_offer" and entry.result.ok
    )
    eligibility_step = next(
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "check_offer_eligibility" and entry.result.ok
    )
    sms_step = next(
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "send_confirmation_sms" and entry.result.ok
    )

    assert run.consent_events[0].step < enroll_step
    assert eligibility_step < enroll_step
    assert enroll_step < sms_step
    assert run.final_db.sms_records[0].enrollment_id == enrollment.enrollment_id
    assert run.final_db.consent_records[0].consent_id == enrollment.consent_id


def test_db_state_is_reset_between_runs(database, policy, marketing_tasks):
    task = find_task("marketing_hesitant_acceptance", marketing_tasks)
    runner = MarketingRunner(
        initial_db=database,
        policy=policy,
        agent_factory=DemoAgent,
    )

    first = runner.run(task)
    second = runner.run(task)

    assert database.enrollments == []
    assert database.consent_records == []
    assert database.sms_records == []
    assert len(first.final_db.enrollments) == 1
    assert len(second.final_db.enrollments) == 1
    assert first.final_db.enrollments[0].enrollment_id == "ENR-0001"
    assert second.final_db.enrollments[0].enrollment_id == "ENR-0001"


def test_environment_reset_clears_db_and_verification(database, policy):
    env = CmOutboundEnvironment(database, policy)
    verified = env.execute_tool(
        "verify_customer_identity",
        {
            "phone_number_id": "PN001",
            "identity_token": "TEST_IDENTITY_C001",
        },
        step=1,
    )
    env.execute_tool(
        "register_do_not_call",
        {"phone_number_id": "PN001"},
        step=2,
    )

    assert verified.ok
    assert len(env.tool_trace) == 2
    env.reset()
    assert env.tool_trace == []
    assert env.db.do_not_call_records == []

    blocked = env.execute_tool(
        "get_customer_account",
        {"phone_number_id": "PN001"},
        step=1,
    )
    assert not blocked.ok
    assert blocked.error is not None
    assert blocked.error.code == "identity_verification_required"


def test_agent_context_excludes_simulator_private_fields(
    database, policy, marketing_tasks
):
    class CapturingAgent:
        def __init__(self):
            self.contexts: list[AgentContext] = []

        def act(self, context: AgentContext):
            self.contexts.append(context)
            return FinishAction(reason="capture")

    task = find_task("marketing_hesitant_acceptance", marketing_tasks)
    agent = CapturingAgent()
    runner = MarketingRunner(
        initial_db=database,
        policy=policy,
        agent_factory=lambda: agent,
    )
    runner.run(task)

    serialized = agent.contexts[0].model_dump_json()
    assert "TEST_IDENTITY_C002" not in serialized
    assert "hesitant_acceptance" not in serialized
    assert "response_behavior" not in serialized
    assert "may_accept_offer" not in serialized
    assert "may_give_consent" not in serialized
    assert '"customer_id":' not in serialized
    assert "C002" not in serialized
    assert set(task.agent_view().model_dump()) == {
        "domain",
        "objective",
        "phone_number_id",
        "offer_id",
    }


def test_max_step_limit_stops_infinite_agent(database, policy, marketing_tasks):
    class ChattyAgent:
        def act(self, context: AgentContext):
            return SayAction(message="我还在确认，请稍等。")

    task = find_task("marketing_direct_refusal", marketing_tasks)
    run, evaluation = _run_task(
        task,
        database,
        policy,
        max_steps=3,
        agent_factory=ChattyAgent,
    )

    assert run.stop_reason is StopReason.STEP_LIMIT
    assert run.steps_executed == 3
    assert not evaluation.success
    assert any(
        check.name == "agent_finished" and not check.passed
        for check in evaluation.checks
    )


def test_agent_receives_clean_tool_error(database, policy, marketing_tasks):
    class ErrorProbeAgent:
        error = None

        def act(self, context: AgentContext):
            if not context.tool_results:
                return CallToolAction(tool_name="missing_tool", arguments={})
            self.error = context.tool_results[-1].result.error
            return FinishAction(reason="observed error")

    task = find_task("marketing_direct_refusal", marketing_tasks)
    agent = ErrorProbeAgent()
    runner = MarketingRunner(
        initial_db=database,
        policy=policy,
        agent_factory=lambda: agent,
    )
    runner.run(task)

    assert agent.error is not None
    assert agent.error.code == "unknown_tool"
    json.dumps(agent.error.model_dump(mode="json"))


def test_evaluator_rejects_enrollment_without_runner_consent(database, marketing_tasks):
    task = find_task("marketing_hesitant_acceptance", marketing_tasks)
    data = database.model_dump(mode="json")
    data["consent_records"].append(
        ConsentRecord(
            consent_id="CONSENT-0001",
            customer_id="C002",
            phone_number_id="PN003",
            offer_id="OFFER_DATA_001",
        ).model_dump(mode="json")
    )
    data["enrollments"].append(
        Enrollment(
            enrollment_id="ENR-0001",
            customer_id="C002",
            phone_number_id="PN003",
            offer_id="OFFER_DATA_001",
            consent_id="CONSENT-0001",
        ).model_dump(mode="json")
    )
    final_db = CmOutboundDB.model_validate(data)
    forged_run = RunResult(
        task_id=task.task_id,
        stop_reason=StopReason.AGENT_FINISHED,
        steps_executed=0,
        conversation=[],
        tool_trace=[],
        consent_events=[],
        final_db=final_db,
    )

    evaluation = evaluate_task(task, forged_run)

    assert not evaluation.success
    assert "consent_not_recorded_by_simulator" in evaluation.policy_violations
    assert "enrollment_without_runner_consent" in evaluation.policy_violations
    assert "enrollment_without_eligibility_check" in evaluation.policy_violations
