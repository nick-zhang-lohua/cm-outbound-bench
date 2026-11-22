"""Typed business tools for the marketing outbound domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, ValidationError, model_validator

from cm_outbound_bench.domain.data_model import (
    CmOutboundDB,
    ConsentRecord,
    ConsentSource,
    Customer,
    DomainModel,
    DoNotCallRecord,
    Enrollment,
    Offer,
    PhoneNumber,
    PhoneStatus,
    Plan,
    SmsRecord,
    SmsStatus,
)


class VerifyCustomerIdentityArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    identity_token: str = Field(
        description=(
            "Identity token supplied by the customer in the current "
            "conversation. Never construct, guess, infer or derive it from a "
            "phone number, customer ID, account ID or any other identifier."
        )
    )


class GetCustomerAccountArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")


class GetOfferDetailsArgs(DomainModel):
    offer_id: str = Field(description="Marketing offer identifier.")


class CheckOfferEligibilityArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    offer_id: str = Field(description="Marketing offer identifier.")


class EnrollOfferArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    offer_id: str = Field(description="Marketing offer identifier.")


class SendConfirmationSmsArgs(DomainModel):
    enrollment_id: str = Field(description="Successful enrollment identifier.")


class RegisterDoNotCallArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")


class ToolError(DomainModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Safe message for the agent.")


class ToolResult(DomainModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: ToolError | None = None

    @classmethod
    def success(cls, data: dict[str, Any]) -> "ToolResult":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, code: str, message: str) -> "ToolResult":
        return cls(ok=False, error=ToolError(code=code, message=message))

    @model_validator(mode="after")
    def validate_result_shape(self) -> "ToolResult":
        if self.ok and self.error is not None:
            raise ValueError("A successful tool result cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("A failed tool result must contain an error")
        return self


class ToolDefinition(DomainModel):
    name: str
    description: str
    parameters: dict[str, Any]
    mutating: bool = Field(
        default=False,
        description=(
            "True when the tool changes persistent business state. The agent "
            "must report such an outcome to the customer before finishing."
        ),
    )


@dataclass
class ToolSession:
    """Per-task state that is not part of the persistent business database."""

    verified_phone_number_ids: set[str] = field(default_factory=set)

    def mark_verified(self, phone_number_id: str) -> None:
        self.verified_phone_number_ids.add(phone_number_id)

    def is_verified(self, phone_number_id: str) -> bool:
        return phone_number_id in self.verified_phone_number_ids


_TOOL_REGISTRY: dict[str, tuple[type[DomainModel], str]] = {
    "verify_customer_identity": (
        VerifyCustomerIdentityArgs,
        "Verify the owner of a synthetic line using the identity token the "
        "customer supplied in the current conversation. Verification is scoped "
        "to the current task session.",
    ),
    "get_customer_account": (
        GetCustomerAccountArgs,
        "Return protected account details for a line. Requires successful "
        "identity verification for that line in this task.",
    ),
    "get_offer_details": (
        GetOfferDetailsArgs,
        "Return the synthetic offer's fee, validity, renewal conditions and "
        "restrictions.",
    ),
    "check_offer_eligibility": (
        CheckOfferEligibilityArgs,
        "Read-only check of whether a verified line is eligible for an offer. "
        "It changes no business state, requires successful identity verification "
        "and does not require enrollment consent.",
    ),
    "enroll_offer": (
        EnrollOfferArgs,
        "Enroll a verified, eligible line in an active offer. This changes "
        "business state. Call it only after the customer has explicitly "
        "authorized enrollment in this exact offer for this exact line AND the "
        "benchmark simulator has recorded that consent. Stated interest, a "
        "question about the terms, a permission to run a read-only check or a "
        "generic affirmative reply is not authorization.",
    ),
    "send_confirmation_sms": (
        SendConfirmationSmsArgs,
        "Record a simulated confirmation SMS after a successful enrollment. "
        "No real SMS provider is contacted.",
    ),
    "register_do_not_call": (
        RegisterDoNotCallArgs,
        "Register a customer opt-out preference. The operation is idempotent "
        "and blocks future marketing enrollment for the customer.",
    ),
}

#: Tools in this registry whose success changes persistent business state. The
#: LLM agent must report their outcome to the customer before finishing.
_STATE_CHANGING_TOOLS: frozenset[str] = frozenset(
    {"enroll_offer", "send_confirmation_sms", "register_do_not_call"}
)


class CmOutboundTools:
    """Business operations with explicit dispatch and structured results."""

    def __init__(
        self,
        db: CmOutboundDB,
        session: ToolSession | None = None,
    ) -> None:
        self.db = db
        self.session = session or ToolSession()

    def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=name,
                description=description,
                parameters=args_model.model_json_schema(),
                mutating=name in _STATE_CHANGING_TOOLS,
            )
            for name, (args_model, description) in _TOOL_REGISTRY.items()
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        registry_entry = _TOOL_REGISTRY.get(name)
        if registry_entry is None:
            return ToolResult.failure(
                "unknown_tool", f"Tool {name!r} is not available."
            )

        args_model, _ = registry_entry
        try:
            parsed_arguments = args_model.model_validate(arguments)
        except ValidationError:
            return ToolResult.failure(
                "invalid_arguments",
                f"Arguments do not match the schema for tool {name!r}.",
            )

        handler = getattr(self, name)
        result = handler(parsed_arguments)
        if not isinstance(result, ToolResult):
            raise TypeError(f"Tool {name!r} returned an invalid result")
        return result

    def verify_customer_identity(self, args: VerifyCustomerIdentityArgs) -> ToolResult:
        line = self._find_phone(args.phone_number_id)
        if line is None:
            return self._verification_failed()
        customer = self._find_customer(line.owner_customer_id)
        if customer is None or customer.identity_token != args.identity_token:
            return self._verification_failed()

        self.session.mark_verified(line.phone_number_id)
        return ToolResult.success(
            {
                "verified": True,
                "phone_number_id": line.phone_number_id,
            }
        )

    def get_customer_account(self, args: GetCustomerAccountArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()

        line = self._find_phone(args.phone_number_id)
        if line is None:
            return ToolResult.failure(
                "account_not_found", "The requested account is not available."
            )
        customer = self._find_customer(line.owner_customer_id)
        plan = self._find_plan(line.current_plan_id)
        if customer is None or plan is None:
            return ToolResult.failure(
                "account_not_found", "The requested account is not available."
            )

        return ToolResult.success(
            {
                "customer_id": customer.customer_id,
                "owner_name": customer.owner_name,
                "phone_number_id": line.phone_number_id,
                "phone_number": line.phone_number,
                "status": line.status.value,
                "current_plan": {
                    "plan_id": plan.plan_id,
                    "name": plan.name,
                    "monthly_fee": plan.monthly_fee,
                    "currency": plan.currency,
                },
            }
        )

    def get_offer_details(self, args: GetOfferDetailsArgs) -> ToolResult:
        offer = self._find_offer(args.offer_id)
        if offer is None:
            return ToolResult.failure(
                "offer_not_found", "The requested offer is not available."
            )
        return ToolResult.success(self._offer_data(offer))

    def check_offer_eligibility(self, args: CheckOfferEligibilityArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()

        line = self._find_phone(args.phone_number_id)
        offer = self._find_offer(args.offer_id)
        if line is None:
            return ToolResult.failure(
                "account_not_found", "The requested account is not available."
            )
        if offer is None:
            return ToolResult.failure(
                "offer_not_found", "The requested offer is not available."
            )

        reasons = self._eligibility_reasons(line, offer)
        return ToolResult.success(
            {
                "phone_number_id": line.phone_number_id,
                "offer_id": offer.offer_id,
                "eligible": not reasons,
                "reasons": reasons,
            }
        )

    def enroll_offer(self, args: EnrollOfferArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()

        line = self._find_phone(args.phone_number_id)
        offer = self._find_offer(args.offer_id)
        if line is None:
            return ToolResult.failure(
                "account_not_found", "The requested account is not available."
            )
        if offer is None:
            return ToolResult.failure(
                "offer_not_found", "The requested offer is not available."
            )

        reasons = self._eligibility_reasons(line, offer)
        if "offer_inactive" in reasons:
            return ToolResult.failure(
                "offer_inactive", "This offer is not currently available."
            )
        if "line_not_active" in reasons:
            return ToolResult.failure(
                "line_not_active", "This line cannot enroll in the offer."
            )
        if "plan_not_eligible" in reasons:
            return ToolResult.failure(
                "plan_not_eligible", "The line's plan is not eligible for this offer."
            )

        if self._do_not_call_for_customer(line.owner_customer_id) is not None:
            return ToolResult.failure(
                "do_not_call_registered",
                "The customer has opted out of marketing enrollment.",
            )

        if self._find_enrollment(line.phone_number_id, offer.offer_id) is not None:
            return ToolResult.failure(
                "already_enrolled", "This line is already enrolled in the offer."
            )

        consent = self._find_consent(line.phone_number_id, offer.offer_id)
        if consent is None:
            return ToolResult.failure(
                "consent_required",
                "Explicit consent from the customer is required before enrollment.",
            )

        enrollment = Enrollment(
            enrollment_id=self._next_id(
                "ENR", {item.enrollment_id for item in self.db.enrollments}
            ),
            customer_id=line.owner_customer_id,
            phone_number_id=line.phone_number_id,
            offer_id=offer.offer_id,
            consent_id=consent.consent_id,
        )
        self.db.enrollments.append(enrollment)
        return ToolResult.success(
            {
                "enrollment_id": enrollment.enrollment_id,
                "phone_number_id": enrollment.phone_number_id,
                "offer_id": enrollment.offer_id,
                "status": "enrolled",
            }
        )

    def send_confirmation_sms(self, args: SendConfirmationSmsArgs) -> ToolResult:
        enrollment = next(
            (
                item
                for item in self.db.enrollments
                if item.enrollment_id == args.enrollment_id
            ),
            None,
        )
        if enrollment is None:
            return ToolResult.failure(
                "enrollment_not_found",
                "A successful enrollment is required before sending confirmation.",
            )
        if not self.session.is_verified(enrollment.phone_number_id):
            return self._verification_required()
        if any(
            item.enrollment_id == enrollment.enrollment_id
            for item in self.db.sms_records
        ):
            return ToolResult.failure(
                "confirmation_already_sent",
                "A confirmation SMS was already recorded for this enrollment.",
            )

        sms = SmsRecord(
            sms_id=self._next_id("SMS", {item.sms_id for item in self.db.sms_records}),
            enrollment_id=enrollment.enrollment_id,
            phone_number_id=enrollment.phone_number_id,
            status=SmsStatus.SIMULATED_SENT,
        )
        self.db.sms_records.append(sms)
        return ToolResult.success(
            {
                "sms_id": sms.sms_id,
                "enrollment_id": sms.enrollment_id,
                "status": sms.status.value,
            }
        )

    def register_do_not_call(self, args: RegisterDoNotCallArgs) -> ToolResult:
        line = self._find_phone(args.phone_number_id)
        if line is None:
            return ToolResult.failure(
                "account_not_found", "The requested account is not available."
            )

        existing = self._do_not_call_for_customer(line.owner_customer_id)
        if existing is not None:
            return ToolResult.success(
                {
                    "record_id": existing.record_id,
                    "phone_number_id": existing.phone_number_id,
                    "status": "registered",
                    "already_registered": True,
                }
            )

        record = DoNotCallRecord(
            record_id=self._next_id(
                "DNC", {item.record_id for item in self.db.do_not_call_records}
            ),
            customer_id=line.owner_customer_id,
            phone_number_id=line.phone_number_id,
        )
        self.db.do_not_call_records.append(record)
        return ToolResult.success(
            {
                "record_id": record.record_id,
                "phone_number_id": record.phone_number_id,
                "status": "registered",
                "already_registered": False,
            }
        )

    def record_simulator_consent(
        self,
        phone_number_id: str,
        offer_id: str,
    ) -> ConsentRecord | None:
        """Record runner-verified simulator consent.

        This is intentionally not a registered business tool. The runner calls
        it after the user simulator emits explicit consent, so an agent cannot
        supply consent by itself.
        """
        line = self._find_phone(phone_number_id)
        if line is None or self._find_offer(offer_id) is None:
            return None
        if self._do_not_call_for_customer(line.owner_customer_id) is not None:
            return None

        existing = self._find_consent(phone_number_id, offer_id)
        if existing is not None:
            return existing

        consent = ConsentRecord(
            consent_id=self._next_id(
                "CONSENT", {item.consent_id for item in self.db.consent_records}
            ),
            customer_id=line.owner_customer_id,
            phone_number_id=phone_number_id,
            offer_id=offer_id,
            source=ConsentSource.USER_SIMULATOR,
        )
        self.db.consent_records.append(consent)
        return consent

    def _find_customer(self, customer_id: str) -> Customer | None:
        return next(
            (
                customer
                for customer in self.db.customers
                if customer.customer_id == customer_id
            ),
            None,
        )

    def _find_phone(self, phone_number_id: str) -> PhoneNumber | None:
        return next(
            (
                phone
                for phone in self.db.phone_numbers
                if phone.phone_number_id == phone_number_id
            ),
            None,
        )

    def _find_plan(self, plan_id: str) -> Plan | None:
        return next(
            (plan for plan in self.db.plans if plan.plan_id == plan_id),
            None,
        )

    def _find_offer(self, offer_id: str) -> Offer | None:
        return next(
            (offer for offer in self.db.offers if offer.offer_id == offer_id),
            None,
        )

    def _find_consent(
        self, phone_number_id: str, offer_id: str
    ) -> ConsentRecord | None:
        return next(
            (
                consent
                for consent in self.db.consent_records
                if consent.phone_number_id == phone_number_id
                and consent.offer_id == offer_id
                and consent.source is ConsentSource.USER_SIMULATOR
            ),
            None,
        )

    def _find_enrollment(
        self, phone_number_id: str, offer_id: str
    ) -> Enrollment | None:
        return next(
            (
                enrollment
                for enrollment in self.db.enrollments
                if enrollment.phone_number_id == phone_number_id
                and enrollment.offer_id == offer_id
            ),
            None,
        )

    def _do_not_call_for_customer(self, customer_id: str) -> DoNotCallRecord | None:
        return next(
            (
                record
                for record in self.db.do_not_call_records
                if record.customer_id == customer_id
            ),
            None,
        )

    @staticmethod
    def _eligibility_reasons(line: PhoneNumber, offer: Offer) -> list[str]:
        reasons: list[str] = []
        if not offer.active:
            reasons.append("offer_inactive")
        if line.status is not PhoneStatus.ACTIVE:
            reasons.append("line_not_active")
        if line.current_plan_id not in offer.eligible_plan_ids:
            reasons.append("plan_not_eligible")
        return reasons

    @staticmethod
    def _offer_data(offer: Offer) -> dict[str, Any]:
        return {
            "offer_id": offer.offer_id,
            "name": offer.name,
            "active": offer.active,
            "monthly_fee": offer.monthly_fee,
            "currency": offer.currency,
            "validity_months": offer.validity_months,
            "auto_renewal": offer.auto_renewal,
            "restrictions": offer.restrictions,
            "eligible_plan_ids": list(offer.eligible_plan_ids),
        }

    @staticmethod
    def _verification_required() -> ToolResult:
        return ToolResult.failure(
            "identity_verification_required",
            "Successful identity verification is required for this action.",
        )

    @staticmethod
    def _verification_failed() -> ToolResult:
        return ToolResult.failure(
            "identity_verification_failed",
            "Identity verification failed.",
        )

    @staticmethod
    def _next_id(prefix: str, existing: set[str]) -> str:
        number = 1
        while f"{prefix}-{number:04d}" in existing:
            number += 1
        return f"{prefix}-{number:04d}"
