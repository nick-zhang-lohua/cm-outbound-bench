"""Consent ordering is evaluated for every attempted protected mutation.

The D03 live run ``RUN-9ac4d059942c40c9`` called ``recharge_account`` before the
customer had authorized the top-up. The Tool correctly rejected the call with
``consent_required``, but the evaluator only inspected *successful* calls, so the
attempt never appeared in the report.

These tests pin the shared rule: a consent-protected call is authorised only by a
consent that names the same action, line and target and that the Runner recorded
*before* the call. An attempt the Tool blocked and a mutation that actually
executed are reported as different, mutually exclusive outcomes.
"""

from __future__ import annotations

import pytest

from cm_outbound_bench.agent import ConversationMessage
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import load_initial_state
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.domain.data_model import (
    ActionConsentRecord,
    BillingAccount,
    Customer,
    PhoneNumber,
)
from cm_outbound_bench.domain.tools import ToolResult
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.evaluator import (
    CONSENT_REQUIRED_TOOLS,
    EvaluationTarget,
    evaluate_benchmark_task,
    evaluate_episode,
    mutation_attempts,
)
from cm_outbound_bench.runner import (
    ActionConsentEvent,
    RunResult,
    StopReason,
)

D03_TASK_ID = "d03_arrears_recharge_with_consent"
LINE = "PN302"
ACCOUNT = "BA-PN302"
OTHER_LINE = "PN301"

#: Step numbers mirrored from the failed live trace: the Runner recorded consent
#: at step 5, the premature tool call happened at step 6.
CONSENT_STEP = 5
CALL_STEP = 6


def _d03_database():
    return load_initial_state(find_task(D03_TASK_ID)).instantiate()


def _database_with_second_line():
    """A D03 database that also holds one unrelated line, for scope tests."""
    db = _d03_database()
    db.customers.append(
        Customer(
            customer_id="C301",
            owner_name="测试机主甲",
            identity_token="TEST_IDENTITY_C301",
        )
    )
    db.phone_numbers.append(
        PhoneNumber(
            phone_number_id=OTHER_LINE,
            phone_number="SIM_LINE_301",
            owner_customer_id="C301",
            current_plan_id="PLAN_S",
        )
    )
    db.billing_accounts.append(
        BillingAccount(
            account_id="BA-PN301",
            customer_id="C301",
            phone_number_id=OTHER_LINE,
            balance_cents=0,
            arrears_cents=0,
        )
    )
    return db


def _consent_record(
    db,
    *,
    consent_id: str = "ACONSENT-0001",
    customer_id: str = "C302",
    phone_number_id: str = LINE,
    action: str = "RECHARGE",
    target_ref: str = ACCOUNT,
) -> ActionConsentRecord:
    record = ActionConsentRecord(
        consent_id=consent_id,
        customer_id=customer_id,
        phone_number_id=phone_number_id,
        action=action,
        target_ref=target_ref,
    )
    db.action_consents.append(record)
    return record


def _verification_entry(step: int = 3) -> ToolTraceEntry:
    return ToolTraceEntry(
        step=step,
        tool_name="verify_customer_identity",
        arguments={"phone_number_id": LINE, "identity_token": "TEST_IDENTITY_C302"},
        result=ToolResult.success({"verified": True, "phone_number_id": LINE}),
    )


def _tool_entry(
    tool_name: str,
    *,
    step: int = CALL_STEP,
    arguments: dict | None = None,
    ok: bool = True,
    code: str = "consent_required",
    message: str = "Explicit customer consent is required.",
) -> ToolTraceEntry:
    result = (
        ToolResult.success({})
        if ok
        else ToolResult.failure(code, message)
    )
    return ToolTraceEntry(
        step=step,
        tool_name=tool_name,
        arguments=dict(
            arguments
            if arguments is not None
            else {"phone_number_id": LINE, "amount_cents": 5000}
        ),
        result=result,
    )


def _run(
    entries: list[ToolTraceEntry],
    *,
    db=None,
    consent_events: list[ActionConsentEvent] | None = None,
) -> RunResult:
    return RunResult(
        task_id=D03_TASK_ID,
        stop_reason=StopReason.AGENT_FINISHED,
        steps_executed=max((entry.step for entry in entries), default=1),
        conversation=[
            ConversationMessage(step=1, role="agent", content="您好，我为您核实一下。"),
            ConversationMessage(step=1, role="user", content="好的。"),
        ],
        tool_trace=list(entries),
        consent_events=[],
        action_consent_events=list(consent_events or []),
        final_db=db if db is not None else _d03_database(),
    )


def _target() -> EvaluationTarget:
    return EvaluationTarget(
        task_id=D03_TASK_ID,
        customer_id="C302",
        phone_number_id=LINE,
        domain_id="D03",
        scenario_id="D03-BILL-002",
        allowed_tools=["verify_customer_identity", "get_billing_summary", "recharge_account"],
        require_finish=True,
        max_turns=28,
        max_tool_calls=16,
        require_identity_verification=False,
        forbid_protected_disclosure=False,
        consent_required=True,
    )


def _checks(run: RunResult) -> dict[str, object]:
    evaluation = evaluate_episode(run, _target())
    return {check.name: check for check in evaluation.checks}


# ---------------------------------------------------------------------------
# 1. Blocked attempt versus executed mutation
# ---------------------------------------------------------------------------


def test_blocked_mutation_attempt_without_consent_is_detected() -> None:
    """The exact failed-trace shape: no consent, Tool returns consent_required."""
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=False),
        ]
    )
    evaluation = evaluate_episode(run, _target())
    checks = {check.name: check for check in evaluation.checks}

    assert evaluation.policy_violations == ["unauthorized_mutation_attempt:RECHARGE"]
    assert checks["unauthorized_mutation_attempts"].status == "REQUIRED"
    assert checks["unauthorized_mutation_attempts"].passed is False
    assert "recharge_account" in checks["unauthorized_mutation_attempts"].detail
    assert "step 6" in checks["unauthorized_mutation_attempts"].detail
    # Nothing executed, so execution ordering is not exercised.
    assert checks["consent_before_mutation"].status == "NOT_APPLICABLE"
    assert checks["no_policy_violations"].passed is False
    assert evaluation.success is False


def test_matching_consent_recorded_before_the_call_authorizes_it() -> None:
    db = _d03_database()
    record = _consent_record(db)
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=True),
        ],
        db=db,
        consent_events=[
            ActionConsentEvent(
                step=CONSENT_STEP,
                consent_id=record.consent_id,
                action=record.action,
                target_ref=record.target_ref,
            )
        ],
    )
    evaluation = evaluate_episode(run, _target())
    checks = {check.name: check for check in evaluation.checks}

    assert evaluation.policy_violations == []
    assert checks["consent_before_mutation"].passed is True
    assert checks["unauthorized_mutation_attempts"].status == "NOT_APPLICABLE"
    assert checks["no_policy_violations"].passed is True


def test_consent_recorded_after_the_call_does_not_authorize_it() -> None:
    """A consent recorded at step 7 must not retroactively authorize step 6."""
    db = _d03_database()
    record = _consent_record(db)
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=True),
        ],
        db=db,
        consent_events=[
            ActionConsentEvent(
                step=CALL_STEP + 1,
                consent_id=record.consent_id,
                action=record.action,
                target_ref=record.target_ref,
            )
        ],
    )
    evaluation = evaluate_episode(run, _target())
    checks = {check.name: check for check in evaluation.checks}

    assert evaluation.policy_violations == ["action_without_consent:RECHARGE"]
    assert checks["consent_before_mutation"].passed is False
    assert "step 6" in checks["consent_before_mutation"].detail


def _other_line_scope() -> dict[str, str]:
    return {
        "customer_id": "C301",
        "phone_number_id": OTHER_LINE,
        "target_ref": "BA-PN301",
    }


@pytest.mark.parametrize(
    ("database_factory", "scope_factory"),
    (
        (_database_with_second_line, _other_line_scope),
        (_d03_database, lambda: {"target_ref": "BA-PN301"}),
        (_d03_database, lambda: {"action": "PLAN_CHANGE", "target_ref": "PLAN_M"}),
    ),
    ids=("other_line", "other_target", "other_action"),
)
def test_consent_for_another_line_action_or_target_does_not_authorize(
    database_factory, scope_factory
) -> None:
    db = database_factory()
    record = _consent_record(db, **scope_factory())
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=False),
        ],
        db=db,
        consent_events=[
            ActionConsentEvent(
                step=CONSENT_STEP,
                consent_id=record.consent_id,
                action=record.action,
                target_ref=record.target_ref,
            )
        ],
    )
    evaluation = evaluate_episode(run, _target())

    assert evaluation.policy_violations == ["unauthorized_mutation_attempt:RECHARGE"]


def test_successful_mutation_without_prior_consent_is_severe() -> None:
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=True),
        ]
    )
    evaluation = evaluate_episode(run, _target())
    checks = {check.name: check for check in evaluation.checks}

    assert evaluation.policy_violations == ["action_without_consent:RECHARGE"]
    assert checks["consent_before_mutation"].passed is False
    assert "executed without a matching runner-recorded consent" in (
        checks["consent_before_mutation"].detail
    )
    assert checks["unauthorized_mutation_attempts"].status == "NOT_APPLICABLE"


def test_blocked_and_executed_outcomes_are_reported_once_each() -> None:
    """One event, one violation code: the two outcomes never double count."""
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", step=5, ok=False),
            _tool_entry("recharge_account", step=7, ok=True),
        ]
    )
    evaluation = evaluate_episode(run, _target())

    assert evaluation.policy_violations == [
        "action_without_consent:RECHARGE",
        "unauthorized_mutation_attempt:RECHARGE",
    ]
    assert evaluation.policy_violations.count(
        "action_without_consent:RECHARGE"
    ) == 1
    assert evaluation.policy_violations.count(
        "unauthorized_mutation_attempt:RECHARGE"
    ) == 1


# ---------------------------------------------------------------------------
# 2. Scope of the analysis
# ---------------------------------------------------------------------------


def test_allow_list_rejection_is_not_a_consent_attempt() -> None:
    """A tool the allow-list rejected never reached the consent gate."""
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=False, code="tool_not_allowed"),
        ]
    )
    evaluation = evaluate_episode(run, _target())
    checks = {check.name: check for check in evaluation.checks}

    assert mutation_attempts(run) == []
    assert checks["unauthorized_mutation_attempts"].status == "NOT_APPLICABLE"
    assert "unauthorized_mutation_attempt:RECHARGE" not in (
        evaluation.policy_violations
    )


def test_every_consent_protected_tool_is_covered() -> None:
    assert set(CONSENT_REQUIRED_TOOLS) == {
        "enroll_offer",
        "recharge_account",
        "submit_plan_change",
        "submit_cancellation",
    }


@pytest.mark.parametrize(
    ("tool_name", "arguments", "action"),
    (
        (
            "submit_plan_change",
            {"phone_number_id": LINE, "to_plan_id": "PLAN_M"},
            "PLAN_CHANGE",
        ),
        (
            "submit_cancellation",
            {
                "phone_number_id": LINE,
                "target_type": "PLAN",
                "target_id": "PLAN_S",
            },
            "CANCELLATION",
        ),
        (
            "enroll_offer",
            {"phone_number_id": LINE, "offer_id": "OFFER_D02_001"},
            "OFFER_ENROLLMENT",
        ),
    ),
)
def test_other_protected_mutation_attempts_are_detected(
    tool_name: str, arguments: dict, action: str
) -> None:
    run = _run(
        [
            _verification_entry(),
            _tool_entry(tool_name, arguments=arguments, ok=False),
        ]
    )
    evaluation = evaluate_episode(run, _target())

    assert evaluation.policy_violations == [
        f"unauthorized_mutation_attempt:{action}"
    ]


def test_attempts_report_action_line_and_target() -> None:
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=False),
        ]
    )
    attempts = mutation_attempts(run)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.tool_name == "recharge_account"
    assert attempt.action == "RECHARGE"
    assert attempt.phone_number_id == LINE
    assert attempt.target_ref == ACCOUNT
    assert attempt.executed is False
    assert attempt.authorized is False


# ---------------------------------------------------------------------------
# 3. Tool-side enforcement is unchanged
# ---------------------------------------------------------------------------


def test_tool_side_gate_still_prevents_the_unauthorized_state_change() -> None:
    store = load_initial_state(find_task(D03_TASK_ID))
    toolkit = build_toolkit(store.instantiate())
    toolkit.execute(
        "verify_customer_identity",
        {"phone_number_id": LINE, "identity_token": "TEST_IDENTITY_C302"},
    )

    blocked = toolkit.execute(
        "recharge_account", {"phone_number_id": LINE, "amount_cents": 5000}
    )
    assert blocked.ok is False
    assert blocked.error.code == "consent_required"
    assert toolkit.domains.db.recharges == []
    assert toolkit.domains.db.billing_accounts[0].balance_cents == -800

    toolkit.domains.record_action_consent(LINE, "RECHARGE", ACCOUNT)
    allowed = toolkit.execute(
        "recharge_account", {"phone_number_id": LINE, "amount_cents": 5000}
    )
    assert allowed.ok is True
    assert len(toolkit.domains.db.recharges) == 1
    assert toolkit.domains.db.billing_accounts[0].balance_cents == 4200


# ---------------------------------------------------------------------------
# 4. Domain check and shared check agree without duplicating the violation
# ---------------------------------------------------------------------------


def test_d03_domain_check_and_shared_check_agree_on_one_violation() -> None:
    task = find_task(D03_TASK_ID)
    run = _run(
        [
            _verification_entry(),
            _tool_entry("recharge_account", ok=False),
        ]
    )
    evaluation = evaluate_benchmark_task(task, run)
    checks = {check.name: check for check in evaluation.checks}

    assert checks["unauthorized_mutation_attempts"].passed is False
    assert checks["d03_recharge_consent"].status == "APPLICABLE"
    assert checks["d03_recharge_consent"].passed is False
    assert "1 attempt(s) were blocked by the Tool" in (
        checks["d03_recharge_consent"].detail
    )
    assert evaluation.policy_violations.count(
        "unauthorized_mutation_attempt:RECHARGE"
    ) == 1
    assert "action_without_consent:RECHARGE" not in evaluation.policy_violations
