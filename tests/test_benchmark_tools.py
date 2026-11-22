from __future__ import annotations

import pytest

from cm_outbound_bench.benchmark.state import StateStore
from cm_outbound_bench.benchmark.toolkits import build_toolkit
from cm_outbound_bench.paths import data_path


@pytest.fixture
def state():
    store = StateStore.from_file(data_path("synthetic", "states", "d04_lifecycle.json"))
    return store.get("STATE-D04-001")


@pytest.fixture
def toolkit(state):
    return build_toolkit(state.instantiate())


def _ok(result):
    assert result.ok, result.error
    return result


def _call(toolkit, name, **arguments):
    return toolkit.execute(name, arguments)


def _verify(toolkit, token="TEST_IDENTITY_C401", line="PN401"):
    return _ok(
        toolkit.execute(
            "verify_customer_identity",
            {"phone_number_id": line, "identity_token": token},
        )
    )


def test_composite_toolkit_keeps_legacy_tools_and_adds_domain_tools(toolkit):
    names = {definition.name for definition in toolkit.list_tools()}
    assert {
        "verify_customer_identity",
        "get_customer_account",
        "get_offer_details",
        "check_offer_eligibility",
        "enroll_offer",
        "send_confirmation_sms",
        "register_do_not_call",
    } <= names
    assert {
        "list_plans",
        "get_plan_details",
        "compare_plans",
        "get_billing_summary",
        "list_charges",
        "recharge_account",
        "get_subscription_status",
        "check_plan_change_eligibility",
        "submit_plan_change",
        "submit_cancellation",
        "get_identity_status",
        "check_ownership_transfer_conditions",
        "send_notification",
        "get_notification_status",
    } <= names


def test_unknown_tool_and_bad_arguments_are_structured(toolkit):
    unknown = _call(toolkit, "nope")
    assert not unknown.ok and unknown.error.code == "unknown_tool"

    invalid = _call(toolkit, "get_plan_details")
    assert not invalid.ok and invalid.error.code == "invalid_arguments"


def test_plan_tools_return_allowances(toolkit):
    plans = _ok(_call(toolkit, "list_plans")).data["plans"]
    assert len(plans) == 3
    plan_s = next(item for item in plans if item["plan_id"] == "PLAN_S")
    assert plan_s["allowance"]["data_mb"] == 5000

    detail = _ok(_call(toolkit, "get_plan_details", plan_id="PLAN_M")).data
    assert detail["monthly_fee"] == 59.0
    assert detail["allowance"]["voice_minutes"] == 300

    comparison = _ok(
        _call(toolkit, "compare_plans", plan_id_a="PLAN_M", plan_id_b="PLAN_L")
    ).data
    assert comparison["monthly_fee_delta"] == 40.0

    missing = _call(toolkit, "get_plan_details", plan_id="PLAN_X")
    assert not missing.ok and missing.error.code == "plan_not_found"


def test_account_scoped_tools_require_verification(toolkit):
    for name, arguments in (
        ("get_billing_summary", {"phone_number_id": "PN401"}),
        ("list_charges", {"phone_number_id": "PN401"}),
        ("get_subscription_status", {"phone_number_id": "PN401"}),
        (
            "check_plan_change_eligibility",
            {"phone_number_id": "PN401", "to_plan_id": "PLAN_L"},
        ),
        ("get_identity_status", {"phone_number_id": "PN401"}),
        ("check_ownership_transfer_conditions", {"phone_number_id": "PN401"}),
        (
            "send_notification",
            {"phone_number_id": "PN401", "kind": "INFO", "related_ref": "X"},
        ),
    ):
        result = _call(toolkit, name, **arguments)
        assert not result.ok, name
        assert result.error.code == "identity_verification_required", name


def test_plan_change_requires_consent(toolkit):
    _verify(toolkit)
    eligibility = _ok(
        _call(
            toolkit,
            "check_plan_change_eligibility",
            phone_number_id="PN401",
            to_plan_id="PLAN_L",
        )
    ).data
    assert eligibility["eligible"] is True

    blocked = _call(
        toolkit, "submit_plan_change", phone_number_id="PN401", to_plan_id="PLAN_L"
    )
    assert not blocked.ok
    assert blocked.error.code == "consent_required"

    consent = toolkit.record_action_consent("PN401", "PLAN_CHANGE", "PLAN_L")
    assert consent is not None
    changed = _ok(
        _call(
            toolkit, "submit_plan_change", phone_number_id="PN401", to_plan_id="PLAN_L"
        )
    ).data
    assert changed["to_plan_id"] == "PLAN_L"
    assert changed["status"] == "APPROVED"

    subscription = _ok(
        _call(toolkit, "get_subscription_status", phone_number_id="PN401")
    ).data
    assert subscription["plan_id"] == "PLAN_L"


def test_plan_change_blocked_by_commitment():
    store = StateStore.from_file(
        data_path("synthetic", "states", "d04_lifecycle.json")
    )
    toolkit = build_toolkit(store.get("STATE-D04-002").instantiate())
    _verify(toolkit, token="TEST_IDENTITY_C402", line="PN402")
    toolkit.record_action_consent("PN402", "PLAN_CHANGE", "PLAN_M")

    result = _call(
        toolkit, "submit_plan_change", phone_number_id="PN402", to_plan_id="PLAN_M"
    )
    assert not result.ok
    assert result.error.code == "plan_change_not_allowed"
    assert "active_commitment" in result.error.message


def test_recharge_requires_consent_and_updates_balance():
    store = StateStore.from_file(data_path("synthetic", "states", "d03_billing.json"))
    toolkit = build_toolkit(store.get("STATE-D03-002").instantiate())
    _verify(toolkit, token="TEST_IDENTITY_C302", line="PN302")

    summary = _ok(
        _call(toolkit, "get_billing_summary", phone_number_id="PN302")
    ).data
    assert summary["balance_cents"] == -800
    assert summary["arrears_cents"] == 800

    blocked = _call(
        toolkit, "recharge_account", phone_number_id="PN302", amount_cents=5000
    )
    assert not blocked.ok
    assert blocked.error.code == "consent_required"

    toolkit.record_action_consent("PN302", "RECHARGE", "BA-PN302")
    recharged = _ok(
        _call(toolkit, "recharge_account", phone_number_id="PN302", amount_cents=5000)
    ).data
    assert recharged["balance_cents"] == 4200

    after = _ok(_call(toolkit, "get_billing_summary", phone_number_id="PN302")).data
    assert after["balance_cents"] == 4200


def test_recharge_rejects_non_positive_amount(toolkit):
    _verify(toolkit)
    result = _call(
        toolkit, "recharge_account", phone_number_id="PN401", amount_cents=0
    )
    assert not result.ok
    assert result.error.code == "invalid_arguments"


def test_identity_and_transfer_conditions():
    store = StateStore.from_file(
        data_path("synthetic", "states", "d05_identity.json")
    )
    transferable = build_toolkit(store.get("STATE-D05-001").instantiate())
    _verify(transferable, token="TEST_IDENTITY_C501", line="PN501")
    identity = _ok(
        _call(transferable, "get_identity_status", phone_number_id="PN501")
    ).data
    assert identity["real_name_status"] == "VERIFIED"
    conditions = _ok(
        _call(
            transferable,
            "check_ownership_transfer_conditions",
            phone_number_id="PN501",
        )
    ).data
    assert conditions["transferable"] is True
    assert conditions["blocking_conditions"] == []

    blocked = build_toolkit(store.get("STATE-D05-002").instantiate())
    _verify(blocked, token="TEST_IDENTITY_C502", line="PN502")
    conditions = _ok(
        _call(blocked, "check_ownership_transfer_conditions", phone_number_id="PN502")
    ).data
    assert conditions["transferable"] is False
    assert "real_name_not_verified" in conditions["blocking_conditions"]
    assert "arrears_outstanding" in conditions["blocking_conditions"]


def test_notification_creation_and_status(toolkit):
    _verify(toolkit)
    created = _ok(
        _call(
            toolkit,
            "send_notification",
            phone_number_id="PN401",
            kind="CONFIRMATION",
            related_ref="OFFER_D02_001",
        )
    ).data
    assert created["status"] == "SENT"

    status = _ok(
        _call(
            toolkit, "get_notification_status", notification_id=created["notification_id"]
        )
    ).data
    assert status["status"] == "SENT"
    assert status["related_ref"] == "OFFER_D02_001"

    bad_kind = _call(
        toolkit,
        "send_notification",
        phone_number_id="PN401",
        kind="PIGEON",
        related_ref="OFFER_D02_001",
    )
    assert not bad_kind.ok
    assert bad_kind.error.code == "invalid_notification_kind"

    missing = _call(toolkit, "get_notification_status", notification_id="NTF-9999")
    assert not missing.ok
    assert missing.error.code == "notification_not_found"


def test_cancel_offer_removes_enrollment_and_sms():
    store = StateStore.from_file(
        data_path("synthetic", "states", "d06_notification.json")
    )
    toolkit = build_toolkit(store.get("STATE-D06-001").instantiate())
    _verify(toolkit, token="TEST_IDENTITY_C601", line="PN601")
    toolkit.record_simulator_consent("PN601", "OFFER_D02_001")
    enrollment = _ok(
        _call(
            toolkit,
            "enroll_offer",
            phone_number_id="PN601",
            offer_id="OFFER_D02_001",
        )
    ).data
    _ok(_call(toolkit, "send_confirmation_sms", enrollment_id=enrollment["enrollment_id"]))

    toolkit.record_action_consent("PN601", "CANCELLATION", "OFFER_D02_001")
    _ok(
        _call(
            toolkit,
            "submit_cancellation",
            phone_number_id="PN601",
            target_type="OFFER",
            target_id="OFFER_D02_001",
        )
    )

    database = toolkit.legacy.db
    assert database.enrollments == []
    assert database.sms_records == []
    assert len(database.cancellation_requests) == 1


def test_consent_cannot_be_recorded_for_unknown_line(toolkit):
    assert toolkit.record_action_consent("PN-MISSING", "RECHARGE", "X") is None
    assert toolkit.record_action_consent("PN401", "NOT_AN_ACTION", "X") is None


def test_verification_is_shared_between_legacy_and_domain_tools(toolkit):
    _verify(toolkit)
    assert _call(toolkit, "get_customer_account", phone_number_id="PN401").ok
    assert _call(toolkit, "get_subscription_status", phone_number_id="PN401").ok
