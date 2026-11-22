import pytest
from pydantic import ValidationError

from cm_outbound_bench.domain.data_model import (
    CmOutboundDB,
    ConsentRecord,
    DoNotCallRecord,
    Enrollment,
    SmsRecord,
)


def test_load_synthetic_database(database):
    assert database.get_statistics() == {
        "num_customers": 2,
        "num_phone_numbers": 3,
        "num_plans": 3,
        "num_offers": 2,
    }


def test_customer_can_own_multiple_phone_numbers(database):
    customer_phones = [
        phone for phone in database.phone_numbers if phone.owner_customer_id == "C001"
    ]
    assert len(customer_phones) == 2


@pytest.mark.parametrize(
    ("collection", "index", "field", "invalid_value"),
    [
        ("phone_numbers", 0, "owner_customer_id", "C999"),
        ("phone_numbers", 0, "current_plan_id", "PLAN_UNKNOWN"),
        ("offers", 0, "eligible_plan_ids", ["PLAN_UNKNOWN"]),
    ],
)
def test_reject_unknown_references(database, collection, index, field, invalid_value):
    data = database.model_dump(mode="json")
    data[collection][index][field] = invalid_value

    with pytest.raises(ValidationError):
        CmOutboundDB.model_validate(data)


@pytest.mark.parametrize(
    ("collection", "index", "field", "duplicate_value"),
    [
        ("customers", 1, "customer_id", "C001"),
        ("phone_numbers", 1, "phone_number_id", "PN001"),
        ("phone_numbers", 1, "phone_number", "SIM_LINE_0001"),
        ("plans", 1, "plan_id", "PLAN_BASIC"),
        ("offers", 1, "offer_id", "OFFER_DATA_001"),
    ],
)
def test_reject_duplicate_identifiers(
    database, collection, index, field, duplicate_value
):
    data = database.model_dump(mode="json")
    data[collection][index][field] = duplicate_value

    with pytest.raises(ValidationError):
        CmOutboundDB.model_validate(data)


def test_deep_copy_is_independent(database):
    copied = database.deep_copy()
    copied.do_not_call_records.append(
        DoNotCallRecord(
            record_id="DNC-0001",
            customer_id="C001",
            phone_number_id="PN001",
        )
    )
    copied.offers[0].eligible_plan_ids.append("PLAN_PREMIUM")

    assert database.do_not_call_records == []
    assert "PLAN_PREMIUM" not in database.offers[0].eligible_plan_ids
    assert len(copied.do_not_call_records) == 1


def test_reject_consent_for_wrong_line_owner(database):
    data = database.model_dump(mode="json")
    data["consent_records"].append(
        ConsentRecord(
            consent_id="CONSENT-0001",
            customer_id="C001",
            phone_number_id="PN003",
            offer_id="OFFER_DATA_001",
        ).model_dump(mode="json")
    )

    with pytest.raises(ValidationError):
        CmOutboundDB.model_validate(data)


def test_reject_enrollment_without_consent_record(database):
    data = database.model_dump(mode="json")
    data["enrollments"].append(
        Enrollment(
            enrollment_id="ENR-0001",
            customer_id="C001",
            phone_number_id="PN001",
            offer_id="OFFER_DATA_001",
            consent_id="CONSENT-404",
        ).model_dump(mode="json")
    )

    with pytest.raises(ValidationError):
        CmOutboundDB.model_validate(data)


def test_reject_duplicate_enrollment(database):
    data = database.model_dump(mode="json")
    data["consent_records"].append(
        ConsentRecord(
            consent_id="CONSENT-0001",
            customer_id="C001",
            phone_number_id="PN001",
            offer_id="OFFER_DATA_001",
        ).model_dump(mode="json")
    )
    enrollment = Enrollment(
        enrollment_id="ENR-0001",
        customer_id="C001",
        phone_number_id="PN001",
        offer_id="OFFER_DATA_001",
        consent_id="CONSENT-0001",
    )
    data["enrollments"].extend(
        [
            enrollment.model_dump(mode="json"),
            enrollment.model_copy(update={"enrollment_id": "ENR-0002"}).model_dump(
                mode="json"
            ),
        ]
    )

    with pytest.raises(ValidationError):
        CmOutboundDB.model_validate(data)


def test_reject_sms_before_enrollment(database):
    data = database.model_dump(mode="json")
    data["sms_records"].append(
        SmsRecord(
            sms_id="SMS-0001",
            enrollment_id="ENR-404",
            phone_number_id="PN001",
        ).model_dump(mode="json")
    )

    with pytest.raises(ValidationError):
        CmOutboundDB.model_validate(data)
