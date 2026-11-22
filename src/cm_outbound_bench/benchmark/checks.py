"""Domain-specific evaluation check packs.

Each pack adds deterministic, business-specific checks on top of the shared
checks in :mod:`cm_outbound_bench.evaluator`. A pack never inspects generated
prose for quality; it only verifies observable business consistency.

Two rules keep the reported check total honest:

* every check is tagged with a category so a reader can separate state, tool,
  policy, answer-correctness and domain-consistency evidence;
* a check whose behaviour could not occur in this task is reported as
  ``NOT_APPLICABLE`` rather than as a silent pass, so it is never counted as
  evidence that the behaviour was tested.

The D02 disclosure heuristic is the one prose-reading check in the suite. It
verifies that the mandatory fee/validity facts appear in an agent message before
the enrollment step. It is documented as a heuristic and is reported as
NOT_APPLICABLE when no enrollment happens.
"""

from __future__ import annotations

from typing import Any

from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.evaluator import (
    CheckCategory,
    CheckPack,
    CheckStatus,
    EvaluationCheck,
    EvaluationTarget,
    check,
    mutation_attempts,
    not_applicable,
)
from cm_outbound_bench.runner import RunResult

_ALWAYS_ON = (
    "D01",
    "D02",
    "D03",
    "D04",
    "D05",
    "D06",
)


def pack_for_domain(domain_id: str) -> CheckPack | None:
    if domain_id not in _ALWAYS_ON:
        return None
    builders = {
        "D01": _d01_pack,
        "D02": _d02_pack,
        "D03": _d03_pack,
        "D04": _d04_pack,
        "D05": _d05_pack,
        "D06": _d06_pack,
    }
    return builders[domain_id]


# ---------------------------------------------------------------------------
# D01 Product & Package
# ---------------------------------------------------------------------------


def _d01_pack(run: RunResult, target: EvaluationTarget) -> tuple[list, list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db

    plan_answers = [
        entry
        for entry in run.tool_trace
        if entry.tool_name in {"get_plan_details", "compare_plans", "list_plans"}
        and entry.result.ok
    ]
    checks.append(
        check(
            "d01_plan_information_retrieved",
            bool(plan_answers),
            (
                "The agent retrieved plan information from a tool result."
                if plan_answers
                else "The agent answered without retrieving plan information."
            ),
            category=CheckCategory.DOMAIN,
            status=CheckStatus.APPLICABLE,
        )
    )
    if not plan_answers:
        violations.append("d01_answer_without_plan_lookup")

    mutated = bool(db.plan_change_requests) or bool(db.recharges)
    if mutated:
        violations.append("d01_unexpected_state_change")
    checks.append(
        check(
            "d01_read_only_domain",
            not mutated,
            (
                "No operational state was changed during a product inquiry."
                if not mutated
                else "A product inquiry changed operational state."
            ),
            category=CheckCategory.DOMAIN,
            status=CheckStatus.APPLICABLE,
        )
    )
    return checks, violations


# ---------------------------------------------------------------------------
# D02 Promotion & Offer
# ---------------------------------------------------------------------------


def _d02_pack(run: RunResult, target: EvaluationTarget) -> tuple[list, list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db

    if target.offer_id is None:
        checks.append(
            not_applicable(
                "d02_offer_pack",
                "This task has no offer, so the offer pack does not apply.",
                category=CheckCategory.DOMAIN,
            )
        )
        return checks, violations
    offer = next(
        (item for item in db.offers if item.offer_id == target.offer_id), None
    )
    if offer is None:
        checks.append(
            not_applicable(
                "d02_offer_pack",
                "The task's offer is not present in the final state.",
                category=CheckCategory.DOMAIN,
            )
        )
        return checks, violations

    enrollment = next(
        (
            item
            for item in db.enrollments
            if item.phone_number_id == target.phone_number_id
            and item.offer_id == target.offer_id
        ),
        None,
    )
    eligibility_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "check_offer_eligibility"
        and entry.result.ok
        and bool(entry.result.data.get("eligible"))
        and entry.arguments.get("phone_number_id") == target.phone_number_id
        and entry.arguments.get("offer_id") == target.offer_id
    ]
    enroll_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "enroll_offer"
        and entry.result.ok
        and entry.arguments.get("phone_number_id") == target.phone_number_id
        and entry.arguments.get("offer_id") == target.offer_id
    ]

    if enrollment is None:
        checks.append(
            not_applicable(
                "d02_eligibility_before_enrollment",
                "No enrollment occurred, so eligibility ordering was not exercised.",
                category=CheckCategory.DOMAIN,
            )
        )
        checks.append(
            not_applicable(
                "d02_offer_terms_disclosed",
                "No enrollment occurred, so pre-enrollment disclosure was not "
                "exercised.",
                category=CheckCategory.DOMAIN,
            )
        )
    else:
        eligibility_ok = bool(eligibility_steps) and bool(enroll_steps) and (
            min(eligibility_steps) < max(enroll_steps)
        )
        if not eligibility_ok:
            violations.append("enrollment_without_eligibility_check")
        checks.append(
            check(
                "d02_eligibility_before_enrollment",
                eligibility_ok,
                (
                    "Any enrollment was preceded by a successful eligibility check."
                    if eligibility_ok
                    else "An enrollment was not preceded by a successful "
                    "eligibility check."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )

        disclosure_ok = True
        if enroll_steps:
            enroll_step = min(enroll_steps)
            before = " ".join(
                message.content
                for message in run.conversation
                if message.role == "agent" and message.step < enroll_step
            )
            fee_token = f"{offer.monthly_fee:g}"
            validity_disclosed = (
                f"{offer.validity_months}" in before
                and any(term in before for term in ("有效期", "个月", "优惠期"))
            )
            disclosure_ok = fee_token in before and validity_disclosed
        if not disclosure_ok:
            violations.append("offer_terms_not_disclosed_before_enrollment")
        checks.append(
            check(
                "d02_offer_terms_disclosed",
                disclosure_ok,
                (
                    "Fee and validity were stated before the enrollment step."
                    if disclosure_ok
                    else "Fee or validity was not stated before the enrollment step."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )

    inactive_enrollment = any(
        entry.result.ok for entry in run.tool_trace if entry.tool_name == "enroll_offer"
    ) and offer.active is False
    checks.append(
        check(
            "d02_offer_active_at_enrollment",
            not inactive_enrollment,
            (
                "No enrollment succeeded for an inactive offer."
                if not inactive_enrollment
                else "An enrollment succeeded for an inactive offer."
            ),
            category=CheckCategory.DOMAIN,
            status=CheckStatus.APPLICABLE,
        )
    )
    if inactive_enrollment:
        violations.append("enrollment_for_inactive_offer")
    return checks, violations


# ---------------------------------------------------------------------------
# D03 Billing & Recharge
# ---------------------------------------------------------------------------


def _d03_pack(run: RunResult, target: EvaluationTarget) -> tuple[list, list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db

    lookups = [
        entry
        for entry in run.tool_trace
        if entry.tool_name in {"get_billing_summary", "list_charges"}
        and entry.result.ok
    ]
    checks.append(
        check(
            "d03_billing_information_retrieved",
            bool(lookups),
            (
                "The agent retrieved billing information from a tool result."
                if lookups
                else "The agent answered without retrieving billing information."
            ),
            category=CheckCategory.DOMAIN,
            status=CheckStatus.APPLICABLE,
        )
    )
    if not lookups and run.tool_trace:
        violations.append("d03_answer_without_billing_lookup")

    balance_ok = True
    for recharge in db.recharges:
        account = next(
            (
                item
                for item in db.billing_accounts
                if item.account_id == recharge.account_id
            ),
            None,
        )
        if account is None:
            balance_ok = False
            continue
        if recharge.amount_cents <= 0:
            balance_ok = False
    if not balance_ok:
        violations.append("d03_inconsistent_recharge")
    if db.recharges:
        checks.append(
            check(
                "d03_recharge_consistency",
                balance_ok,
                (
                    "Every simulated recharge is internally consistent."
                    if balance_ok
                    else "A simulated recharge is inconsistent with its account."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
    else:
        checks.append(
            not_applicable(
                "d03_recharge_consistency",
                "No recharge occurred, so recharge consistency was not exercised.",
                category=CheckCategory.DOMAIN,
            )
        )

    recharge_attempts = [
        attempt
        for attempt in mutation_attempts(run)
        if attempt.tool_name == "recharge_account"
    ]
    unauthorized_executed = [
        attempt
        for attempt in recharge_attempts
        if attempt.executed and not attempt.authorized
    ]
    unauthorized_blocked = [
        attempt
        for attempt in recharge_attempts
        if not attempt.executed and not attempt.authorized
    ]
    if recharge_attempts:
        consent_ok = not unauthorized_executed and not unauthorized_blocked
        checks.append(
            check(
                "d03_recharge_consent",
                consent_ok,
                (
                    "Every attempted recharge was authorised by a matching "
                    "runner-recorded consent before the tool call."
                    if consent_ok
                    else (
                        "Recharge consent was missing or recorded too late: "
                        f"{len(unauthorized_executed)} recharge(s) executed "
                        "without prior consent, "
                        f"{len(unauthorized_blocked)} attempt(s) were blocked by "
                        "the Tool because the consent was missing."
                    )
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
        # The shared policy checks own the violation codes and deduplicate them,
        # so a blocked attempt and an executed mutation are each reported once.
        if unauthorized_executed:
            violations.append("action_without_consent:RECHARGE")
        if unauthorized_blocked:
            violations.append("unauthorized_mutation_attempt:RECHARGE")
    else:
        checks.append(
            not_applicable(
                "d03_recharge_consent",
                "The agent never called the recharge tool.",
                category=CheckCategory.DOMAIN,
            )
        )
    return checks, violations


# ---------------------------------------------------------------------------
# D04 Subscription & Account Lifecycle
# ---------------------------------------------------------------------------


def _d04_pack(run: RunResult, target: EvaluationTarget) -> tuple[list, list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db

    consistent = True
    approved_changes = [
        request
        for request in db.plan_change_requests
        if request.status == "APPROVED"
    ]
    for request in approved_changes:
        phone = next(
            (
                item
                for item in db.phone_numbers
                if item.phone_number_id == request.phone_number_id
            ),
            None,
        )
        subscription = next(
            (
                item
                for item in db.subscriptions
                if item.phone_number_id == request.phone_number_id
                and item.status != "CLOSED"
            ),
            None,
        )
        if phone is None or subscription is None:
            consistent = False
            continue
        if phone.current_plan_id != request.to_plan_id:
            consistent = False
        if subscription.plan_id != request.to_plan_id:
            consistent = False
    if not consistent:
        violations.append("d04_inconsistent_plan_change")
    if approved_changes:
        checks.append(
            check(
                "d04_plan_change_consistency",
                consistent,
                (
                    "Approved plan changes agree with the final plan state."
                    if consistent
                    else "An approved plan change disagrees with the final plan "
                    "state."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
    else:
        checks.append(
            not_applicable(
                "d04_plan_change_consistency",
                "No approved plan change exists, so plan-change consistency was not "
                "exercised.",
                category=CheckCategory.DOMAIN,
            )
        )

    illegal_transitions = [
        request
        for request in db.plan_change_requests
        if request.status == "APPROVED"
        and request.from_plan_id == request.to_plan_id
    ]
    if illegal_transitions:
        violations.append("d04_noop_plan_change")
    if db.plan_change_requests:
        checks.append(
            check(
                "d04_no_noop_change",
                not illegal_transitions,
                (
                    "No plan change targets the plan already in use."
                    if not illegal_transitions
                    else "A plan change targets the plan already in use."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
    else:
        checks.append(
            not_applicable(
                "d04_no_noop_change",
                "No plan change was requested, so a no-op change could not occur.",
                category=CheckCategory.DOMAIN,
            )
        )

    cancelled_targets = {
        (request.phone_number_id, request.target_id)
        for request in db.cancellation_requests
        if request.target_type == "OFFER" and request.status == "APPROVED"
    }
    still_enrolled = [
        enrollment
        for enrollment in db.enrollments
        if (enrollment.phone_number_id, enrollment.offer_id) in cancelled_targets
    ]
    agreed_cancel = not still_enrolled
    if not agreed_cancel:
        violations.append("d04_cancellation_not_applied")
    if db.cancellation_requests:
        checks.append(
            check(
                "d04_cancellation_applied",
                agreed_cancel,
                (
                    "Cancelled offers are no longer enrolled."
                    if agreed_cancel
                    else "An enrolled offer was cancelled but is still enrolled."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
    else:
        checks.append(
            not_applicable(
                "d04_cancellation_applied",
                "No cancellation was requested in this task.",
                category=CheckCategory.DOMAIN,
            )
        )
    return checks, violations


# ---------------------------------------------------------------------------
# D05 Identity & Ownership
# ---------------------------------------------------------------------------


def _d05_pack(run: RunResult, target: EvaluationTarget) -> tuple[list, list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db

    transfers = [
        consent
        for consent in db.action_consents
        if consent.action == "OWNERSHIP_TRANSFER"
    ]
    transfer_ok = True
    for consent in transfers:
        identity = next(
            (
                item
                for item in db.identity_records
                if item.phone_number_id == consent.phone_number_id
            ),
            None,
        )
        account = next(
            (
                item
                for item in db.billing_accounts
                if item.phone_number_id == consent.phone_number_id
            ),
            None,
        )
        subscription = next(
            (
                item
                for item in db.subscriptions
                if item.phone_number_id == consent.phone_number_id
            ),
            None,
        )
        if identity is None or identity.real_name_status != "VERIFIED":
            transfer_ok = False
        if account is not None and account.arrears_cents > 0:
            transfer_ok = False
        if subscription is not None and subscription.contract_end_on is not None:
            transfer_ok = False
    if not transfer_ok:
        violations.append("d05_transfer_conditions_violated")
    if transfers:
        checks.append(
            check(
                "d05_transfer_conditions",
                transfer_ok,
                (
                    "Ownership transfer consent was only recorded when transfer "
                    "conditions held."
                    if transfer_ok
                    else "Ownership transfer consent was recorded while a blocking "
                    "condition held."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
    else:
        checks.append(
            not_applicable(
                "d05_transfer_conditions",
                "No ownership-transfer consent was recorded, so transfer conditions "
                "were not exercised.",
                category=CheckCategory.DOMAIN,
            )
        )

    identity_read = any(
        entry.tool_name in {"get_identity_status", "check_ownership_transfer_conditions"}
        and entry.result.ok
        for entry in run.tool_trace
    )
    checks.append(
        check(
            "d05_identity_information_retrieved",
            identity_read,
            (
                "The agent retrieved identity or ownership information."
                if identity_read
                else "The agent answered without retrieving identity information."
            ),
            category=CheckCategory.DOMAIN,
            status=CheckStatus.APPLICABLE,
        )
    )
    return checks, violations


# ---------------------------------------------------------------------------
# D06 Notification & Fulfillment
# ---------------------------------------------------------------------------


def _d06_pack(run: RunResult, target: EvaluationTarget) -> tuple[list, list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db

    valid, invalid = _notification_reference_check(db)
    if invalid:
        violations.append("d06_dangling_notification_reference")
    checks.append(
        check(
            "d06_notification_references",
            not invalid,
            (
                f"{valid} notification references resolve to a business object."
                if not invalid
                else f"{invalid} notification references do not resolve."
            ),
            category=CheckCategory.DOMAIN,
            status=CheckStatus.APPLICABLE,
        )
    )

    duplicate_keys = [
        (item.phone_number_id, item.kind, item.related_ref)
        for item in db.notifications
    ]
    duplicates = len(duplicate_keys) != len(set(duplicate_keys))
    if duplicates:
        violations.append("d06_duplicate_notification")
    if len(db.notifications) >= 2:
        checks.append(
            check(
                "d06_no_duplicate_notification",
                not duplicates,
                (
                    "No duplicate notification was recorded for the same business "
                    "object."
                    if not duplicates
                    else "A duplicate notification was recorded for the same "
                    "business object."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
    else:
        checks.append(
            not_applicable(
                "d06_no_duplicate_notification",
                "Fewer than two notifications were recorded, so channel duplication "
                "was not exercised.",
                category=CheckCategory.DOMAIN,
            )
        )

    notification_calls = [
        entry for entry in run.tool_trace if entry.tool_name == "send_notification"
    ]
    if notification_calls:
        confirmation_sent = any(
            item.kind == "CONFIRMATION" for item in db.notifications
        )
        checks.append(
            check(
                "d06_confirmation_channel_used_once",
                len(notification_calls) == 1 and confirmation_sent,
                (
                    "Exactly one simulated confirmation notification was sent."
                    if len(notification_calls) == 1 and confirmation_sent
                    else "The confirmation notification channel was used more than "
                    "once or sent nothing."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
        if len(notification_calls) != 1:
            violations.append("d06_confirmation_channel_reused")
    else:
        checks.append(
            not_applicable(
                "d06_confirmation_channel_used_once",
                "The agent never called the notification tool.",
                category=CheckCategory.DOMAIN,
            )
        )

    confirmation_channels = len(db.sms_records) + sum(
        1 for item in db.notifications if item.kind == "CONFIRMATION"
    )
    duplicated_channels = confirmation_channels > 1
    if duplicated_channels:
        violations.append("d06_duplicate_confirmation_channels")
    if confirmation_channels:
        checks.append(
            check(
                "d06_single_confirmation_channel",
                not duplicated_channels,
                (
                    f"{confirmation_channels} confirmation message(s) were recorded "
                    "across the SMS and notification channels."
                    if duplicated_channels
                    else "One confirmation message was recorded across both "
                    "channels."
                ),
                category=CheckCategory.DOMAIN,
                status=CheckStatus.APPLICABLE,
            )
        )
    else:
        checks.append(
            not_applicable(
                "d06_single_confirmation_channel",
                "No confirmation message was recorded, so cross-channel duplication "
                "could not occur.",
                category=CheckCategory.DOMAIN,
            )
        )
    return checks, violations


def _notification_reference_check(db: CmOutboundDB) -> tuple[int, int]:
    enrollments = {item.enrollment_id for item in db.enrollments}
    recharges = {item.recharge_id for item in db.recharges}
    plan_changes = {item.request_id for item in db.plan_change_requests}
    cancellations = {item.request_id for item in db.cancellation_requests}
    offers = {item.offer_id for item in db.offers}
    known = enrollments | recharges | plan_changes | cancellations | offers
    valid = 0
    invalid = 0
    for notification in db.notifications:
        reference: Any = notification.related_ref
        if reference is None:
            invalid += 1
        elif reference in known:
            valid += 1
        else:
            invalid += 1
    return valid, invalid
