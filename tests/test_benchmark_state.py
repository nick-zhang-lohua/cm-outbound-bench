from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cm_outbound_bench.benchmark.state import (
    InitialState,
    StateRecord,
    hash_database,
    validate_state_for_line,
)
from cm_outbound_bench.domain.data_model import (
    CmOutboundDB,
    DoNotCallRecord,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def state() -> InitialState:
    db = CmOutboundDB.load(PROJECT_ROOT / "data" / "synthetic" / "db.json")
    return InitialState.from_database("STATE-TEST-001", db, description="test state")


def test_state_hash_is_stable(state: InitialState) -> None:
    assert state.state_hash == hash_database(state.db)
    again = InitialState(state.to_record())
    assert again.state_hash == state.state_hash
    assert again.state_id == state.state_id


def test_state_hash_changes_with_content(state: InitialState) -> None:
    mutated = state.db.deep_copy()
    mutated.plans[0].monthly_fee += 1.0
    assert hash_database(mutated) != state.state_hash


def test_instantiate_returns_independent_copies(state: InitialState) -> None:
    first = state.instantiate()
    second = state.instantiate()
    first.do_not_call_records.append(
        DoNotCallRecord(
            record_id="DNC-TEST",
            customer_id="C001",
            phone_number_id="PN001",
        )
    )
    assert second.do_not_call_records == []
    assert state.db.do_not_call_records == []


def test_state_record_rejects_unknown_references() -> None:
    with pytest.raises(ValidationError):
        StateRecord(
            state_id="STATE-BAD",
            db=CmOutboundDB.model_validate(
                {
                    "customers": [
                        {
                            "customer_id": "C001",
                            "owner_name": "测试",
                            "identity_token": "TEST_IDENTITY_C001",
                        }
                    ],
                    "phone_numbers": [
                        {
                            "phone_number_id": "PN001",
                            "phone_number": "SIM_LINE_0001",
                            "owner_customer_id": "C999",
                            "current_plan_id": "PLAN_X",
                        }
                    ],
                }
            ),
        )


def test_validate_state_for_line_reports_missing_entities(
    state: InitialState,
) -> None:
    problems = validate_state_for_line(state, "PN001")
    assert "subscription_missing" in problems
    assert "identity_record_missing" in problems


def test_validate_state_for_line_reports_missing_line(state: InitialState) -> None:
    assert validate_state_for_line(state, "PN-MISSING") == ["target_line_missing"]


#: Initial-state payloads that are deliberately identical. STATE-D01-001 and
#: STATE-D01-002 describe the same customer situation (PN201 on PLAN_S) used by
#: two different D01 scenarios, so they share one payload under two identifiers.
#: Pinning the set here means any *new* accidental duplicate fails the suite.
KNOWN_DUPLICATE_STATE_HASHES = {
    frozenset({"STATE-D01-001", "STATE-D01-002"}),
}


def test_initial_state_payload_duplicates_are_known_and_explicit() -> None:
    from cm_outbound_bench.benchmark.domains import domain_ids, states_for_domain

    by_hash: dict[str, set[str]] = {}
    for domain_id in domain_ids():
        for state in states_for_domain(domain_id).list_states():
            by_hash.setdefault(state.state_hash, set()).add(state.state_id)
    duplicates = {frozenset(ids) for ids in by_hash.values() if len(ids) > 1}
    assert duplicates == KNOWN_DUPLICATE_STATE_HASHES


def test_every_initial_state_is_addressed_by_at_least_one_task() -> None:
    from cm_outbound_bench.benchmark.domains import (
        all_tasks,
        domain_ids,
        states_for_domain,
    )

    used = {task.initial_state_id for task in all_tasks()}
    declared = {
        state.state_id
        for domain_id in domain_ids()
        for state in states_for_domain(domain_id).list_states()
    }
    assert declared == used
