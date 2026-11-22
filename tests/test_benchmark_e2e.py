from __future__ import annotations

import json

import pytest

from cm_outbound_bench.agent import AgentContext, FinishAction, SayAction
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.metrics import aggregate, build_manifest
from cm_outbound_bench.benchmark.versions import EVALUATOR_VERSION
from cm_outbound_bench.evaluator import evaluate_benchmark_task
from cm_outbound_bench.runner import (
    RunResult,
    StopReason,
)


def test_d02_vertical_slice_end_to_end() -> None:
    task = find_task("d02_offer_benefit_inquiry_accept")
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)

    evaluation = outcome.evaluation
    assert evaluation.success, evaluation.summary
    assert evaluation.domain_id == "D02"
    assert evaluation.scenario_id == "D02-OFFER-001"

    # The evaluator inspected the real final database, not only the transcript.
    final_db = outcome.run.final_db
    assert len(final_db.enrollments) == 1
    enrollment = final_db.enrollments[0]
    assert enrollment.phone_number_id == "PN101"
    assert enrollment.offer_id == "OFFER_D02_001"
    assert len(final_db.consent_records) == 1
    assert enrollment.consent_id == final_db.consent_records[0].consent_id
    assert len(final_db.sms_records) == 1
    assert final_db.sms_records[0].enrollment_id == enrollment.enrollment_id

    # Consent was recorded by the runner before the enrollment tool ran.
    enroll_step = next(
        entry.step
        for entry in outcome.run.tool_trace
        if entry.tool_name == "enroll_offer" and entry.result.ok
    )
    assert outcome.run.consent_events
    assert outcome.run.consent_events[0].step < enroll_step

    # The SMS followed the enrollment.
    sms_step = next(
        entry.step
        for entry in outcome.run.tool_trace
        if entry.tool_name == "send_confirmation_sms" and entry.result.ok
    )
    assert enroll_step < sms_step

    # A machine-readable result exists for every run.
    assert evaluation.final_state_hash
    assert evaluation.metric_scores["policy_compliance"] == 1.0
    assert evaluation.completion_reason == "agent_finished"


def test_task_carries_evidence_references() -> None:
    task = find_task("d02_offer_benefit_inquiry_accept")
    assert task.evidence_refs
    for reference in task.evidence_refs:
        assert reference.transcript_id.startswith("CALL_")
        assert reference.note
        assert "text_masked" not in reference.note


def test_agent_context_never_contains_corpus_evidence() -> None:
    task = find_task("d02_offer_benefit_inquiry_accept")
    captured: list[str] = []

    class CapturingAgent(BenchmarkDemoAgent):
        def act(self, context: AgentContext):
            captured.append(context.model_dump_json())
            return super().act(context)

    execute_task(task, agent_factory=CapturingAgent)

    assert captured
    evidence_ids = {ref.transcript_id for ref in task.evidence_refs}
    for serialized in captured:
        for transcript_id in evidence_ids:
            assert transcript_id not in serialized
        assert "text_masked" not in serialized
        assert "evidence_refs" not in serialized
        assert "CALL_" not in serialized
        assert '"customer_id"' not in serialized


def test_every_packaged_task_passes_with_the_baseline_agent() -> None:
    failures: list[str] = []
    for task in all_tasks():
        outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
        if not outcome.evaluation.success:
            failed = [
                check.name
                for check in outcome.evaluation.checks
                if not check.passed
            ]
            failures.append(f"{task.task_id}: {failed}")
    assert failures == []


def test_d02_variants_reach_their_expected_outcomes() -> None:
    accept = execute_task(
        find_task("d02_offer_benefit_inquiry_accept"),
        agent_factory=BenchmarkDemoAgent,
    )
    decline = execute_task(
        find_task("d02_hesitation_then_decline"),
        agent_factory=BenchmarkDemoAgent,
    )
    ineligible = execute_task(
        find_task("d02_eligibility_ineligible_explanation"),
        agent_factory=BenchmarkDemoAgent,
    )
    refusal = execute_task(
        find_task("d02_direct_refusal_do_not_call"),
        agent_factory=BenchmarkDemoAgent,
    )

    assert len(accept.run.final_db.enrollments) == 1
    assert decline.run.final_db.enrollments == []
    assert decline.run.final_db.consent_records == []
    assert ineligible.run.final_db.enrollments == []
    assert len(refusal.run.final_db.do_not_call_records) == 1
    assert refusal.run.final_db.enrollments == []
    for outcome in (accept, decline, ineligible, refusal):
        assert outcome.evaluation.success, outcome.evaluation.summary


def test_state_is_isolated_between_episodes() -> None:
    task = find_task("d02_offer_benefit_inquiry_accept")
    first = execute_task(task, agent_factory=BenchmarkDemoAgent)
    second = execute_task(task, agent_factory=BenchmarkDemoAgent)

    assert first.state.state_hash == second.state.state_hash
    assert len(first.run.final_db.enrollments) == 1
    assert len(second.run.final_db.enrollments) == 1
    assert first.run.final_db.enrollments[0].enrollment_id == (
        second.run.final_db.enrollments[0].enrollment_id
    )
    assert first.state.db.enrollments == []
    assert second.state.db.enrollments == []


def test_idle_agent_fails_the_task() -> None:
    class IdleAgent:
        def act(self, context: AgentContext):
            return FinishAction(reason="nothing to do")

    outcome = execute_task(
        find_task("d02_offer_benefit_inquiry_accept"),
        agent_factory=IdleAgent,
    )
    assert not outcome.evaluation.success
    failed = {check.name for check in outcome.evaluation.checks if not check.passed}
    assert "required_tools" in failed
    assert any(name.startswith("expected_state_") for name in failed)


def test_evaluator_rejects_enrollment_without_runner_consent() -> None:
    task = find_task("d02_offer_benefit_inquiry_accept")
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    assert len(outcome.run.final_db.enrollments) == 1

    # Strip the runner consent events while keeping the final state: the
    # enrollment then has no recorded consent provenance.
    forged = RunResult(
        task_id=task.task_id,
        stop_reason=StopReason.AGENT_FINISHED,
        steps_executed=outcome.run.steps_executed,
        conversation=outcome.run.conversation,
        tool_trace=outcome.run.tool_trace,
        consent_events=[],
        final_db=outcome.run.final_db.deep_copy(),
    )

    evaluation = evaluate_benchmark_task(task, forged)

    assert not evaluation.success
    assert "enrollment_without_runner_consent" in evaluation.policy_violations


def test_evaluator_detects_disclosure_before_verification() -> None:
    task = find_task("d02_offer_benefit_inquiry_accept")

    class LeakyAgent:
        def __init__(self) -> None:
            self._done = False

        def act(self, context: AgentContext):
            if self._done:
                return FinishAction(reason="leaked")
            self._done = True
            return SayAction(message="我帮您确认一下，机主是周文博，这个号码是 SIM_LINE_0101。")

    outcome = execute_task(task, agent_factory=LeakyAgent, strict=False)

    assert not outcome.evaluation.success
    assert (
        "protected_information_disclosed_before_verification"
        in outcome.evaluation.policy_violations
    )


def test_manifest_records_reproducible_identity() -> None:
    task = find_task("d04_plan_upgrade_with_consent")
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    manifest = build_manifest(outcome, agent_type="benchmark_demo")

    assert manifest.task_id == task.task_id
    assert manifest.scenario_id == task.scenario_id
    assert manifest.initial_state_id == task.initial_state_id
    assert manifest.initial_state_hash == outcome.state.state_hash
    assert manifest.task_success is True
    assert manifest.run_id.startswith("RUN-")
    # Both the task and the manifest must agree with the canonical evaluator
    # implementation, not merely with each other.
    assert manifest.evaluator_version == task.evaluator_version == EVALUATOR_VERSION
    payload = json.dumps(manifest.model_dump(mode="json"))
    assert "TEST_IDENTITY" not in payload
    assert "text_masked" not in payload


def test_aggregate_report_counts_every_manifest() -> None:
    manifests = []
    for task_id in ("d02_offer_benefit_inquiry_accept", "d02_hesitation_then_decline"):
        outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
        manifests.append(build_manifest(outcome, agent_type="benchmark_demo"))

    report = aggregate(manifests)

    assert report.manifests == 2
    assert report.task_success_rate == 1.0
    assert report.policy_violation_rate == 0.0
    assert report.distinct_agents == ["benchmark_demo"]
    assert {domain.domain_id for domain in report.domains} == {"D02"}
    assert all(task.runs == 1 for task in report.tasks)


def test_empty_aggregate_is_safe() -> None:
    report = aggregate([])
    assert report.manifests == 0
    assert report.task_success_rate == 0.0


@pytest.mark.parametrize(
    "task_id",
    [
        "d01_plan_content_explanation",
        "d03_bill_explanation",
        "d04_plan_change_blocked_by_commitment",
        "d05_real_name_not_verified",
        "d06_recharge_confirmation_notification",
    ],
)
def test_domain_tasks_have_an_observable_business_outcome(task_id: str) -> None:
    task = find_task(task_id)
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    assert outcome.evaluation.success
    assert outcome.evaluation.checks
    assert outcome.evaluation.final_state_hash
