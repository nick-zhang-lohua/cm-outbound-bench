from __future__ import annotations

import pytest
from pydantic import ValidationError

from cm_outbound_bench.benchmark.domains import (
    all_tasks,
    get_domain,
    policy_for_domain,
    tasks_for_domain,
)
from cm_outbound_bench.benchmark.execution import (
    validate_catalog,
    validate_state_identity,
    validate_task_identity,
)
from cm_outbound_bench.benchmark.state import StateStore
from cm_outbound_bench.benchmark.tasks import (
    BenchmarkTask,
    BenchmarkTaskFile,
    EvidenceRef,
    ForbiddenOutcome,
    StateExpectation,
    load_task_file,
)
from cm_outbound_bench.tasks import AgentTaskView


def test_catalog_is_internally_consistent() -> None:
    assert validate_catalog() == []


def test_every_domain_has_tasks_and_a_policy() -> None:
    for domain_id in ("D01", "D02", "D03", "D04", "D05", "D06"):
        spec = get_domain(domain_id)
        tasks = tasks_for_domain(domain_id)
        assert tasks, f"{domain_id} has no tasks"
        assert all(task.domain_id == domain_id for task in tasks)
        policy = policy_for_domain(domain_id)
        assert "identity" in policy.lower()
        assert spec.name


def test_every_task_has_evidence_and_an_initial_state() -> None:
    for task in all_tasks():
        assert task.evidence_refs, task.task_id
        assert task.initial_state_id.startswith("STATE-")
        for reference in task.evidence_refs:
            assert reference.transcript_id.startswith("CALL_")
            assert reference.provenance_status in {
                "OBSERVED_PATTERN",
                "HUMAN_REVIEWED_PATTERN",
                "AUTHORITATIVE_POLICY_VERIFIED",
                "SYNTHETIC_BENCHMARK_ASSUMPTION",
                "SAFETY_STRESS_SCENARIO",
            }


def test_task_files_reference_existing_states() -> None:
    for task in all_tasks():
        store = StateStore.from_file(_state_path(task.domain_id))
        assert store.has(task.initial_state_id), task.task_id


def _state_path(domain_id: str):
    from cm_outbound_bench.paths import data_path

    return data_path("synthetic", "states", get_domain(domain_id).state_file)


def test_task_requires_at_least_one_evidence_reference() -> None:
    task = tasks_for_domain("D02")[0]
    payload = task.model_dump(mode="json")
    payload["evidence_refs"] = []
    with pytest.raises(ValidationError):
        BenchmarkTask.model_validate(payload)


def test_required_tools_must_be_allowed() -> None:
    task = tasks_for_domain("D02")[0]
    payload = task.model_dump(mode="json")
    payload["required_tools"] = ["cancel_subscription_not_a_tool"]
    with pytest.raises(ValidationError):
        BenchmarkTask.model_validate(payload)


def test_offer_tools_require_an_offer_id() -> None:
    task = tasks_for_domain("D02")[0]
    payload = task.model_dump(mode="json")
    payload["offer_id"] = None
    with pytest.raises(ValidationError):
        BenchmarkTask.model_validate(payload)


def test_only_one_consent_step_is_allowed() -> None:
    task = tasks_for_domain("D02")[0]
    payload = task.model_dump(mode="json")
    consent_steps = [
        step for step in payload["simulator"]["steps"]
        if step.get("consent_action") is not None
    ]
    assert len(consent_steps) == 1
    payload["simulator"]["steps"].append(consent_steps[0])
    with pytest.raises(ValidationError):
        BenchmarkTask.model_validate(payload)


def test_task_file_rejects_duplicate_task_ids() -> None:
    task = tasks_for_domain("D02")[0]
    payload = {
        "domain_id": "D02",
        "tasks": [
            task.model_dump(mode="json"),
            task.model_dump(mode="json"),
        ],
    }
    with pytest.raises(ValidationError):
        BenchmarkTaskFile.model_validate(payload)


def test_task_file_rejects_domain_mismatch(tmp_path) -> None:
    task = tasks_for_domain("D02")[0]
    payload = task.model_dump(mode="json")
    payload["domain_id"] = "D01"
    path = tmp_path / "mismatch.json"
    path.write_text(
        '{"domain_id": "D02", "tasks": [' + _dumps(payload) + "]}",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_task_file(path, expected_domain="D02")


def _dumps(payload) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def test_agent_view_hides_simulator_and_evaluator_fields() -> None:
    for task in all_tasks():
        view = task.agent_view(task.domain_id.lower())
        serialized = view.model_dump_json()
        assert "evidence" not in serialized
        assert "CALL_" not in serialized
        assert "simulator" not in serialized
        assert "TEST_IDENTITY" not in serialized
        assert task.customer_id not in serialized


def test_evidence_reference_validates_sequence_range() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef(
            evidence_id="EV-X",
            transcript_id="CALL_x",
            seq_start=10,
            seq_end=5,
            pattern_type="test",
            provenance_status="OBSERVED_PATTERN",
        )


def test_state_expectation_rejects_unknown_collection() -> None:
    with pytest.raises(ValidationError):
        StateExpectation(collection="not_a_collection")


def test_forbidden_outcome_rejects_unknown_collection() -> None:
    with pytest.raises(ValidationError):
        ForbiddenOutcome(collection="not_a_collection")


def test_forbidden_outcome_supports_the_full_collection_vocabulary() -> None:
    """A negative expectation is as expressive as a positive one.

    The previous narrow vocabulary could not forbid a consent record, an action
    consent or a charge line. Widening it is a deliberate validation change, not
    a weakened assertion.
    """
    for collection in (
        "consent_records",
        "action_consents",
        "charges",
        "enrollments",
        "notifications",
    ):
        forbidden = ForbiddenOutcome(
            collection=collection, match={}, description="test"
        )
        assert forbidden.collection == collection


def test_state_expectation_rejects_unknown_match_field() -> None:
    with pytest.raises(ValidationError):
        StateExpectation(
            collection="enrollments",
            match={"no_such_field": "PN101"},
            expected_count=1,
        )


def test_forbidden_outcome_rejects_unknown_match_field() -> None:
    with pytest.raises(ValidationError):
        ForbiddenOutcome(
            collection="consent_records",
            match={"no_such_field": "PN101"},
        )


def test_state_expectation_rejects_ill_typed_value() -> None:
    with pytest.raises(ValidationError):
        StateExpectation(
            collection="recharges",
            match={"amount_cents": "5000"},
            expected_count=1,
        )


def test_state_expectation_accepts_enum_and_literal_values() -> None:
    expectation = StateExpectation(
        collection="plan_change_requests",
        match={"status": "APPROVED", "to_plan_id": "PLAN_L"},
        expected_count=1,
    )
    assert expectation.match["status"] == "APPROVED"


def test_validate_catalog_rejects_cross_file_duplicate_task_id() -> None:
    tasks = all_tasks()
    duplicated = [tasks[0], tasks[0]]
    problems = validate_task_identity(duplicated)
    assert any(problem.startswith("duplicate_task_id:") for problem in problems)


def test_validate_catalog_rejects_duplicate_state_id() -> None:
    problems = validate_state_identity([("STATE-X", "D01"), ("STATE-X", "D02")])
    assert any(problem.startswith("duplicate_state_id:") for problem in problems)


def test_catalog_identifiers_are_globally_unique() -> None:
    from cm_outbound_bench.benchmark.execution import validate_state_identity

    tasks = all_tasks()
    assert validate_task_identity(tasks) == []
    from cm_outbound_bench.benchmark.domains import domain_ids, states_for_domain

    state_ids = [
        (state.state_id, domain_id)
        for domain_id in domain_ids()
        for state in states_for_domain(domain_id).list_states()
    ]
    assert validate_state_identity(state_ids) == []


def test_every_task_declares_candidate_status() -> None:
    """Technical executability is separate from semantic review status."""
    for task in all_tasks():
        assert task.scenario_status == "CANDIDATE", task.task_id


def test_answer_assertions_are_never_visible_to_the_agent() -> None:
    """The answer keys must not leak into the agent-visible task view.

    Business values that the customer states or a tool reveals legitimately appear
    in the objective (for example the amount the customer asked to top up). What
    must never appear are the assertion identifiers and the forbidden (false)
    statements the evaluator looks for.
    """
    assert "answer_assertions" not in AgentTaskView.model_fields
    for task in all_tasks():
        view = task.agent_view(task.domain_id.lower())
        payload = view.model_dump_json()
        assertions = task.answer_assertions
        if assertions is None:
            continue
        for fact in (*assertions.required_facts, *assertions.forbidden_facts):
            assert fact.fact_id not in payload, task.task_id
        for fact in assertions.forbidden_facts:
            for pattern in fact.forbidden_patterns:
                assert pattern not in payload, task.task_id


def test_no_task_claims_unreviewed_human_review() -> None:
    """No scenario may claim human review that has not happened."""
    tasks = all_tasks()
    assert tasks
    for task in tasks:
        assert task.rule_provenance_status in {
            "OBSERVED_PATTERN",
            "SYNTHETIC_BENCHMARK_ASSUMPTION",
            "SAFETY_STRESS_SCENARIO",
        }, task.task_id
