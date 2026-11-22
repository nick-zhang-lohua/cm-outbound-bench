import json

from cm_outbound_bench.domain.tools import CmOutboundTools, ToolResult


def _call(tools: CmOutboundTools, name: str, **arguments) -> ToolResult:
    return tools.execute(name, arguments)


def _ok(tools: CmOutboundTools, name: str, **arguments) -> ToolResult:
    result = _call(tools, name, **arguments)
    assert result.ok, result.error
    return result


def _verify(tools: CmOutboundTools, line: str = "PN001") -> ToolResult:
    token = "TEST_IDENTITY_C001" if line in {"PN001", "PN002"} else "TEST_IDENTITY_C002"
    return _ok(
        tools,
        "verify_customer_identity",
        phone_number_id=line,
        identity_token=token,
    )


def test_tool_registry_has_required_tools(database):
    tools = CmOutboundTools(database)
    definitions = {item.name: item for item in tools.list_tools()}

    assert set(definitions) == {
        "verify_customer_identity",
        "get_customer_account",
        "get_offer_details",
        "check_offer_eligibility",
        "enroll_offer",
        "send_confirmation_sms",
        "register_do_not_call",
    }
    for definition in definitions.values():
        assert definition.description
        assert definition.parameters["type"] == "object"
        assert "properties" in definition.parameters

    enroll_schema = definitions["enroll_offer"].parameters
    assert "consent" not in enroll_schema["properties"]


def test_verification_success_and_failure(database):
    tools = CmOutboundTools(database)

    failed = _call(
        tools,
        "verify_customer_identity",
        phone_number_id="PN001",
        identity_token="TEST_IDENTITY_WRONG",
    )
    assert not failed.ok
    assert failed.error is not None
    assert failed.error.code == "identity_verification_failed"

    verified = _verify(tools)
    assert verified.data == {"verified": True, "phone_number_id": "PN001"}


def test_account_lookup_blocked_before_verification(database):
    tools = CmOutboundTools(database)
    result = _call(tools, "get_customer_account", phone_number_id="PN001")

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "identity_verification_required"
    assert "owner_name" not in result.model_dump(mode="json")


def test_account_lookup_after_verification(database):
    tools = CmOutboundTools(database)
    _verify(tools)

    account = _ok(tools, "get_customer_account", phone_number_id="PN001")
    assert account.data["customer_id"] == "C001"
    assert account.data["owner_name"] == "林启明"
    assert account.data["current_plan"]["plan_id"] == "PLAN_BASIC"
    assert account.data["current_plan"]["currency"] == "CNY"
    assert "identity_token" not in account.data


def test_verification_is_scoped_to_one_line(database):
    tools = CmOutboundTools(database)
    _verify(tools, "PN001")

    result = _call(tools, "get_customer_account", phone_number_id="PN003")
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "identity_verification_required"


def test_offer_details_are_accurate(database):
    tools = CmOutboundTools(database)
    offer = _ok(tools, "get_offer_details", offer_id="OFFER_DATA_001").data

    assert offer["name"] == "模拟流量优惠活动"
    assert offer["monthly_fee"] == 10.0
    assert offer["currency"] == "CNY"
    assert offer["validity_months"] == 6
    assert offer["auto_renewal"] is True
    assert offer["restrictions"]
    assert offer["active"] is True


def test_eligibility_requires_verification_and_checks_plan_status(database):
    tools = CmOutboundTools(database)
    blocked = _call(
        tools,
        "check_offer_eligibility",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
    )
    assert not blocked.ok
    assert blocked.error is not None
    assert blocked.error.code == "identity_verification_required"

    _verify(tools, "PN001")
    eligible = _ok(
        tools,
        "check_offer_eligibility",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
    )
    assert eligible.data["eligible"] is True
    assert eligible.data["reasons"] == []

    _verify(tools, "PN002")
    ineligible = _ok(
        tools,
        "check_offer_eligibility",
        phone_number_id="PN002",
        offer_id="OFFER_DATA_001",
    )
    assert ineligible.data["eligible"] is False
    assert "line_not_active" in ineligible.data["reasons"]
    assert "plan_not_eligible" in ineligible.data["reasons"]


def test_enrollment_requires_runner_recorded_consent(database):
    tools = CmOutboundTools(database)
    _verify(tools)

    result = _call(
        tools,
        "enroll_offer",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "consent_required"
    assert database.enrollments == []


def test_enrollment_blocked_when_ineligible(database):
    tools = CmOutboundTools(database)
    _verify(tools, "PN002")
    consent = tools.record_simulator_consent("PN002", "OFFER_DATA_001")
    assert consent is not None

    result = _call(
        tools,
        "enroll_offer",
        phone_number_id="PN002",
        offer_id="OFFER_DATA_001",
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "line_not_active"
    assert database.enrollments == []


def test_enrollment_blocked_for_non_owner_line(database):
    tools = CmOutboundTools(database)
    _verify(tools, "PN001")

    result = _call(
        tools,
        "enroll_offer",
        phone_number_id="PN003",
        offer_id="OFFER_DATA_001",
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "identity_verification_required"

    wrong_token = _call(
        tools,
        "verify_customer_identity",
        phone_number_id="PN003",
        identity_token="TEST_IDENTITY_C001",
    )
    assert not wrong_token.ok
    assert database.enrollments == []


def test_successful_enrollment_is_unique(database):
    tools = CmOutboundTools(database)
    _verify(tools)
    assert tools.record_simulator_consent("PN001", "OFFER_DATA_001") is not None

    first = _ok(
        tools,
        "enroll_offer",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
    )
    assert first.data["status"] == "enrolled"
    duplicate = _call(
        tools,
        "enroll_offer",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
    )
    assert not duplicate.ok
    assert duplicate.error is not None
    assert duplicate.error.code == "already_enrolled"
    assert len(database.enrollments) == 1


def test_do_not_call_is_idempotent_and_blocks_enrollment(database):
    tools = CmOutboundTools(database)
    first = _ok(tools, "register_do_not_call", phone_number_id="PN001")
    second = _ok(tools, "register_do_not_call", phone_number_id="PN001")

    assert first.data["record_id"] == second.data["record_id"]
    assert second.data["already_registered"] is True
    assert len(database.do_not_call_records) == 1

    _verify(tools)
    consent = tools.record_simulator_consent("PN001", "OFFER_DATA_001")
    assert consent is None
    result = _call(
        tools,
        "enroll_offer",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
    )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "do_not_call_registered"


def test_confirmation_sms_requires_enrollment(database):
    tools = CmOutboundTools(database)
    _verify(tools)

    before = _call(
        tools,
        "send_confirmation_sms",
        enrollment_id="ENR-9999",
    )
    assert not before.ok
    assert before.error is not None
    assert before.error.code == "enrollment_not_found"

    assert tools.record_simulator_consent("PN001", "OFFER_DATA_001") is not None
    enrollment = _ok(
        tools,
        "enroll_offer",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
    )
    sms = _ok(
        tools,
        "send_confirmation_sms",
        enrollment_id=enrollment.data["enrollment_id"],
    )
    assert sms.data["status"] == "simulated_sent"
    assert len(database.sms_records) == 1

    duplicate = _call(
        tools,
        "send_confirmation_sms",
        enrollment_id=enrollment.data["enrollment_id"],
    )
    assert not duplicate.ok
    assert duplicate.error is not None
    assert duplicate.error.code == "confirmation_already_sent"


def test_tool_errors_are_structured_and_json_serializable(database):
    tools = CmOutboundTools(database)

    unknown = _call(tools, "not_a_tool")
    assert not unknown.ok
    assert unknown.error is not None
    assert unknown.error.code == "unknown_tool"
    json.dumps(unknown.model_dump(mode="json"))

    invalid = _call(tools, "verify_customer_identity")
    assert not invalid.ok
    assert invalid.error is not None
    assert invalid.error.code == "invalid_arguments"
    json.dumps(invalid.model_dump(mode="json"))

    missing = _call(tools, "get_offer_details", offer_id="OFFER_404")
    assert not missing.ok
    assert missing.error is not None
    assert missing.error.code == "offer_not_found"
