"""Composable business toolkits for the multi-domain benchmark.

The original seven marketing tools stay in :mod:`cm_outbound_bench.domain.tools`
untouched. Domain-specific tools live here and are composed into a single
toolkit per episode, sharing one identity-verification session and one synthetic
database.

Consent boundary: no business tool can create consent. Offer enrollment keeps
using the original runner-only ``record_simulator_consent``; every other
irreversible action requires a runner-recorded ``ActionConsentRecord``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, ValidationError

from cm_outbound_bench.domain.data_model import (
    ActionConsentRecord,
    BillingAccount,
    CancellationRequest,
    CmOutboundDB,
    DomainModel,
    IdentityRecord,
    Notification,
    Plan,
    PlanAllowance,
    PlanChangeRequest,
    Recharge,
    Subscription,
)
from cm_outbound_bench.domain.tools import (
    CmOutboundTools,
    ToolDefinition,
    ToolResult,
    ToolSession,
)

# ---------------------------------------------------------------------------
# Argument models
# ---------------------------------------------------------------------------


class NoArgs(DomainModel):
    pass


class PlanArgs(DomainModel):
    plan_id: str = Field(description="Synthetic plan identifier.")


class ComparePlansArgs(DomainModel):
    plan_id_a: str = Field(description="First synthetic plan identifier.")
    plan_id_b: str = Field(description="Second synthetic plan identifier.")


class LineArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")


class ChargesArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    billing_period: str | None = Field(
        default=None,
        description="Optional billing period in YYYY-MM form.",
    )


class RechargeArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    amount_cents: int = Field(
        gt=0,
        le=100_000,
        description=(
            "Top-up amount in cents. It must be the exact amount the customer "
            "explicitly authorized."
        ),
    )
    operation_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional caller-supplied operation identifier. Repeating the same "
            "identifier does not create a second top-up."
        ),
    )


class PlanChangeCheckArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    to_plan_id: str = Field(description="Requested target plan identifier.")


class PlanChangeArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    to_plan_id: str = Field(description="Requested target plan identifier.")


class CancellationArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    target_type: str = Field(description="One of PLAN, OFFER or SERVICE.")
    target_id: str = Field(description="Identifier of the object to cancel.")
    operation_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional caller-supplied operation identifier. Repeating the same "
            "identifier does not create a second cancellation request."
        ),
    )


class NotificationArgs(DomainModel):
    phone_number_id: str = Field(description="Target synthetic line identifier.")
    kind: str = Field(description="CONFIRMATION, REMINDER, FOLLOW_UP or INFO.")
    related_ref: str = Field(
        min_length=1,
        description="Opaque reference for the business object being confirmed.",
    )
    operation_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional caller-supplied operation identifier. Repeating the same "
            "identifier does not create a second notification."
        ),
    )


class NotificationStatusArgs(DomainModel):
    notification_id: str = Field(description="Synthetic notification identifier.")


# ---------------------------------------------------------------------------
# Domain toolkit
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, tuple[type[DomainModel], str]] = {
    "list_plans": (
        NoArgs,
        "List the synthetic mobile plans with their monthly fee and allowances.",
    ),
    "get_plan_details": (
        PlanArgs,
        "Return one plan's fee, allowance and package contents.",
    ),
    "compare_plans": (
        ComparePlansArgs,
        "Compare two plans' fees and allowances side by side.",
    ),
    "get_billing_summary": (
        LineArgs,
        "Return balance, arrears and current-period charges for a verified line.",
    ),
    "list_charges": (
        ChargesArgs,
        "List charge lines for a verified line, optionally filtered by period.",
    ),
    "recharge_account": (
        RechargeArgs,
        "Simulate a prepaid top-up for a verified line. This changes billing "
        "state. Call it only after the customer has explicitly authorized this "
        "exact top-up, amount and target line AND the benchmark runner has "
        "recorded that consent. A stated intention to recharge, an amount "
        "confirmation on its own (for example the customer replies "
        "'对，50 元，你操作前跟我确认一下金额。'), or a request to confirm the "
        "amount before the operation is not authorization: ask for explicit "
        "final authorization and wait instead.",
    ),
    "get_subscription_status": (
        LineArgs,
        "Return the subscription status, plan and commitment end date for a "
        "verified line.",
    ),
    "check_plan_change_eligibility": (
        PlanChangeCheckArgs,
        "Check whether a verified line can move to another plan.",
    ),
    "submit_plan_change": (
        PlanChangeArgs,
        "Request an upgrade or downgrade for a verified, eligible line. This "
        "changes the subscription. Call it only after the customer has "
        "explicitly authorized this exact change, including the target plan and "
        "the target line, AND the benchmark runner has recorded that consent. A "
        "stated intention, a question about the plans, a generic affirmative "
        "reply or a request to confirm the details first is not authorization.",
    ),
    "submit_cancellation": (
        CancellationArgs,
        "Record a cancellation request for a verified line. This changes "
        "business state. Call it only after the customer has explicitly "
        "authorized cancelling this exact target AND the benchmark runner has "
        "recorded that consent. A stated intention, a question about the "
        "cancellation terms or a request to confirm the target first is not "
        "authorization.",
    ),
    "get_identity_status": (
        LineArgs,
        "Return the real-name registration state of a verified line.",
    ),
    "check_ownership_transfer_conditions": (
        LineArgs,
        "Return the conditions that must hold before ownership transfer.",
    ),
    "send_notification": (
        NotificationArgs,
        "Record a simulated notification for a verified line. No real message "
        "is sent.",
    ),
    "get_notification_status": (
        NotificationStatusArgs,
        "Return the simulated delivery status of a notification.",
    ),
}

_MUTATING_TOOLS = {
    "recharge_account": "RECHARGE",
    "submit_plan_change": "PLAN_CHANGE",
    "submit_cancellation": "CANCELLATION",
}

#: Tools in this registry whose success changes persistent business state. The
#: LLM agent must report their outcome to the customer before finishing. A
#: notification is state-changing but does not require customer consent.
_STATE_CHANGING_TOOLS: frozenset[str] = frozenset(_MUTATING_TOOLS) | {
    "send_notification"
}

#: Synthetic effective date used by every D04 request in this benchmark. It is a
#: transparently synthetic assumption, not an observed business rule.
DEFAULT_EFFECTIVE_ON = "2026-12-01"

#: Every business tool name the runtime exposes. Kept next to the registries so
#: static task validation does not need to build a toolkit.
LEGACY_TOOL_NAMES: tuple[str, ...] = (
    "verify_customer_identity",
    "get_customer_account",
    "get_offer_details",
    "check_offer_eligibility",
    "enroll_offer",
    "send_confirmation_sms",
    "register_do_not_call",
)
DOMAIN_TOOL_NAMES: tuple[str, ...] = tuple(_TOOL_REGISTRY)
TASK_TOOLS: frozenset[str] = frozenset(LEGACY_TOOL_NAMES) | frozenset(
    DOMAIN_TOOL_NAMES
)


@dataclass
class DomainToolkit:
    """D01, D03, D04, D05 and D06 business operations on one synthetic database."""

    db: CmOutboundDB
    session: ToolSession
    #: Episode-scoped operation-id memory for explicit idempotent retries.
    _operations: dict[tuple[str, str], dict[str, Any]] = field(
        default_factory=dict, init=False
    )

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
        entry = _TOOL_REGISTRY.get(name)
        if entry is None:
            return ToolResult.failure(
                "unknown_tool", f"Tool {name!r} is not available."
            )
        args_model, _ = entry
        try:
            parsed = args_model.model_validate(arguments)
        except ValidationError:
            return ToolResult.failure(
                "invalid_arguments",
                f"Arguments do not match the schema for tool {name!r}.",
            )
        handler = getattr(self, name)
        result = handler(parsed)
        if not isinstance(result, ToolResult):
            raise TypeError(f"Tool {name!r} returned an invalid result")
        return result

    # -- D01 ---------------------------------------------------------------

    def list_plans(self, args: NoArgs) -> ToolResult:
        allowances = {item.plan_id: item for item in self.db.plan_allowances}
        return ToolResult.success(
            {
                "plans": [
                    self._plan_payload(plan, allowances.get(plan.plan_id))
                    for plan in self.db.plans
                ]
            }
        )

    def get_plan_details(self, args: PlanArgs) -> ToolResult:
        plan = self._find_plan(args.plan_id)
        if plan is None:
            return ToolResult.failure(
                "plan_not_found", "The requested plan is not available."
            )
        allowance = next(
            (
                item
                for item in self.db.plan_allowances
                if item.plan_id == plan.plan_id
            ),
            None,
        )
        return ToolResult.success(self._plan_payload(plan, allowance))

    def compare_plans(self, args: ComparePlansArgs) -> ToolResult:
        plans = []
        for plan_id in (args.plan_id_a, args.plan_id_b):
            plan = self._find_plan(plan_id)
            if plan is None:
                return ToolResult.failure(
                    "plan_not_found", "The requested plan is not available."
                )
            allowance = next(
                (
                    item
                    for item in self.db.plan_allowances
                    if item.plan_id == plan_id
                ),
                None,
            )
            plans.append(self._plan_payload(plan, allowance))
        fee_delta = plans[1]["monthly_fee"] - plans[0]["monthly_fee"]
        return ToolResult.success(
            {
                "plans": plans,
                "monthly_fee_delta": round(fee_delta, 2),
                "currency": "CNY",
            }
        )

    # -- D03 ---------------------------------------------------------------

    def get_billing_summary(self, args: LineArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        account = self._find_account(args.phone_number_id)
        if account is None:
            return ToolResult.failure(
                "account_not_found", "There is no billing account for this line."
            )
        charges = [
            item for item in self.db.charges if item.account_id == account.account_id
        ]
        return ToolResult.success(
            {
                "account_id": account.account_id,
                "phone_number_id": account.phone_number_id,
                "balance_cents": account.balance_cents,
                "arrears_cents": account.arrears_cents,
                "currency": account.currency,
                "charge_count": len(charges),
                "unpaid_cents": sum(
                    item.amount_cents
                    for item in charges
                    if item.status in {"BILLED", "DISPUTED"}
                ),
            }
        )

    def list_charges(self, args: ChargesArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        account = self._find_account(args.phone_number_id)
        if account is None:
            return ToolResult.failure(
                "account_not_found", "There is no billing account for this line."
            )
        charges = [
            item
            for item in self.db.charges
            if item.account_id == account.account_id
            and (
                args.billing_period is None
                or item.billing_period == args.billing_period
            )
        ]
        return ToolResult.success(
            {
                "account_id": account.account_id,
                "currency": account.currency,
                "charges": [
                    {
                        "charge_id": item.charge_id,
                        "description": item.description,
                        "amount_cents": item.amount_cents,
                        "billing_period": item.billing_period,
                        "status": item.status,
                    }
                    for item in charges
                ],
            }
        )

    def recharge_account(self, args: RechargeArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        account = self._find_account(args.phone_number_id)
        if account is None:
            return ToolResult.failure(
                "account_not_found", "There is no billing account for this line."
            )
        if not self._has_action_consent(
            args.phone_number_id, "RECHARGE", account.account_id
        ):
            return self._consent_required()

        replay = self._operation_replay("recharge_account", args.operation_id)
        if replay is not None:
            return replay

        recharge = Recharge(
            recharge_id=self._next_id(
                "RCG", {item.recharge_id for item in self.db.recharges}
            ),
            account_id=account.account_id,
            amount_cents=args.amount_cents,
            status="COMPLETED",
        )
        self.db.recharges.append(recharge)
        account.balance_cents += args.amount_cents
        payload = {
            "recharge_id": recharge.recharge_id,
            "account_id": recharge.account_id,
            "amount_cents": recharge.amount_cents,
            "status": recharge.status,
            "balance_cents": account.balance_cents,
        }
        self._remember_operation("recharge_account", args.operation_id, payload)
        return ToolResult.success(payload)

    # -- D04 ---------------------------------------------------------------

    def get_subscription_status(self, args: LineArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        subscription = self._find_subscription(args.phone_number_id)
        if subscription is None:
            return ToolResult.failure(
                "subscription_not_found",
                "There is no subscription record for this line.",
            )
        plan = self._find_plan(subscription.plan_id)
        return ToolResult.success(
            {
                "subscription_id": subscription.subscription_id,
                "phone_number_id": subscription.phone_number_id,
                "plan_id": subscription.plan_id,
                "plan_name": plan.name if plan is not None else "",
                "status": subscription.status,
                "started_on": subscription.started_on,
                "contract_end_on": subscription.contract_end_on,
            }
        )

    def check_plan_change_eligibility(self, args: PlanChangeCheckArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        reasons = self._plan_change_reasons(args.phone_number_id, args.to_plan_id)
        return ToolResult.success(
            {
                "phone_number_id": args.phone_number_id,
                "to_plan_id": args.to_plan_id,
                "eligible": not reasons,
                "reasons": reasons,
            }
        )

    def submit_plan_change(self, args: PlanChangeArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        reasons = self._plan_change_reasons(args.phone_number_id, args.to_plan_id)
        if reasons:
            return ToolResult.failure(
                "plan_change_not_allowed",
                "This plan change is not permitted: " + ", ".join(reasons) + ".",
            )
        if not self._has_action_consent(
            args.phone_number_id, "PLAN_CHANGE", args.to_plan_id
        ):
            return self._consent_required()

        subscription = self._find_subscription(args.phone_number_id)
        line = self._find_line(args.phone_number_id)
        if subscription is None or line is None:
            return ToolResult.failure(
                "subscription_not_found",
                "There is no subscription record for this line.",
            )
        request = PlanChangeRequest(
            request_id=self._next_id(
                "PCR", {item.request_id for item in self.db.plan_change_requests}
            ),
            phone_number_id=args.phone_number_id,
            from_plan_id=subscription.plan_id,
            to_plan_id=args.to_plan_id,
            status="APPROVED",
            effective_on=DEFAULT_EFFECTIVE_ON,
        )
        self.db.plan_change_requests.append(request)
        subscription.plan_id = args.to_plan_id
        line.current_plan_id = args.to_plan_id
        return ToolResult.success(
            {
                "request_id": request.request_id,
                "phone_number_id": request.phone_number_id,
                "from_plan_id": request.from_plan_id,
                "to_plan_id": request.to_plan_id,
                "status": request.status,
                "effective_on": request.effective_on,
            }
        )

    def submit_cancellation(self, args: CancellationArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        target_type = args.target_type.upper()
        if target_type not in {"PLAN", "OFFER", "SERVICE"}:
            return ToolResult.failure(
                "invalid_target_type",
                "target_type must be PLAN, OFFER or SERVICE.",
            )
        if not self._has_action_consent(
            args.phone_number_id, "CANCELLATION", args.target_id
        ):
            return self._consent_required()

        replay = self._operation_replay("submit_cancellation", args.operation_id)
        if replay is not None:
            return replay

        existing = next(
            (
                item
                for item in self.db.cancellation_requests
                if item.phone_number_id == args.phone_number_id
                and item.target_type == target_type
                and item.target_id == args.target_id
            ),
            None,
        )
        if existing is not None:
            payload = {
                "request_id": existing.request_id,
                "phone_number_id": existing.phone_number_id,
                "target_type": existing.target_type,
                "target_id": existing.target_id,
                "status": existing.status,
                "effective_on": existing.effective_on,
                "already_requested": True,
            }
            self._remember_operation(
                "submit_cancellation", args.operation_id, payload
            )
            return ToolResult.success(payload)

        request = CancellationRequest(
            request_id=self._next_id(
                "CCR", {item.request_id for item in self.db.cancellation_requests}
            ),
            phone_number_id=args.phone_number_id,
            target_type=target_type,
            target_id=args.target_id,
            status="APPROVED",
            effective_on=DEFAULT_EFFECTIVE_ON,
        )
        self.db.cancellation_requests.append(request)
        if target_type == "OFFER":
            removed = {
                item.enrollment_id
                for item in self.db.enrollments
                if item.phone_number_id == args.phone_number_id
                and item.offer_id == args.target_id
            }
            self.db.enrollments = [
                item
                for item in self.db.enrollments
                if item.enrollment_id not in removed
            ]
            self.db.sms_records = [
                item
                for item in self.db.sms_records
                if item.enrollment_id not in removed
            ]
        payload = {
            "request_id": request.request_id,
            "phone_number_id": request.phone_number_id,
            "target_type": request.target_type,
            "target_id": request.target_id,
            "status": request.status,
            "effective_on": request.effective_on,
            "already_requested": False,
        }
        self._remember_operation("submit_cancellation", args.operation_id, payload)
        return ToolResult.success(payload)

    # -- D05 ---------------------------------------------------------------

    def get_identity_status(self, args: LineArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        identity = self._find_identity(args.phone_number_id)
        if identity is None:
            return ToolResult.failure(
                "identity_record_not_found",
                "There is no real-name record for this line.",
            )
        return ToolResult.success(
            {
                "phone_number_id": identity.phone_number_id,
                "real_name_status": identity.real_name_status,
                "registration_type": identity.registration_type,
            }
        )

    def check_ownership_transfer_conditions(self, args: LineArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        blocked: list[str] = []
        identity = self._find_identity(args.phone_number_id)
        if identity is None or identity.real_name_status != "VERIFIED":
            blocked.append("real_name_not_verified")
        account = self._find_account(args.phone_number_id)
        if account is not None and account.arrears_cents > 0:
            blocked.append("arrears_outstanding")
        subscription = self._find_subscription(args.phone_number_id)
        if subscription is not None and subscription.contract_end_on is not None:
            blocked.append("active_commitment")
        return ToolResult.success(
            {
                "phone_number_id": args.phone_number_id,
                "transferable": not blocked,
                "blocking_conditions": blocked,
            }
        )

    # -- D06 ---------------------------------------------------------------

    def send_notification(self, args: NotificationArgs) -> ToolResult:
        if not self.session.is_verified(args.phone_number_id):
            return self._verification_required()
        kind = args.kind.upper()
        if kind not in {"CONFIRMATION", "REMINDER", "FOLLOW_UP", "INFO"}:
            return ToolResult.failure(
                "invalid_notification_kind",
                "kind must be CONFIRMATION, REMINDER, FOLLOW_UP or INFO.",
            )
        replay = self._operation_replay("send_notification", args.operation_id)
        if replay is not None:
            return replay
        duplicate = next(
            (
                item
                for item in self.db.notifications
                if item.phone_number_id == args.phone_number_id
                and item.kind == kind
                and item.related_ref == args.related_ref
            ),
            None,
        )
        if duplicate is not None:
            return ToolResult.failure(
                "notification_already_sent",
                "A notification of this kind already exists for this business "
                "object; a duplicate was not created.",
            )
        notification = Notification(
            notification_id=self._next_id(
                "NTF", {item.notification_id for item in self.db.notifications}
            ),
            phone_number_id=args.phone_number_id,
            kind=kind,
            status="SENT",
            related_ref=args.related_ref,
        )
        self.db.notifications.append(notification)
        payload = {
            "notification_id": notification.notification_id,
            "phone_number_id": notification.phone_number_id,
            "kind": notification.kind,
            "channel": notification.channel,
            "status": notification.status,
        }
        self._remember_operation("send_notification", args.operation_id, payload)
        return ToolResult.success(payload)

    def get_notification_status(self, args: NotificationStatusArgs) -> ToolResult:
        notification = next(
            (
                item
                for item in self.db.notifications
                if item.notification_id == args.notification_id
            ),
            None,
        )
        if notification is None:
            return ToolResult.failure(
                "notification_not_found", "The notification is not available."
            )
        if not self.session.is_verified(notification.phone_number_id):
            return self._verification_required()
        return ToolResult.success(
            {
                "notification_id": notification.notification_id,
                "phone_number_id": notification.phone_number_id,
                "kind": notification.kind,
                "status": notification.status,
                "related_ref": notification.related_ref,
            }
        )

    # -- runner-only consent ----------------------------------------------

    def record_action_consent(
        self,
        phone_number_id: str,
        action: str,
        target_ref: str,
    ) -> ActionConsentRecord | None:
        """Record runner-verified consent for a non-offer action.

        Deliberately not a business tool: only the runner may call this after the
        user simulator has explicitly agreed.
        """
        line = self._find_line(phone_number_id)
        if line is None:
            return None
        if action not in {"PLAN_CHANGE", "CANCELLATION", "RECHARGE", "OWNERSHIP_TRANSFER"}:
            return None
        existing = next(
            (
                item
                for item in self.db.action_consents
                if item.phone_number_id == phone_number_id
                and item.action == action
                and item.target_ref == target_ref
            ),
            None,
        )
        if existing is not None:
            return existing
        consent = ActionConsentRecord(
            consent_id=self._next_id(
                "ACONSENT", {item.consent_id for item in self.db.action_consents}
            ),
            customer_id=line.owner_customer_id,
            phone_number_id=phone_number_id,
            action=action,
            target_ref=target_ref,
        )
        self.db.action_consents.append(consent)
        return consent

    # -- internals ---------------------------------------------------------

    def _find_line(self, phone_number_id: str):
        return next(
            (
                item
                for item in self.db.phone_numbers
                if item.phone_number_id == phone_number_id
            ),
            None,
        )

    def _find_plan(self, plan_id: str) -> Plan | None:
        return next(
            (item for item in self.db.plans if item.plan_id == plan_id), None
        )

    def _find_account(self, phone_number_id: str) -> BillingAccount | None:
        return next(
            (
                item
                for item in self.db.billing_accounts
                if item.phone_number_id == phone_number_id
            ),
            None,
        )

    def _find_subscription(self, phone_number_id: str) -> Subscription | None:
        return next(
            (
                item
                for item in self.db.subscriptions
                if item.phone_number_id == phone_number_id
                and item.status != "CLOSED"
            ),
            None,
        )

    def _find_identity(self, phone_number_id: str) -> IdentityRecord | None:
        return next(
            (
                item
                for item in self.db.identity_records
                if item.phone_number_id == phone_number_id
            ),
            None,
        )

    def _has_action_consent(
        self, phone_number_id: str, action: str, target_ref: str
    ) -> bool:
        return any(
            item.phone_number_id == phone_number_id
            and item.action == action
            and item.target_ref == target_ref
            for item in self.db.action_consents
        )

    def _plan_change_reasons(
        self, phone_number_id: str, to_plan_id: str
    ) -> list[str]:
        reasons: list[str] = []
        line = self._find_line(phone_number_id)
        subscription = self._find_subscription(phone_number_id)
        plan = self._find_plan(to_plan_id)
        if line is None or subscription is None:
            return ["subscription_not_found"]
        if plan is None:
            return ["target_plan_not_found"]
        if line.status != "active":
            reasons.append("line_not_active")
        if subscription.status != "ACTIVE":
            reasons.append("subscription_not_active")
        if subscription.contract_end_on is not None:
            reasons.append("active_commitment")
        if subscription.plan_id == to_plan_id:
            reasons.append("already_on_plan")
        return reasons

    @staticmethod
    def _plan_payload(plan: Plan, allowance: PlanAllowance | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "name": plan.name,
            "monthly_fee": plan.monthly_fee,
            "currency": plan.currency,
        }
        if allowance is not None:
            payload["allowance"] = {
                "data_mb": allowance.data_mb,
                "voice_minutes": allowance.voice_minutes,
                "sms_count": allowance.sms_count,
                "note": allowance.note,
            }
        else:
            payload["allowance"] = None
        return payload

    @staticmethod
    def _next_id(prefix: str, existing: set[str]) -> str:
        number = 1
        while f"{prefix}-{number:04d}" in existing:
            number += 1
        return f"{prefix}-{number:04d}"

    def _operation_replay(
        self, tool_name: str, operation_id: str | None
    ) -> ToolResult | None:
        """Return the recorded result of an already-applied operation id."""
        if not operation_id:
            return None
        stored = self._operations.get((tool_name, operation_id))
        if stored is None:
            return None
        payload = dict(stored)
        payload["idempotent_replay"] = True
        return ToolResult.success(payload)

    def _remember_operation(
        self,
        tool_name: str,
        operation_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        if not operation_id:
            return
        self._operations[(tool_name, operation_id)] = dict(payload)

    @staticmethod
    def _verification_required() -> ToolResult:
        return ToolResult.failure(
            "identity_verification_required",
            "Successful identity verification is required for this action.",
        )

    @staticmethod
    def _consent_required() -> ToolResult:
        return ToolResult.failure(
            "consent_required",
            "Explicit customer consent recorded by the benchmark runner is required.",
        )


@dataclass
class CompositeToolkit:
    """One tool surface per episode: legacy marketing tools plus domain tools."""

    legacy: CmOutboundTools
    domains: DomainToolkit
    _definitions: list[ToolDefinition] = field(default_factory=list, init=False)
    _owners: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        definitions: list[ToolDefinition] = []
        owners: dict[str, str] = {}
        for toolkit_name, toolkit in (
            ("legacy", self.legacy),
            ("domains", self.domains),
        ):
            for definition in toolkit.list_tools():
                if definition.name in owners:
                    raise ValueError(
                        f"Duplicate tool name across toolkits: {definition.name}"
                    )
                owners[definition.name] = toolkit_name
                definitions.append(definition)
        self._definitions.extend(definitions)
        self._owners.update(owners)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._definitions)

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        owner = self._owners.get(name)
        if owner is None:
            return ToolResult.failure(
                "unknown_tool", f"Tool {name!r} is not available."
            )
        toolkit = self.legacy if owner == "legacy" else self.domains
        return toolkit.execute(name, arguments)

    def record_simulator_consent(self, phone_number_id: str, offer_id: str):
        return self.legacy.record_simulator_consent(phone_number_id, offer_id)

    def record_action_consent(
        self, phone_number_id: str, action: str, target_ref: str
    ):
        return self.domains.record_action_consent(
            phone_number_id, action, target_ref
        )


def build_toolkit(db: CmOutboundDB) -> CompositeToolkit:
    """Build the complete tool surface for one isolated episode."""
    session = ToolSession()
    legacy = CmOutboundTools(db, session=session)
    domains = DomainToolkit(db=db, session=session)
    return CompositeToolkit(legacy=legacy, domains=domains)


def tool_surface_hash(allowed_tools: Iterable[str]) -> str:
    """Return the SHA-256 of the episode's declared initial tool surface.

    The manifest records this as ``initial_tools_hash``. It is a content hash of
    the tool names, descriptions, JSON parameter schemas and mutating flags the
    agent can actually see for this task, filtered by the task allow-list and
    sorted by name. It is therefore stable for an unchanged tool registry and
    changes when a tool schema or description changes.

    A name in the allow-list that the registry does not define is still hashed,
    as ``available: False``, so an unknown-tool defect can never hide behind an
    identical hash.
    """
    definitions = {item.name: item for item in build_toolkit(CmOutboundDB()).list_tools()}
    payload = [
        definitions[name].model_dump(mode="json")
        if name in definitions
        else {"name": name, "available": False}
        for name in sorted(set(allowed_tools))
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CompositeToolkit",
    "DOMAIN_TOOL_NAMES",
    "DomainToolkit",
    "LEGACY_TOOL_NAMES",
    "TASK_TOOLS",
    "build_toolkit",
    "tool_surface_hash",
]
