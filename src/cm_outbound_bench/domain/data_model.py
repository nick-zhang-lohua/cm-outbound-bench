"""Synthetic domain data and operational state for marketing outbound calls.

Every record in this module is fictional benchmark data. Phone numbers and
identity tokens are obviously non-routable test values, never real customer
information.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainModel(BaseModel):
    """Base configuration shared by the small domain models."""

    model_config = ConfigDict(extra="forbid")


class PhoneStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class ConsentSource(str, Enum):
    USER_SIMULATOR = "user_simulator"


class SmsStatus(str, Enum):
    SIMULATED_SENT = "simulated_sent"


class DoNotCallSource(str, Enum):
    CUSTOMER_REQUEST = "customer_request"


class Customer(DomainModel):
    customer_id: str = Field(description="Unique synthetic customer identifier.")
    owner_name: str = Field(
        min_length=1,
        description="Fictional name of the registered customer.",
    )
    identity_token: str = Field(
        pattern=r"^TEST_IDENTITY_[A-Za-z0-9_]+$",
        description="Synthetic identity-verification token used by the simulator.",
    )


class PhoneNumber(DomainModel):
    phone_number_id: str = Field(
        description="Unique internal identifier of the phone line."
    )
    phone_number: str = Field(
        pattern=r"^SIM_LINE_[A-Za-z0-9_]+$",
        description="Synthetic non-routable phone-number token.",
    )
    owner_customer_id: str = Field(
        description="Customer ID of the registered phone-line owner."
    )
    current_plan_id: str = Field(description="ID of the line's current plan.")
    status: PhoneStatus = Field(
        default=PhoneStatus.ACTIVE,
        description="Current operational status of the line.",
    )


class Plan(DomainModel):
    plan_id: str = Field(description="Unique synthetic plan identifier.")
    name: str = Field(min_length=1, description="Display name of the fictional plan.")
    monthly_fee: float = Field(
        ge=0,
        description="Synthetic monthly plan fee.",
    )
    currency: Literal["CNY"] = Field(
        default="CNY",
        description="Currency of all synthetic prices.",
    )


class Offer(DomainModel):
    offer_id: str = Field(description="Unique synthetic marketing offer identifier.")
    name: str = Field(min_length=1, description="Display name of the fictional offer.")
    eligible_plan_ids: list[str] = Field(
        min_length=1,
        description="Plan IDs eligible for this offer.",
    )
    active: bool = Field(
        default=True,
        description="Whether the offer is currently available.",
    )
    monthly_fee: float = Field(
        default=0.0,
        ge=0,
        description="Synthetic additional monthly fee during the offer period.",
    )
    currency: Literal["CNY"] = Field(
        default="CNY",
        description="Currency of the offer's monthly fee.",
    )
    validity_months: int = Field(
        default=12,
        ge=1,
        description="Number of months the offer pricing is valid.",
    )
    auto_renewal: bool = Field(
        default=False,
        description="Whether the offer renews automatically after its validity period.",
    )
    restrictions: str = Field(
        default="",
        description="Short synthetic restrictions that must be disclosed.",
    )


class ConsentRecord(DomainModel):
    """A positive consent signal recorded by the benchmark runner.

    The presence of a record means the user simulator explicitly consented to
    the offer for that line. The agent cannot create these records through a
    business tool.
    """

    consent_id: str = Field(description="Unique consent record identifier.")
    customer_id: str = Field(description="Customer who gave consent.")
    phone_number_id: str = Field(description="Line covered by the consent.")
    offer_id: str = Field(description="Offer covered by the consent.")
    source: ConsentSource = Field(
        default=ConsentSource.USER_SIMULATOR,
        description="Only the benchmark simulator may be the consent source.",
    )


class Enrollment(DomainModel):
    enrollment_id: str = Field(description="Unique enrollment identifier.")
    customer_id: str = Field(description="Customer whose line was enrolled.")
    phone_number_id: str = Field(description="Enrolled phone line.")
    offer_id: str = Field(description="Enrolled marketing offer.")
    consent_id: str = Field(description="Consent record used for this enrollment.")


class SmsRecord(DomainModel):
    sms_id: str = Field(description="Unique synthetic SMS record identifier.")
    enrollment_id: str = Field(description="Enrollment confirmed by the SMS.")
    phone_number_id: str = Field(description="Line that receives the SMS.")
    status: SmsStatus = Field(
        default=SmsStatus.SIMULATED_SENT,
        description="Simulated delivery result. No SMS provider is called.",
    )


class DoNotCallRecord(DomainModel):
    record_id: str = Field(description="Unique do-not-call record identifier.")
    customer_id: str = Field(description="Customer who requested no marketing.")
    phone_number_id: str = Field(
        description="Line on which the opt-out request was received."
    )
    source: DoNotCallSource = Field(
        default=DoNotCallSource.CUSTOMER_REQUEST,
        description="Origin of the opt-out preference.",
    )


# ---------------------------------------------------------------------------
# Multi-domain operational entities (D01, D03, D04, D05, D06).
#
# These are all synthetic benchmark records. They were added without changing
# any legacy field, so the original five marketing tasks keep working against
# the unchanged `data/synthetic/db.json` fixture.
# ---------------------------------------------------------------------------


class PlanAllowance(DomainModel):
    """D01: what a plan actually contains."""

    plan_id: str = Field(description="Plan the allowance belongs to.")
    data_mb: int = Field(ge=0, description="Monthly data allowance in megabytes.")
    voice_minutes: int = Field(ge=0, description="Monthly voice minutes.")
    sms_count: int = Field(ge=0, description="Monthly SMS allowance.")
    note: str = Field(default="", description="Short synthetic allowance note.")


class BillingAccount(DomainModel):
    """D03: prepaid balance and arrears for one line."""

    account_id: str = Field(description="Unique synthetic billing account.")
    customer_id: str = Field(description="Account holder.")
    phone_number_id: str = Field(description="Line the account belongs to.")
    balance_cents: int = Field(description="Current balance in cents; may be negative.")
    arrears_cents: int = Field(
        default=0, ge=0, description="Outstanding arrears in cents."
    )
    currency: Literal["CNY"] = "CNY"


class Charge(DomainModel):
    """D03: one billed or pending charge line."""

    charge_id: str
    account_id: str
    description: str = Field(min_length=1)
    amount_cents: int = Field(ge=0)
    billing_period: str = Field(
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Billing period in YYYY-MM form.",
    )
    status: Literal["BILLED", "PENDING", "DISPUTED"] = "BILLED"


class Recharge(DomainModel):
    """D03: a simulated prepaid top-up."""

    recharge_id: str
    account_id: str
    amount_cents: int = Field(gt=0)
    channel: Literal["SIMULATED_APP", "SIMULATED_SMS", "SIMULATED_COUNTER"] = (
        "SIMULATED_APP"
    )
    status: Literal["COMPLETED", "FAILED"] = "COMPLETED"


class Subscription(DomainModel):
    """D04: the lifecycle state of one line's plan subscription."""

    subscription_id: str
    phone_number_id: str
    plan_id: str
    status: Literal["ACTIVE", "SUSPENDED", "CLOSED"] = "ACTIVE"
    started_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    contract_end_on: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Contract end date when the line is inside a commitment.",
    )


class PlanChangeRequest(DomainModel):
    """D04: a requested upgrade or downgrade."""

    request_id: str
    phone_number_id: str
    from_plan_id: str
    to_plan_id: str
    status: Literal["REQUESTED", "APPROVED", "REJECTED"] = "REQUESTED"
    effective_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class CancellationRequest(DomainModel):
    """D04: a requested cancellation or unsubscription."""

    request_id: str
    phone_number_id: str
    target_type: Literal["PLAN", "OFFER", "SERVICE"]
    target_id: str
    status: Literal["REQUESTED", "APPROVED", "REJECTED"] = "REQUESTED"
    effective_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class IdentityRecord(DomainModel):
    """D05: real-name registration state for one line."""

    phone_number_id: str
    real_name_status: Literal["VERIFIED", "NOT_VERIFIED", "PENDING"] = (
        "NOT_VERIFIED"
    )
    registration_type: Literal["PERSONAL", "CORPORATE"] = "PERSONAL"


class Notification(DomainModel):
    """D06: a simulated outbound notification and its delivery status."""

    notification_id: str
    phone_number_id: str
    kind: Literal["CONFIRMATION", "REMINDER", "FOLLOW_UP", "INFO"] = "CONFIRMATION"
    channel: Literal["SMS"] = "SMS"
    status: Literal["QUEUED", "SENT", "FAILED"] = "QUEUED"
    related_ref: str | None = Field(
        default=None,
        description="Opaque reference to the enrollment, request or change.",
    )


class ActionConsentRecord(DomainModel):
    """Consent for a non-offer business action, recorded only by the runner.

    Offer enrollment keeps using the original ``ConsentRecord`` so that the
    legacy marketing flow and its tests are untouched. Every other irreversible
    action (plan change, cancellation, recharge, ownership transfer) needs one
    of these records, and no business tool can create it.
    """

    consent_id: str
    customer_id: str
    phone_number_id: str
    action: Literal[
        "PLAN_CHANGE",
        "CANCELLATION",
        "RECHARGE",
        "OWNERSHIP_TRANSFER",
    ]
    target_ref: str = Field(
        default="",
        description="Plan, offer, service or account reference the consent covers.",
    )
    source: ConsentSource = ConsentSource.USER_SIMULATOR



class CmOutboundDB(DomainModel):
    """Small synthetic business database used by the benchmark runtime."""

    customers: list[Customer] = Field(
        default_factory=list,
        description="Synthetic customer and registered-owner records.",
    )
    phone_numbers: list[PhoneNumber] = Field(
        default_factory=list,
        description="Synthetic phone-line records.",
    )
    plans: list[Plan] = Field(
        default_factory=list,
        description="Available synthetic mobile plans.",
    )
    offers: list[Offer] = Field(
        default_factory=list,
        description="Available synthetic marketing offers.",
    )
    consent_records: list[ConsentRecord] = Field(
        default_factory=list,
        description="Explicit simulator consents recorded for this task.",
    )
    enrollments: list[Enrollment] = Field(
        default_factory=list,
        description="Offer enrollments created during the task.",
    )
    sms_records: list[SmsRecord] = Field(
        default_factory=list,
        description="Simulated confirmation SMS records.",
    )
    do_not_call_records: list[DoNotCallRecord] = Field(
        default_factory=list,
        description="Customer opt-out preferences.",
    )
    plan_allowances: list[PlanAllowance] = Field(
        default_factory=list,
        description="D01 plan content and allowances.",
    )
    billing_accounts: list[BillingAccount] = Field(
        default_factory=list,
        description="D03 prepaid billing accounts.",
    )
    charges: list[Charge] = Field(
        default_factory=list,
        description="D03 charge lines.",
    )
    recharges: list[Recharge] = Field(
        default_factory=list,
        description="D03 simulated top-ups.",
    )
    subscriptions: list[Subscription] = Field(
        default_factory=list,
        description="D04 subscription lifecycle state.",
    )
    plan_change_requests: list[PlanChangeRequest] = Field(
        default_factory=list,
        description="D04 requested plan changes.",
    )
    cancellation_requests: list[CancellationRequest] = Field(
        default_factory=list,
        description="D04 requested cancellations.",
    )
    identity_records: list[IdentityRecord] = Field(
        default_factory=list,
        description="D05 real-name registration state.",
    )
    notifications: list[Notification] = Field(
        default_factory=list,
        description="D06 simulated notifications.",
    )
    action_consents: list[ActionConsentRecord] = Field(
        default_factory=list,
        description="Runner-recorded consent for non-offer business actions.",
    )

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Load and validate a database from a UTF-8 JSON file."""
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def deep_copy(self) -> Self:
        """Return an independent copy for one evaluation run."""
        return self.model_copy(deep=True)

    @model_validator(mode="after")
    def validate_database(self) -> Self:
        """Validate uniqueness, references and operational consistency."""
        self._validate_unique_ids()
        self._validate_references()
        self._validate_operational_state()
        return self

    def _validate_unique_ids(self) -> None:
        for label, values in (
            ("customer_id", [item.customer_id for item in self.customers]),
            ("phone_number_id", [item.phone_number_id for item in self.phone_numbers]),
            ("phone_number", [item.phone_number for item in self.phone_numbers]),
            ("plan_id", [item.plan_id for item in self.plans]),
            ("offer_id", [item.offer_id for item in self.offers]),
            ("consent_id", [item.consent_id for item in self.consent_records]),
            ("enrollment_id", [item.enrollment_id for item in self.enrollments]),
            ("sms_id", [item.sms_id for item in self.sms_records]),
            ("record_id", [item.record_id for item in self.do_not_call_records]),
            ("account_id", [item.account_id for item in self.billing_accounts]),
            ("charge_id", [item.charge_id for item in self.charges]),
            ("recharge_id", [item.recharge_id for item in self.recharges]),
            (
                "subscription_id",
                [item.subscription_id for item in self.subscriptions],
            ),
            (
                "plan_change_request_id",
                [item.request_id for item in self.plan_change_requests],
            ),
            (
                "cancellation_request_id",
                [item.request_id for item in self.cancellation_requests],
            ),
            (
                "notification_id",
                [item.notification_id for item in self.notifications],
            ),
            (
                "action_consent_id",
                [item.consent_id for item in self.action_consents],
            ),
        ):
            self._ensure_unique(label, values)

    def _validate_references(self) -> None:
        customer_ids = {item.customer_id for item in self.customers}
        plan_ids = {item.plan_id for item in self.plans}
        offer_ids = {item.offer_id for item in self.offers}
        phone_ids = {item.phone_number_id for item in self.phone_numbers}

        for phone in self.phone_numbers:
            if phone.owner_customer_id not in customer_ids:
                raise ValueError(
                    "Phone number "
                    f"{phone.phone_number_id} references unknown customer "
                    f"{phone.owner_customer_id}"
                )
            if phone.current_plan_id not in plan_ids:
                raise ValueError(
                    "Phone number "
                    f"{phone.phone_number_id} references unknown plan "
                    f"{phone.current_plan_id}"
                )

        for offer in self.offers:
            unknown_plans = sorted(set(offer.eligible_plan_ids) - plan_ids)
            if unknown_plans:
                raise ValueError(
                    f"Offer {offer.offer_id} references unknown plans: {unknown_plans}"
                )

        for consent in self.consent_records:
            if consent.customer_id not in customer_ids:
                raise ValueError(
                    f"Consent {consent.consent_id} references unknown customer"
                )
            if consent.offer_id not in offer_ids:
                raise ValueError(
                    f"Consent {consent.consent_id} references unknown offer"
                )

        for record in self.do_not_call_records:
            if record.customer_id not in customer_ids:
                raise ValueError(
                    f"Do-not-call record {record.record_id} references an "
                    "unknown customer"
                )

        for allowance in self.plan_allowances:
            if allowance.plan_id not in plan_ids:
                raise ValueError(
                    f"Plan allowance references unknown plan {allowance.plan_id}"
                )

        for account in self.billing_accounts:
            if account.customer_id not in customer_ids:
                raise ValueError(
                    f"Billing account {account.account_id} references an "
                    "unknown customer"
                )
            if account.phone_number_id not in phone_ids:
                raise ValueError(
                    f"Billing account {account.account_id} references an "
                    "unknown line"
                )

        account_ids = {item.account_id for item in self.billing_accounts}
        for charge in self.charges:
            if charge.account_id not in account_ids:
                raise ValueError(
                    f"Charge {charge.charge_id} references an unknown account"
                )
        for recharge in self.recharges:
            if recharge.account_id not in account_ids:
                raise ValueError(
                    f"Recharge {recharge.recharge_id} references an unknown account"
                )

        for subscription in self.subscriptions:
            if subscription.phone_number_id not in phone_ids:
                raise ValueError(
                    "Subscription "
                    f"{subscription.subscription_id} references an unknown line"
                )
            if subscription.plan_id not in plan_ids:
                raise ValueError(
                    "Subscription "
                    f"{subscription.subscription_id} references an unknown plan"
                )

        for plan_change in self.plan_change_requests:
            if plan_change.phone_number_id not in phone_ids:
                raise ValueError(
                    f"Plan change {plan_change.request_id} references an "
                    "unknown line"
                )
            for plan_id in (plan_change.from_plan_id, plan_change.to_plan_id):
                if plan_id not in plan_ids:
                    raise ValueError(
                        f"Plan change {plan_change.request_id} references an "
                        f"unknown plan {plan_id}"
                    )

        for cancellation in self.cancellation_requests:
            if cancellation.phone_number_id not in phone_ids:
                raise ValueError(
                    f"Cancellation {cancellation.request_id} references an "
                    "unknown line"
                )

        for identity in self.identity_records:
            if identity.phone_number_id not in phone_ids:
                raise ValueError(
                    "Identity record references an unknown line "
                    f"{identity.phone_number_id}"
                )

        for notification in self.notifications:
            if notification.phone_number_id not in phone_ids:
                raise ValueError(
                    f"Notification {notification.notification_id} references an "
                    "unknown line"
                )

        for action_consent in self.action_consents:
            if action_consent.customer_id not in customer_ids:
                raise ValueError(
                    f"Action consent {action_consent.consent_id} references an "
                    "unknown customer"
                )
            if action_consent.phone_number_id not in phone_ids:
                raise ValueError(
                    f"Action consent {action_consent.consent_id} references an "
                    "unknown line"
                )

    def _validate_operational_state(self) -> None:
        phones = {item.phone_number_id: item for item in self.phone_numbers}
        offers = {item.offer_id: item for item in self.offers}
        consents = {item.consent_id: item for item in self.consent_records}
        enrollments = {item.enrollment_id: item for item in self.enrollments}

        consent_keys: set[tuple[str, str]] = set()
        for consent in self.consent_records:
            line = self._require_line(phones, consent.phone_number_id, "Consent")
            self._require_owner(line, consent.customer_id, "Consent")
            if consent.offer_id not in offers:
                raise ValueError(
                    f"Consent {consent.consent_id} references unknown offer"
                )
            key = (consent.phone_number_id, consent.offer_id)
            if key in consent_keys:
                raise ValueError(
                    "Duplicate consent for line/offer: "
                    f"{consent.phone_number_id}/{consent.offer_id}"
                )
            consent_keys.add(key)

        enrollment_keys: set[tuple[str, str]] = set()
        for enrollment in self.enrollments:
            line = self._require_line(phones, enrollment.phone_number_id, "Enrollment")
            self._require_owner(line, enrollment.customer_id, "Enrollment")
            if enrollment.offer_id not in offers:
                raise ValueError(
                    f"Enrollment {enrollment.enrollment_id} references unknown offer"
                )
            consent = consents.get(enrollment.consent_id)
            if consent is None:
                raise ValueError(
                    f"Enrollment {enrollment.enrollment_id} references unknown consent"
                )
            if (
                consent.customer_id,
                consent.phone_number_id,
                consent.offer_id,
            ) != (
                enrollment.customer_id,
                enrollment.phone_number_id,
                enrollment.offer_id,
            ):
                raise ValueError(
                    "Enrollment consent does not match its customer, line and offer"
                )
            key = (enrollment.phone_number_id, enrollment.offer_id)
            if key in enrollment_keys:
                raise ValueError(
                    "Duplicate enrollment for line/offer: "
                    f"{enrollment.phone_number_id}/{enrollment.offer_id}"
                )
            enrollment_keys.add(key)

        sms_enrollments: set[str] = set()
        for sms in self.sms_records:
            enrollment = enrollments.get(sms.enrollment_id)
            if enrollment is None:
                raise ValueError(
                    f"SMS record {sms.sms_id} references unknown enrollment"
                )
            if sms.phone_number_id != enrollment.phone_number_id:
                raise ValueError(
                    f"SMS record {sms.sms_id} does not match its enrollment line"
                )
            if sms.enrollment_id in sms_enrollments:
                raise ValueError(
                    f"Duplicate confirmation SMS for enrollment: {sms.enrollment_id}"
                )
            sms_enrollments.add(sms.enrollment_id)

        dnc_customers: set[str] = set()
        for record in self.do_not_call_records:
            line = self._require_line(
                phones, record.phone_number_id, "Do-not-call record"
            )
            self._require_owner(line, record.customer_id, "Do-not-call record")
            if record.customer_id in dnc_customers:
                raise ValueError(
                    f"Duplicate do-not-call preference for {record.customer_id}"
                )
            dnc_customers.add(record.customer_id)

        self._validate_multidomain_state(phones)

    def _validate_multidomain_state(
        self, phones: dict[str, PhoneNumber]
    ) -> None:
        """Validate invariants introduced for D01, D03, D04, D05 and D06."""
        # D01: one allowance record per plan.
        allowance_plans: set[str] = set()
        for allowance in self.plan_allowances:
            if allowance.plan_id in allowance_plans:
                raise ValueError(
                    f"Duplicate plan allowance for {allowance.plan_id}"
                )
            allowance_plans.add(allowance.plan_id)

        # D03: one billing account per line, owner-consistent, arrears <= charges.
        account_lines: set[str] = set()
        account_owner: dict[str, str] = {}
        for account in self.billing_accounts:
            line = self._require_line(
                phones, account.phone_number_id, "Billing account"
            )
            self._require_owner(line, account.customer_id, "Billing account")
            if account.phone_number_id in account_lines:
                raise ValueError(
                    "Duplicate billing account for line "
                    f"{account.phone_number_id}"
                )
            account_lines.add(account.phone_number_id)
            account_owner[account.account_id] = account.customer_id

        # D04: at most one active subscription per line.
        active_lines: set[str] = set()
        for subscription in self.subscriptions:
            if subscription.status == "ACTIVE":
                if subscription.phone_number_id in active_lines:
                    raise ValueError(
                        "Duplicate active subscription for line "
                        f"{subscription.phone_number_id}"
                    )
                active_lines.add(subscription.phone_number_id)

        # D05: exactly one identity record per line.
        identity_lines: set[str] = set()
        for identity in self.identity_records:
            if identity.phone_number_id in identity_lines:
                raise ValueError(
                    "Duplicate identity record for line "
                    f"{identity.phone_number_id}"
                )
            identity_lines.add(identity.phone_number_id)

        # D06: a SENT notification must reference the object it confirms.
        notification_ids: set[str] = set()
        for notification in self.notifications:
            if notification.notification_id in notification_ids:
                raise ValueError(
                    f"Duplicate notification {notification.notification_id}"
                )
            notification_ids.add(notification.notification_id)
            if notification.status in {"SENT", "QUEUED"} and not notification.related_ref:
                raise ValueError(
                    f"Notification {notification.notification_id} requires a "
                    "related_ref"
                )

        # Non-offer consent must belong to the account holder of the line.
        consent_keys: set[tuple[str, str, str]] = set()
        for action_consent in self.action_consents:
            line = self._require_line(
                phones, action_consent.phone_number_id, "Action consent"
            )
            self._require_owner(
                line, action_consent.customer_id, "Action consent"
            )
            key = (
                action_consent.phone_number_id,
                action_consent.action,
                action_consent.target_ref,
            )
            if key in consent_keys:
                raise ValueError(
                    "Duplicate action consent for "
                    f"{action_consent.phone_number_id}/"
                    f"{action_consent.action}/{action_consent.target_ref}"
                )
            consent_keys.add(key)

    @staticmethod
    def _require_line(
        phones: dict[str, PhoneNumber],
        phone_number_id: str,
        label: str,
    ) -> PhoneNumber:
        line = phones.get(phone_number_id)
        if line is None:
            raise ValueError(f"{label} references unknown phone line")
        return line

    @staticmethod
    def _require_owner(line: PhoneNumber, customer_id: str, label: str) -> None:
        if line.owner_customer_id != customer_id:
            raise ValueError(f"{label} customer does not own the referenced line")

    @staticmethod
    def _ensure_unique(label: str, values: list[str]) -> None:
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate values found for {label}")

    def get_statistics(self) -> dict[str, int]:
        """Return basic catalog statistics."""
        return {
            "num_customers": len(self.customers),
            "num_phone_numbers": len(self.phone_numbers),
            "num_plans": len(self.plans),
            "num_offers": len(self.offers),
        }
