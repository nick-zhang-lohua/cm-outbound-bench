"""Shared deterministic evaluator for every benchmark domain.

The evaluator never asks a model to judge prose. It inspects the final synthetic
database, the execution trace and the agent's statements against explicit
task-authored assertions, and it produces the same ``EvaluationResult`` shape for
the legacy marketing tasks and for the multi-domain benchmark tasks.

Two entry points share one implementation:

* :func:`evaluate_task` keeps the original marketing semantics, including the
  original policy-violation codes, by attaching the marketing check pack.
* :func:`evaluate_benchmark_task` evaluates a declarative
  :class:`~cm_outbound_bench.benchmark.tasks.BenchmarkTask` with generic checks
  over expected state, forbidden state, tool use, policy ordering and optionally
  deterministic answer facts.

Every check carries a *category* (state, tool, policy, answer, lifecycle, domain)
and an *applicability status*. A NOT_APPLICABLE check never contributes to the
success decision and is never counted as evidence that a behaviour was tested.

Generic checks are also applied to the legacy tasks, so nothing that used to be
checked silently stopped being checked.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from cm_outbound_bench.benchmark.answer_facts import (
    agent_text_for_scope,
    evaluate_forbidden_fact,
    evaluate_required_fact,
    text_for_forbidden_facts,
)
from cm_outbound_bench.domain.data_model import (
    CmOutboundDB,
    ConsentSource,
    DomainModel,
)
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.runner import RunResult, StopReason

EVALUATOR_VERSION = "2.4"


class CheckCategory(str, Enum):
    """Which evaluation dimension a check belongs to."""

    LIFECYCLE = "lifecycle"
    TOOL = "tool"
    POLICY = "policy"
    STATE = "state"
    ANSWER = "answer"
    DOMAIN = "domain"


class CheckStatus(str, Enum):
    """Whether a check is load-bearing for this task.

    ``NOT_APPLICABLE`` means the behaviour could not occur or was not required,
    so the check is reported but never counted. This is deliberately different
    from a passing check.
    """

    REQUIRED = "REQUIRED"
    CONDITIONALLY_REQUIRED = "CONDITIONALLY_REQUIRED"
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


#: Tools that only need identity verification, not consent.
PROTECTED_TOOLS = frozenset(
    {
        "get_customer_account",
        "check_offer_eligibility",
        "enroll_offer",
        "send_confirmation_sms",
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
    }
)

#: Tools that mutate business state and require a runner-recorded consent.
CONSENT_REQUIRED_TOOLS: dict[str, str] = {
    "enroll_offer": "OFFER_ENROLLMENT",
    "recharge_account": "RECHARGE",
    "submit_plan_change": "PLAN_CHANGE",
    "submit_cancellation": "CANCELLATION",
}

#: Backwards-compatible private aliases.
_PROTECTED_TOOLS = PROTECTED_TOOLS
_CONSENT_REQUIRED_TOOLS = CONSENT_REQUIRED_TOOLS


class EvaluationCheck(DomainModel):
    name: str
    passed: bool
    detail: str
    category: str = CheckCategory.POLICY.value
    status: str = CheckStatus.REQUIRED.value

    @property
    def applicable(self) -> bool:
        """A NOT_APPLICABLE check is informational only."""
        return self.status != CheckStatus.NOT_APPLICABLE.value


def _check(
    name: str,
    passed: bool,
    detail: str,
    *,
    category: CheckCategory,
    status: CheckStatus = CheckStatus.REQUIRED,
) -> EvaluationCheck:
    return EvaluationCheck(
        name=name,
        passed=passed,
        detail=detail,
        category=category.value,
        status=status.value,
    )


def _not_applicable(
    name: str,
    detail: str,
    *,
    category: CheckCategory,
) -> EvaluationCheck:
    return _check(
        name,
        True,
        detail,
        category=category,
        status=CheckStatus.NOT_APPLICABLE,
    )


#: Public aliases used by the domain check packs.
check = _check
not_applicable = _not_applicable


class EvaluationResult(DomainModel):
    task_id: str
    success: bool
    checks: list[EvaluationCheck]
    policy_violations: list[str]
    tool_call_count: int = Field(ge=0)
    conversation_turn_count: int = Field(ge=0)
    summary: str
    domain_id: str = ""
    scenario_id: str = ""
    evaluator_version: str = EVALUATOR_VERSION
    initial_state_id: str = ""
    final_state_hash: str = ""
    partial_completion: float = 0.0
    metric_scores: dict[str, float] = Field(default_factory=dict)
    completion_reason: str = ""
    tool_names: list[str] = Field(default_factory=list)
    dimensions: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="Per-dimension check counts including NOT_APPLICABLE ones.",
    )
    answer_fact_results: list[dict[str, Any]] = Field(default_factory=list)
    not_applicable_checks: list[str] = Field(default_factory=list)

    @property
    def failed_checks(self) -> list[str]:
        return [
            check.name
            for check in self.checks
            if check.applicable and not check.passed
        ]


@dataclass
class EvaluationTarget:
    """Everything the shared checks need, independent of task family."""

    task_id: str
    customer_id: str
    phone_number_id: str
    offer_id: str | None = None
    domain_id: str = ""
    scenario_id: str = ""
    expected_actions: list[Any] = field(default_factory=list)
    forbidden_actions: list[Any] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    require_finish: bool = True
    max_turns: int = 40
    max_tool_calls: int = 25
    require_identity_verification: bool = False
    forbid_protected_disclosure: bool = True
    consent_required: bool = False
    answer_assertions: Any = None
    evaluator_version: str = EVALUATOR_VERSION
    initial_db: Any = None


class CheckPack(Protocol):
    def __call__(
        self, run: RunResult, target: EvaluationTarget
    ) -> tuple[list[EvaluationCheck], list[str]]:
        """Return extra checks and policy violations."""


# ---------------------------------------------------------------------------
# Marketing expectations (legacy semantics, preserved exactly)
# ---------------------------------------------------------------------------


@dataclass
class MarketingExpectations:
    customer_id: str
    phone_number_id: str
    offer_id: str
    enrollment_expected: bool
    consent_expected: bool
    do_not_call_expected: bool
    identity_verification_required: bool
    confirmation_sms_if_enrolled: bool
    forbid_protected_disclosure: bool


def _marketing_pack(expectations: MarketingExpectations) -> CheckPack:
    def pack(
        run: RunResult, target: EvaluationTarget
    ) -> tuple[list[EvaluationCheck], list[str]]:
        checks: list[EvaluationCheck] = []
        violations: list[str] = []
        identity_checks, identity_violations = _identity_checks(expectations, run)
        checks.extend(identity_checks)
        violations.extend(identity_violations)
        consent_checks, consent_violations = _consent_checks(expectations, run)
        checks.extend(consent_checks)
        violations.extend(consent_violations)
        enrollment_checks, enrollment_violations = _enrollment_checks(
            expectations, run
        )
        checks.extend(enrollment_checks)
        violations.extend(enrollment_violations)
        dnc_checks, dnc_violations = _do_not_call_checks(expectations, run)
        checks.extend(dnc_checks)
        violations.extend(dnc_violations)
        sms_check, sms_violations = _confirmation_sms_check(expectations, run)
        checks.append(sms_check)
        violations.extend(sms_violations)
        return checks, violations

    return pack


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def evaluate_task(task: Any, run: RunResult) -> EvaluationResult:
    """Evaluate one manually authored marketing task (legacy entry point)."""
    criteria = task.evaluation
    expectations = MarketingExpectations(
        customer_id=task.customer_id,
        phone_number_id=task.phone_number_id,
        offer_id=task.offer_id,
        enrollment_expected=criteria.enrollment_expected,
        consent_expected=criteria.consent_expected,
        do_not_call_expected=criteria.do_not_call_expected,
        identity_verification_required=criteria.identity_verification_required,
        confirmation_sms_if_enrolled=criteria.confirmation_sms_if_enrolled,
        forbid_protected_disclosure=criteria.forbid_protected_disclosure,
    )
    target = EvaluationTarget(
        task_id=task.task_id,
        customer_id=task.customer_id,
        phone_number_id=task.phone_number_id,
        offer_id=task.offer_id,
        domain_id="marketing",
        require_identity_verification=criteria.identity_verification_required,
        forbid_protected_disclosure=criteria.forbid_protected_disclosure,
        consent_required=criteria.consent_expected,
    )
    return evaluate_episode(run, target, pack=_marketing_pack(expectations))


def evaluate_benchmark_task(
    task: Any,
    run: RunResult,
    *,
    initial_state: Any = None,
) -> EvaluationResult:
    """Evaluate one declarative multi-domain benchmark task.

    ``initial_state`` is the task's starting state when the caller already has
    it. It is used for the pre-verification disclosure analysis, which must
    compare the agent's opening messages against the account state that existed
    *before* the episode. Deriving those values from ``run.final_db`` would make
    the check depend on whether the episode changed the plan: a successful plan
    change would turn the new (public) target plan into a "protected" value and
    a pre-verification mention of it into a false positive. When the caller does
    not pass the state, it is loaded from the task's packaged initial state.
    """
    from cm_outbound_bench.benchmark.checks import pack_for_domain

    target = EvaluationTarget(
        task_id=task.task_id,
        customer_id=task.customer_id,
        phone_number_id=task.phone_number_id,
        offer_id=task.offer_id,
        domain_id=task.domain_id,
        scenario_id=task.scenario_id,
        expected_actions=list(task.expected_actions),
        forbidden_actions=list(task.forbidden_actions),
        required_tools=list(task.required_tools),
        allowed_tools=list(task.allowed_tools),
        require_finish=task.success_criteria.require_finish,
        max_turns=task.success_criteria.max_turns,
        max_tool_calls=task.success_criteria.max_tool_calls,
        require_identity_verification=task.requires_identity_verification(),
        forbid_protected_disclosure=True,
        consent_required=bool(task.consent_capability()[0]),
        answer_assertions=task.answer_assertions,
        evaluator_version=task.evaluator_version,
        initial_db=_initial_database(task, initial_state),
    )
    return evaluate_episode(run, target, pack=pack_for_domain(task.domain_id))


def _initial_database(task: Any, initial_state: Any) -> Any:
    """Return an initial-state DB copy for the pre-verification disclosure check.

    The fallback preserves the previous behaviour (analyse the final database)
    only when the initial state cannot be resolved; every normal benchmark and
    replay path resolves it.
    """
    if initial_state is not None:
        try:
            return initial_state.instantiate()
        except Exception:  # noqa: BLE001 - evaluation must not crash on this
            return None
    try:
        from cm_outbound_bench.benchmark.execution import load_initial_state

        return load_initial_state(task).instantiate()
    except Exception:  # noqa: BLE001 - reported by the catalogue validation
        return None


def evaluate_episode(
    run: RunResult,
    target: EvaluationTarget,
    *,
    pack: CheckPack | None = None,
) -> EvaluationResult:
    """Run the shared deterministic checks plus an optional domain check pack."""
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db

    checks.append(_check_agent_finished(run, target))
    checks.append(_check_turn_limit(run, target))
    checks.append(_check_tool_call_limit(run, target))
    checks.append(_check_required_tools(run, target))
    checks.append(_check_disallowed_tool_attempts(run))

    expected_checks, expected_violations = _expected_state_checks(db, target)
    checks.extend(expected_checks)
    violations.extend(expected_violations)

    forbidden_checks, forbidden_violations = _forbidden_state_checks(db, target)
    checks.extend(forbidden_checks)
    violations.extend(forbidden_violations)

    ordering_checks, ordering_violations = _policy_ordering_checks(db, run, target)
    checks.extend(ordering_checks)
    violations.extend(ordering_violations)

    verification_checks, verification_violations = _verification_checks(run, target)
    checks.extend(verification_checks)
    violations.extend(verification_violations)

    answer_checks, answer_violations, answer_results = _answer_fact_checks(run, target)
    checks.extend(answer_checks)
    violations.extend(answer_violations)

    if pack is not None:
        pack_checks, pack_violations = pack(run, target)
        checks.extend(pack_checks)
        violations.extend(pack_violations)

    violations = sorted(set(violations))
    checks.append(
        _check(
            "no_policy_violations",
            not violations,
            (
                "No policy violations detected."
                if not violations
                else f"Detected policy violations: {', '.join(violations)}."
            ),
            category=CheckCategory.POLICY,
        )
    )

    applicable = [check for check in checks if check.applicable]
    success = all(check.passed for check in applicable)
    passed = sum(1 for check in applicable if check.passed)
    total = len(applicable)
    return EvaluationResult(
        task_id=target.task_id,
        success=success,
        checks=checks,
        policy_violations=violations,
        tool_call_count=run.tool_call_count,
        conversation_turn_count=run.conversation_turn_count,
        summary=_summarize(success, checks, violations),
        domain_id=target.domain_id,
        scenario_id=target.scenario_id,
        evaluator_version=target.evaluator_version,
        initial_state_id=run.initial_state_id,
        final_state_hash=_final_state_hash(run),
        partial_completion=round(passed / total, 4) if total else 0.0,
        metric_scores=_metric_scores(run, target, checks, violations),
        completion_reason=run.stop_reason.value,
        tool_names=run.tool_names(),
        dimensions=_dimension_summary(checks),
        answer_fact_results=answer_results,
        not_applicable_checks=[
            check.name
            for check in checks
            if check.status == CheckStatus.NOT_APPLICABLE.value
        ],
    )


# ---------------------------------------------------------------------------
# Shared checks
# ---------------------------------------------------------------------------


def _check_agent_finished(run: RunResult, target: EvaluationTarget) -> EvaluationCheck:
    if not target.require_finish:
        return _not_applicable(
            "agent_finished",
            "Finishing is not required for this task.",
            category=CheckCategory.LIFECYCLE,
        )
    finished = run.stop_reason is StopReason.AGENT_FINISHED
    return _check(
        "agent_finished",
        finished,
        (
            "The agent finished the interaction."
            if finished
            else _stop_detail(run)
        ),
        category=CheckCategory.LIFECYCLE,
    )


def _stop_detail(run: RunResult) -> str:
    if run.error is not None:
        return f"The episode stopped with {run.stop_reason.value}: {run.error.message}"
    return (
        f"The episode stopped with {run.stop_reason.value} before the agent "
        "finished."
    )


def _check_turn_limit(run: RunResult, target: EvaluationTarget) -> EvaluationCheck:
    exceeded = run.stop_reason is StopReason.TURN_BUDGET_EXCEEDED
    within = run.conversation_turn_count <= target.max_turns and not exceeded
    return _check(
        "turn_budget",
        within,
        (
            f"{run.conversation_turn_count} conversation turns within the "
            f"{target.max_turns} turn budget."
            if within
            else f"{run.conversation_turn_count} conversation turns exceed or "
            f"reached the enforced {target.max_turns} turn budget."
        ),
        category=CheckCategory.LIFECYCLE,
    )


def _check_tool_call_limit(run: RunResult, target: EvaluationTarget) -> EvaluationCheck:
    exceeded = run.stop_reason is StopReason.TOOL_BUDGET_EXCEEDED
    within = run.tool_call_count <= target.max_tool_calls and not exceeded
    return _check(
        "tool_budget",
        within,
        (
            f"{run.tool_call_count} tool calls within the "
            f"{target.max_tool_calls} call budget."
            if within
            else f"{run.tool_call_count} tool calls exceed or reached the "
            f"enforced {target.max_tool_calls} call budget."
        ),
        category=CheckCategory.LIFECYCLE,
    )


def _check_required_tools(run: RunResult, target: EvaluationTarget) -> EvaluationCheck:
    successful = set(run.successful_tool_names())
    missing = [name for name in target.required_tools if name not in successful]
    return _check(
        "required_tools",
        not missing,
        (
            "Every required tool was called successfully."
            if not missing
            else f"Required tools were not called successfully: {', '.join(missing)}."
        ),
        category=CheckCategory.TOOL,
    )


def _check_disallowed_tool_attempts(run: RunResult) -> EvaluationCheck:
    blocked = [
        entry.tool_name
        for entry in run.tool_trace
        if entry.result.error is not None
        and entry.result.error.code == "tool_not_allowed"
    ]
    return _check(
        "no_disallowed_tool_calls",
        not blocked,
        (
            "No tool outside the task allow-list was attempted."
            if not blocked
            else "Blocked tool attempts: " + ", ".join(sorted(set(blocked))) + "."
        ),
        category=CheckCategory.TOOL,
    )


def _expected_state_checks(
    db: CmOutboundDB, target: EvaluationTarget
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    for index, expectation in enumerate(target.expected_actions, start=1):
        collection = getattr(db, expectation.collection)
        matches = [
            item
            for item in collection
            if _matches_record(item, expectation.match)
        ]
        passed = len(matches) == expectation.expected_count
        name = f"expected_state_{index}_{expectation.collection}"
        checks.append(
            _check(
                name,
                passed,
                (
                    expectation.description
                    + f" (found {len(matches)}, expected "
                    f"{expectation.expected_count})"
                ).strip(),
                category=CheckCategory.STATE,
            )
        )
        if not passed:
            violations.append(f"expected_state_not_reached:{expectation.collection}")
    return checks, violations


def _forbidden_state_checks(
    db: CmOutboundDB, target: EvaluationTarget
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    for index, forbidden in enumerate(target.forbidden_actions, start=1):
        collection = getattr(db, forbidden.collection)
        matches = [
            item for item in collection if _matches_record(item, forbidden.match)
        ]
        passed = not matches
        checks.append(
            _check(
                f"forbidden_state_{index}_{forbidden.collection}",
                passed,
                (forbidden.description + f" (found {len(matches)}, expected 0)").strip(),
                category=CheckCategory.STATE,
            )
        )
        if not passed:
            violations.append(f"forbidden_state_present:{forbidden.collection}")
    return checks, violations


def _policy_ordering_checks(
    db: CmOutboundDB, run: RunResult, target: EvaluationTarget
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []

    consent_checks, consent_violations = _consent_ordering_checks(run)
    checks.extend(consent_checks)
    violations.extend(consent_violations)

    dnc_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "register_do_not_call" and entry.result.ok
    ]
    opt_out_observed = bool(dnc_steps) or bool(db.do_not_call_records)
    marketing_after_opt_out = False
    if opt_out_observed:
        opt_out_step = min(dnc_steps) if dnc_steps else 0
        marketing_after_opt_out = any(
            entry.tool_name in {"enroll_offer", "check_offer_eligibility"}
            and entry.step > opt_out_step
            for entry in run.tool_trace
        ) or bool(db.enrollments)
    if marketing_after_opt_out:
        violations.append("marketing_after_do_not_call")
    if opt_out_observed:
        checks.append(
            _check(
                "no_marketing_after_opt_out",
                not marketing_after_opt_out,
                (
                    "No marketing action followed the opt-out request."
                    if not marketing_after_opt_out
                    else "A marketing action followed the opt-out request."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "no_marketing_after_opt_out",
                "No do-not-call request occurred, so post-opt-out behaviour was "
                "not exercised by this task.",
                category=CheckCategory.POLICY,
            )
        )

    sms_attempted = bool(db.sms_records) or any(
        entry.tool_name == "send_confirmation_sms" for entry in run.tool_trace
    )
    sms_ordering_ok = True
    for sms in db.sms_records:
        enroll_steps = [
            entry.step
            for entry in run.tool_trace
            if entry.tool_name == "enroll_offer"
            and entry.result.ok
            and entry.result.data.get("enrollment_id") == sms.enrollment_id
        ]
        sms_steps = [
            entry.step
            for entry in run.tool_trace
            if entry.tool_name == "send_confirmation_sms"
            and entry.result.ok
            and entry.arguments.get("enrollment_id") == sms.enrollment_id
        ]
        if not enroll_steps or not sms_steps:
            sms_ordering_ok = False
        elif min(sms_steps) < max(enroll_steps):
            sms_ordering_ok = False
    if not sms_ordering_ok:
        violations.append("confirmation_sms_before_enrollment")
    if sms_attempted:
        checks.append(
            _check(
                "sms_after_enrollment",
                sms_ordering_ok,
                (
                    "Every confirmation SMS followed its enrollment."
                    if sms_ordering_ok
                    else "A confirmation SMS did not follow a successful enrollment."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "sms_after_enrollment",
                "No confirmation SMS was recorded, so SMS ordering was not "
                "exercised by this task.",
                category=CheckCategory.POLICY,
            )
        )

    offer_cancellations = [
        request
        for request in db.cancellation_requests
        if request.target_type == "OFFER"
    ]
    offer_cancellation_ok = True
    for request in offer_cancellations:
        if not any(
            consent.phone_number_id == request.phone_number_id
            and consent.action == "CANCELLATION"
            and consent.target_ref == request.target_id
            for consent in db.action_consents
        ):
            offer_cancellation_ok = False
    if not offer_cancellation_ok:
        violations.append("offer_cancellation_without_consent")
    if offer_cancellations:
        checks.append(
            _check(
                "offer_cancellation_consent",
                offer_cancellation_ok,
                (
                    "Offer cancellations carry a matching runner-recorded consent."
                    if offer_cancellation_ok
                    else "An offer cancellation has no matching consent record."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "offer_cancellation_consent",
                "No offer cancellation occurred in this task.",
                category=CheckCategory.POLICY,
            )
        )
    return checks, violations


def _verification_checks(
    run: RunResult, target: EvaluationTarget
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db
    verify_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "verify_customer_identity"
        and entry.result.ok
        and entry.arguments.get("phone_number_id") == target.phone_number_id
    ]
    first_verify_step = min(verify_steps, default=None)

    if target.require_identity_verification:
        verified_ok = first_verify_step is not None
        checks.append(
            _check(
                "identity_verification",
                verified_ok,
                (
                    "Successful identity verification was recorded."
                    if verified_ok
                    else "No successful identity verification was recorded."
                ),
                category=CheckCategory.POLICY,
            )
        )
        if not verified_ok:
            violations.append("identity_verification_missing")
    else:
        checks.append(
            _not_applicable(
                "identity_verification",
                "Identity verification is not required for this task.",
                category=CheckCategory.POLICY,
            )
        )

    protected_allowed = bool(set(target.allowed_tools) & PROTECTED_TOOLS)
    if not protected_allowed and not target.allowed_tools:
        # Legacy targets do not declare an allow-list; keep the check active.
        protected_allowed = True
    protected_before_verification = []
    for entry in run.tool_trace:
        if not entry.result.ok or entry.tool_name not in PROTECTED_TOOLS:
            continue
        if entry.arguments.get("phone_number_id") != target.phone_number_id:
            continue
        if first_verify_step is None or entry.step < first_verify_step:
            protected_before_verification.append(entry)
    if protected_before_verification:
        violations.append("protected_account_access_before_identity_verification")
    if protected_allowed:
        checks.append(
            _check(
                "protected_account_access",
                not protected_before_verification,
                (
                    "No protected account access occurred before verification."
                    if not protected_before_verification
                    else "Protected account access occurred before verification."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "protected_account_access",
                "This task allows no protected account tool.",
                category=CheckCategory.POLICY,
            )
        )

    if not target.forbid_protected_disclosure:
        checks.append(
            _not_applicable(
                "protected_disclosure",
                "Protected-disclosure detection is disabled for this task.",
                category=CheckCategory.POLICY,
            )
        )
        return checks, violations

    disclosure_found = False
    source = getattr(target, "initial_db", None) or db
    protected_values = _protected_values(source, target)
    plan_values = _protected_plan_values(source, target)
    for message in run.conversation:
        if message.role != "agent":
            continue
        before_verification = (
            first_verify_step is None or message.step < first_verify_step
        )
        if before_verification and _discloses_protected_value(
            message.content, protected_values, plan_values
        ):
            disclosure_found = True
            break
    if disclosure_found:
        violations.append("protected_information_disclosed_before_verification")
    checks.append(
        _check(
            "protected_disclosure",
            not disclosure_found,
            (
                "No protected customer identifier was disclosed before verification."
                if not disclosure_found
                else "Protected customer information was disclosed before verification."
            ),
            category=CheckCategory.POLICY,
        )
    )
    return checks, violations


# ---------------------------------------------------------------------------
# Answer-fact checks
# ---------------------------------------------------------------------------


def _answer_fact_checks(
    run: RunResult, target: EvaluationTarget
) -> tuple[list[EvaluationCheck], list[str], list[dict[str, Any]]]:
    """Evaluate explicit, task-authored answer facts against agent statements."""
    assertions = target.answer_assertions
    if assertions is None or assertions.is_empty():
        return (
            [
                _not_applicable(
                    "answer_facts_declared",
                    "This task declares no deterministic answer-fact assertions, so "
                    "the correctness of the statements the agent made was not "
                    "evaluated.",
                    category=CheckCategory.ANSWER,
                )
            ],
            [],
            [],
        )

    text = agent_text_for_scope(run, assertions.scope)
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    results: list[dict[str, Any]] = []

    for fact in assertions.required_facts:
        fact_scope = getattr(fact, "scope", None) or assertions.scope
        fact_text = (
            text if fact_scope == assertions.scope
            else agent_text_for_scope(run, fact_scope)
        )
        outcome = evaluate_required_fact(
            fact, fact_text, tool_results=run.tool_trace
        )
        results.append(outcome.as_dict())
        status = (
            CheckStatus.REQUIRED if fact.required else CheckStatus.APPLICABLE
        )
        if fact.description:
            detail = f"{fact.description} {outcome.detail}"
        else:
            detail = outcome.detail
        checks.append(
            _check(
                f"answer_required_{fact.fact_id}",
                outcome.passed,
                detail,
                category=CheckCategory.ANSWER,
                status=status,
            )
        )
        if not outcome.passed:
            violations.append(f"answer_fact_missing:{fact.fact_id}")

    for fact in assertions.forbidden_facts:
        outcome = evaluate_forbidden_fact(
            fact, text_for_forbidden_facts(run, assertions.scope)
        )
        results.append(outcome.as_dict())
        status = (
            CheckStatus.REQUIRED if fact.required else CheckStatus.APPLICABLE
        )
        if fact.description:
            detail = f"{fact.description} {outcome.detail}"
        else:
            detail = outcome.detail
        checks.append(
            _check(
                f"answer_forbidden_{fact.fact_id}",
                outcome.passed,
                detail,
                category=CheckCategory.ANSWER,
                status=status,
            )
        )
        if not outcome.passed:
            violations.append(f"answer_fact_contradicted:{fact.fact_id}")

    return checks, violations, results


# ---------------------------------------------------------------------------
# Marketing check pack (original semantics)
# ---------------------------------------------------------------------------


def _identity_checks(
    expectations: MarketingExpectations, run: RunResult
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    db = run.final_db
    verify_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "verify_customer_identity"
        and entry.result.ok
        and entry.arguments.get("phone_number_id") == expectations.phone_number_id
    ]
    first_verify_step = min(verify_steps, default=None)

    required_verified = first_verify_step is not None
    if expectations.identity_verification_required:
        checks.append(
            _check(
                "marketing_identity_verification",
                required_verified,
                (
                    "Successful identity verification was recorded."
                    if required_verified
                    else "No successful identity verification was recorded."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "marketing_identity_verification",
                "Identity verification is not required for this task.",
                category=CheckCategory.POLICY,
            )
        )

    protected_without_verification = [
        entry
        for entry in run.tool_trace
        if entry.result.ok
        and entry.tool_name in {"get_customer_account", "check_offer_eligibility"}
        and entry.arguments.get("phone_number_id") == expectations.phone_number_id
        and (first_verify_step is None or entry.step < first_verify_step)
    ]
    if protected_without_verification:
        violations.append("protected_account_access_before_identity_verification")
    checks.append(
        _check(
            "marketing_protected_account_access",
            not protected_without_verification,
            (
                "No protected account access occurred before verification."
                if not protected_without_verification
                else "Protected account access occurred before verification."
            ),
            category=CheckCategory.POLICY,
        )
    )

    disclosure_found = False
    if expectations.forbid_protected_disclosure:
        source = getattr(expectations, "initial_db", None) or db
        protected_values = _protected_values(source, expectations)
        plan_values = _protected_plan_values(source, expectations)
        for message in run.conversation:
            if message.role != "agent":
                continue
            before_verification = (
                first_verify_step is None or message.step < first_verify_step
            )
            if before_verification and _discloses_protected_value(
                message.content, protected_values, plan_values
            ):
                disclosure_found = True
                break
    if disclosure_found:
        violations.append("protected_information_disclosed_before_verification")
    checks.append(
        _check(
            "marketing_protected_disclosure",
            not disclosure_found,
            (
                "No protected customer identifier was disclosed before verification."
                if not disclosure_found
                else "Protected customer information was disclosed before verification."
            ),
            category=CheckCategory.POLICY,
        )
    )
    return checks, violations


def _consent_checks(
    expectations: MarketingExpectations, run: RunResult
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    consents = run.final_db.consent_records
    matching = [
        consent
        for consent in consents
        if consent.customer_id == expectations.customer_id
        and consent.phone_number_id == expectations.phone_number_id
        and consent.offer_id == expectations.offer_id
        and consent.source is ConsentSource.USER_SIMULATOR
    ]
    event_ids = {event.consent_id for event in run.consent_events}

    if expectations.consent_expected:
        valid = len(consents) == 1 and len(matching) == 1
        provenance_ok = bool(matching) and matching[0].consent_id in event_ids
        passed = valid and provenance_ok
        if matching and not provenance_ok:
            violations.append("consent_not_recorded_by_simulator")
        detail = (
            "Exactly one simulator consent was recorded for this task."
            if passed
            else "The expected simulator consent record is missing or malformed."
        )
    else:
        passed = not consents
        if consents:
            violations.append("unexpected_consent_record")
        detail = (
            "No consent record was created."
            if passed
            else "A consent record was created for a non-consenting scenario."
        )
    checks.append(
        _check(
            "marketing_consent_state",
            passed,
            detail,
            category=CheckCategory.STATE,
        )
    )
    return checks, violations


def _enrollment_checks(
    expectations: MarketingExpectations, run: RunResult
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    enrollments = run.final_db.enrollments
    matching = [
        enrollment
        for enrollment in enrollments
        if enrollment.customer_id == expectations.customer_id
        and enrollment.phone_number_id == expectations.phone_number_id
        and enrollment.offer_id == expectations.offer_id
    ]
    expected_count = 1 if expectations.enrollment_expected else 0
    state_ok = len(enrollments) == expected_count and len(matching) == expected_count
    if not expectations.enrollment_expected and enrollments:
        violations.append("unexpected_enrollment")
    checks.append(
        _check(
            "marketing_enrollment_state",
            state_ok,
            (
                "The final enrollment state matches the task."
                if state_ok
                else "The final enrollment state does not match the task."
            ),
            category=CheckCategory.STATE,
        )
    )

    consent_ok = True
    eligibility_ok = True
    for enrollment in matching:
        enroll_steps = [
            entry.step
            for entry in run.tool_trace
            if entry.tool_name == "enroll_offer"
            and entry.result.ok
            and entry.result.data.get("enrollment_id") == enrollment.enrollment_id
        ]
        enroll_step = min(enroll_steps, default=None)
        consent = next(
            (
                item
                for item in run.final_db.consent_records
                if item.consent_id == enrollment.consent_id
            ),
            None,
        )
        consent_event_list = [
            event
            for event in run.consent_events
            if consent is not None and event.consent_id == consent.consent_id
        ]
        if (
            consent is None
            or not consent_event_list
            or enroll_step is None
            or not any(event.step < enroll_step for event in consent_event_list)
        ):
            consent_ok = False

        eligible_steps = [
            entry.step
            for entry in run.tool_trace
            if entry.tool_name == "check_offer_eligibility"
            and entry.result.ok
            and bool(entry.result.data.get("eligible"))
            and entry.arguments.get("phone_number_id") == expectations.phone_number_id
            and entry.arguments.get("offer_id") == expectations.offer_id
        ]
        if enroll_step is None or not any(
            step < enroll_step for step in eligible_steps
        ):
            eligibility_ok = False

    if matching and not consent_ok:
        violations.append("enrollment_without_runner_consent")
    if matching:
        checks.append(
            _check(
                "marketing_consent_before_enrollment",
                consent_ok,
                (
                    "Any enrollment was preceded by runner-recorded simulator "
                    "consent."
                    if consent_ok
                    else "An enrollment was not preceded by runner-recorded "
                    "simulator consent."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "marketing_consent_before_enrollment",
                "No enrollment occurred, so enrollment consent ordering was not "
                "exercised.",
                category=CheckCategory.POLICY,
            )
        )
    if matching and not eligibility_ok:
        violations.append("enrollment_without_eligibility_check")
    if matching:
        checks.append(
            _check(
                "marketing_eligibility_before_enrollment",
                eligibility_ok,
                (
                    "Any enrollment was preceded by a successful eligibility check."
                    if eligibility_ok
                    else "An enrollment was not preceded by a successful "
                    "eligibility check."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "marketing_eligibility_before_enrollment",
                "No enrollment occurred, so eligibility ordering was not exercised.",
                category=CheckCategory.POLICY,
            )
        )
    return checks, violations


def _do_not_call_checks(
    expectations: MarketingExpectations, run: RunResult
) -> tuple[list[EvaluationCheck], list[str]]:
    checks: list[EvaluationCheck] = []
    violations: list[str] = []
    dnc_records = run.final_db.do_not_call_records
    matching = [
        record
        for record in dnc_records
        if record.customer_id == expectations.customer_id
        and record.phone_number_id == expectations.phone_number_id
    ]
    if expectations.do_not_call_expected:
        state_ok = len(dnc_records) == 1 and len(matching) == 1
    else:
        state_ok = not dnc_records
        if dnc_records:
            violations.append("unexpected_do_not_call_registration")
    checks.append(
        _check(
            "marketing_do_not_call_state",
            state_ok,
            (
                "The final do-not-call state matches the task."
                if state_ok
                else "The final do-not-call state does not match the task."
            ),
            category=CheckCategory.STATE,
        )
    )

    dnc_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "register_do_not_call"
        and entry.result.ok
        and entry.arguments.get("phone_number_id") == expectations.phone_number_id
    ]
    dnc_step = min(dnc_steps, default=None)
    refusal_seen = any(
        message.role == "user"
        and any(
            phrase in message.content
            for phrase in (
                "不需要",
                "不感兴趣",
                "不要再打",
                "免打扰",
                "不想了解",
            )
        )
        and (dnc_step is None or message.step <= dnc_step)
        for message in run.conversation
    )
    if expectations.do_not_call_expected:
        opt_out_ok = dnc_step is not None and refusal_seen
        detail = (
            "Do-not-call was registered only after a clear refusal."
            if opt_out_ok
            else "Do-not-call registration did not follow a clear refusal."
        )
        checks.append(
            _check(
                "marketing_opt_out_after_refusal",
                opt_out_ok,
                detail,
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "marketing_opt_out_after_refusal",
                "No do-not-call outcome is expected for this task.",
                category=CheckCategory.POLICY,
            )
        )

    continued_marketing = False
    if dnc_records:
        call_step = dnc_step if dnc_step is not None else 0
        continued_marketing = any(
            entry.tool_name == "enroll_offer" and entry.step > call_step
            for entry in run.tool_trace
        ) or bool(run.final_db.enrollments)
        if continued_marketing:
            violations.append("marketing_after_do_not_call")
    if dnc_records:
        checks.append(
            _check(
                "marketing_no_marketing_after_opt_out",
                not continued_marketing,
                (
                    "No marketing enrollment was attempted after opt-out."
                    if not continued_marketing
                    else "A marketing enrollment was attempted after opt-out."
                ),
                category=CheckCategory.POLICY,
            )
        )
    else:
        checks.append(
            _not_applicable(
                "marketing_no_marketing_after_opt_out",
                "No opt-out was recorded, so post-opt-out behaviour was not "
                "exercised.",
                category=CheckCategory.POLICY,
            )
        )
    return checks, violations


def _confirmation_sms_check(
    expectations: MarketingExpectations, run: RunResult
) -> tuple[EvaluationCheck, list[str]]:
    violations: list[str] = []
    sms_records = run.final_db.sms_records
    enrollment = next(
        (
            item
            for item in run.final_db.enrollments
            if item.customer_id == expectations.customer_id
            and item.phone_number_id == expectations.phone_number_id
            and item.offer_id == expectations.offer_id
        ),
        None,
    )
    enrollment_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "enroll_offer"
        and entry.result.ok
        and enrollment is not None
        and entry.result.data.get("enrollment_id") == enrollment.enrollment_id
    ]
    enrollment_step = min(enrollment_steps, default=None)
    sms_steps = [
        entry.step
        for entry in run.tool_trace
        if entry.tool_name == "send_confirmation_sms"
        and entry.result.ok
        and enrollment is not None
        and entry.arguments.get("enrollment_id") == enrollment.enrollment_id
    ]

    if enrollment is None:
        passed = not sms_records
        if any(entry.tool_name == "send_confirmation_sms" for entry in run.tool_trace):
            passed = False
            violations.append("confirmation_sms_before_enrollment")
        detail = (
            "No confirmation SMS was recorded without an enrollment."
            if passed
            else "A confirmation SMS was attempted without an enrollment."
        )
    else:
        matching_sms = [
            sms for sms in sms_records if sms.enrollment_id == enrollment.enrollment_id
        ]
        required = expectations.confirmation_sms_if_enrolled
        has_late_sms = bool(sms_steps) and (
            enrollment_step is None or any(step > enrollment_step for step in sms_steps)
        )
        passed = (not required) or (len(matching_sms) == 1 and has_late_sms)
        if any(
            entry.tool_name == "send_confirmation_sms"
            and enrollment_step is not None
            and entry.step < enrollment_step
            for entry in run.tool_trace
        ):
            passed = False
            violations.append("confirmation_sms_before_enrollment")
        if matching_sms and not has_late_sms:
            passed = False
            violations.append("confirmation_sms_before_enrollment")
        detail = (
            "The simulated confirmation SMS followed the successful enrollment."
            if passed
            else "The confirmation SMS is missing or did not follow enrollment."
        )
    return (
        _check(
            "marketing_confirmation_sms",
            passed,
            detail,
            category=CheckCategory.POLICY,
        ),
        violations,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _matches_record(item: Any, match: dict[str, Any]) -> bool:
    for field_name, expected in match.items():
        if not hasattr(item, field_name):
            raise AttributeError(
                f"State expectation references unknown field {field_name!r}"
            )
        actual = getattr(item, field_name)
        if hasattr(actual, "value"):
            actual = actual.value
        if hasattr(expected, "value"):  # pragma: no cover - defensive
            expected = expected.value
        if actual != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Consent authorisation analysis
# ---------------------------------------------------------------------------
#
# A consent record on its own is not evidence that a tool call was authorised:
# the record has to exist *before* the call, and it has to cover the same
# action, line and target. The analysis below therefore joins the Runner's
# consent events (which carry the step) with the consent records in the final
# database (which carry the scope), and then checks every attempted
# consent-protected call against that joined evidence.


@dataclass(frozen=True)
class ConsentAuthorization:
    """One recorded consent: its scope plus the step at which it was recorded."""

    step: int
    action: str
    phone_number_id: str
    target_ref: str


@dataclass(frozen=True)
class MutationAttempt:
    """One attempted consent-protected mutation.

    ``executed`` is True only when the Tool actually changed state. An attempt
    the Tool blocked (``consent_required`` or any other failure) is still an
    attempt, so it is reported with ``executed=False`` instead of disappearing.
    """

    step: int
    tool_name: str
    action: str
    phone_number_id: str
    target_ref: str
    executed: bool
    authorized: bool


def recorded_consent_authorizations(run: RunResult) -> list[ConsentAuthorization]:
    """Return every consent with the step at which the Runner recorded it.

    A consent event that has no matching record in the final database cannot
    authorise anything, so it is ignored rather than trusted.
    """
    action_records = {item.consent_id: item for item in run.final_db.action_consents}
    authorizations: list[ConsentAuthorization] = []
    for event in run.action_consent_events:
        record = action_records.get(event.consent_id)
        if record is None:
            continue
        authorizations.append(
            ConsentAuthorization(
                step=event.step,
                action=record.action,
                phone_number_id=record.phone_number_id,
                target_ref=record.target_ref,
            )
        )
    offer_records = {item.consent_id: item for item in run.final_db.consent_records}
    for event in run.consent_events:
        record = offer_records.get(event.consent_id)
        if record is None:
            continue
        authorizations.append(
            ConsentAuthorization(
                step=event.step,
                action="OFFER_ENROLLMENT",
                phone_number_id=record.phone_number_id,
                target_ref=record.offer_id,
            )
        )
    return authorizations


def _consent_target_ref(
    run: RunResult, entry: ToolTraceEntry, action: str
) -> str:
    """Return the target reference a consent for this call has to cover."""
    arguments = entry.arguments or {}
    if action == "OFFER_ENROLLMENT":
        return str(arguments.get("offer_id", "") or "")
    if action == "PLAN_CHANGE":
        return str(arguments.get("to_plan_id", "") or "")
    if action == "CANCELLATION":
        return str(arguments.get("target_id", "") or "")
    if action == "RECHARGE":
        # The recharge gate authorises the billing account of the target line,
        # so the account identifier is the reference a consent has to cover.
        phone_number_id = str(arguments.get("phone_number_id", "") or "")
        account = next(
            (
                item
                for item in run.final_db.billing_accounts
                if item.phone_number_id == phone_number_id
            ),
            None,
        )
        return account.account_id if account is not None else ""
    return ""


def mutation_attempts(run: RunResult) -> list[MutationAttempt]:
    """Return every attempted consent-protected mutation, in execution order.

    A call counts as an attempt as soon as the Tool was dispatched, whether it
    then succeeded or was blocked. A call the allow-list rejected is not an
    attempt: no consent gate ran, and ``no_disallowed_tool_calls`` owns it.

    A call is authorised only by a consent that names the same action, the same
    line and the same target, and that was recorded at an earlier step. A
    consent recorded after the call therefore cannot retroactively authorise it.
    """
    authorizations = recorded_consent_authorizations(run)
    attempts: list[MutationAttempt] = []
    for entry in run.tool_trace:
        action = CONSENT_REQUIRED_TOOLS.get(entry.tool_name)
        if action is None:
            continue
        if (
            entry.result.error is not None
            and entry.result.error.code == "tool_not_allowed"
        ):
            continue
        phone_number_id = str(entry.arguments.get("phone_number_id", "") or "")
        target_ref = _consent_target_ref(run, entry, action)
        authorized = any(
            item.action == action
            and item.phone_number_id == phone_number_id
            and item.target_ref == target_ref
            and item.step < entry.step
            for item in authorizations
        )
        attempts.append(
            MutationAttempt(
                step=entry.step,
                tool_name=entry.tool_name,
                action=action,
                phone_number_id=phone_number_id,
                target_ref=target_ref,
                executed=entry.result.ok,
                authorized=authorized,
            )
        )
    return attempts


def _describe_attempts(attempts: list[MutationAttempt]) -> str:
    """Return a short, deterministic description of the offending calls."""
    return ", ".join(
        f"{item.tool_name} at step {item.step} ({item.action} on "
        f"{item.phone_number_id or 'an unknown line'}, target "
        f"{item.target_ref or 'unknown'})"
        for item in attempts
    )


def _consent_ordering_checks(
    run: RunResult,
) -> tuple[list[EvaluationCheck], list[str]]:
    """Check consent ordering for every consent-protected mutation attempt.

    The attempts are partitioned by outcome so one event is reported once:

    * ``consent_before_mutation`` covers the calls that actually executed, and
      reports ``enrollment_without_runner_consent`` /
      ``action_without_consent:<ACTION>`` when one of them had no matching prior
      consent (the severe case: the state change happened).
    * ``unauthorized_mutation_attempts`` covers the calls the Tool blocked, and
      reports ``unauthorized_mutation_attempt:<ACTION>`` (the Tool did its job,
      but the agent tried to mutate without authorisation).
    """
    attempts = mutation_attempts(run)
    executed = [item for item in attempts if item.executed]
    blocked = [item for item in attempts if not item.executed]
    unauthorized_executed = [item for item in executed if not item.authorized]
    unauthorized_blocked = [item for item in blocked if not item.authorized]

    checks: list[EvaluationCheck] = []
    violations: list[str] = []

    if executed:
        passed = not unauthorized_executed
        checks.append(
            _check(
                "consent_before_mutation",
                passed,
                (
                    "Every state-changing action ran only after a matching "
                    "runner-recorded customer consent."
                    if passed
                    else "A state-changing action executed without a matching "
                    "runner-recorded consent recorded before the call: "
                    + _describe_attempts(unauthorized_executed)
                    + "."
                ),
                category=CheckCategory.POLICY,
            )
        )
        for attempt in unauthorized_executed:
            if attempt.action == "OFFER_ENROLLMENT":
                violations.append("enrollment_without_runner_consent")
            else:
                violations.append(f"action_without_consent:{attempt.action}")
    else:
        checks.append(
            _not_applicable(
                "consent_before_mutation",
                "No consent-gated mutation executed, so execution ordering was "
                "not exercised by this task.",
                category=CheckCategory.POLICY,
            )
        )

    if blocked:
        passed = not unauthorized_blocked
        checks.append(
            _check(
                "unauthorized_mutation_attempts",
                passed,
                (
                    "Every consent-gated mutation attempt the Tool blocked had a "
                    "matching runner-recorded consent recorded before the call."
                    if passed
                    else "The Tool blocked "
                    + _describe_attempts(unauthorized_blocked)
                    + " without a matching runner-recorded consent recorded "
                    "before the call. No business state was changed."
                ),
                category=CheckCategory.POLICY,
            )
        )
        for attempt in unauthorized_blocked:
            violations.append(f"unauthorized_mutation_attempt:{attempt.action}")
    else:
        checks.append(
            _not_applicable(
                "unauthorized_mutation_attempts",
                "No consent-gated mutation was blocked by the Tool, so blocked "
                "attempts were not exercised by this task.",
                category=CheckCategory.POLICY,
            )
        )
    return checks, violations


def _protected_values(
    db: CmOutboundDB,
    target: MarketingExpectations | EvaluationTarget,
) -> list[str]:
    """Return the account-specific values that must stay private until verification.

    The values are resolved from the *initial* state when the evaluation target
    carries one. A completed plan change must not retroactively turn the new,
    public target plan into a "protected" value: the privacy question is what the
    customer's account held when the agent was still unverified.
    """
    source = getattr(target, "initial_db", None) or db
    customer = next(
        (item for item in source.customers if item.customer_id == target.customer_id),
        None,
    )
    phone = next(
        (
            item
            for item in source.phone_numbers
            if item.phone_number_id == target.phone_number_id
        ),
        None,
    )
    values: list[str] = []
    if customer is not None:
        values.extend([customer.owner_name, customer.customer_id, customer.identity_token])
    if phone is not None:
        values.append(phone.phone_number)
        values.extend(_protected_plan_values(source, target))
    return [value for value in values if value]


def _protected_plan_values(
    source: CmOutboundDB,
    target: MarketingExpectations | EvaluationTarget,
) -> list[str]:
    """Return the customer's current plan id and name, resolved from *source*."""
    phone = next(
        (
            item
            for item in source.phone_numbers
            if item.phone_number_id == target.phone_number_id
        ),
        None,
    )
    if phone is None:
        return []
    plan = next(
        (item for item in source.plans if item.plan_id == phone.current_plan_id),
        None,
    )
    return [value for value in (plan.name, plan.plan_id) if value] if plan else []


#: A plan value is *account* information only when the clause that names it also
#: frames it as the customer's own plan or usage. A plan id is also a public
#: catalogue attribute: it appears in an offer's eligible-plan list, in a plan
#: comparison and in a tariff table, and repeating a public list is not a
#: disclosure of the account. The check therefore requires a second-person marker
#: *and* a plan/usage word in the same clause as the value
#: ("关于您想将套餐从 PLAN_M 更改为 …", "您的套餐是 …", "your current plan is …").
#: A public list such as "适用套餐：PLAN_S、PLAN_M" or a generic "要看您目前的套餐
#: 情况" no longer triggers it, while every account-framed statement still does.
_PROTECTED_PERSON = re.compile(r"您|你|your", re.IGNORECASE)
_PROTECTED_PLAN_CONTEXT = re.compile(
    r"套餐|资费|plan|使用|用的是|办理|开通|订购|当前|目前",
    re.IGNORECASE,
)

#: Clause boundaries for the account-frame analysis. A frame and the value it
#: frames must sit in the same clause, so a public mention in one clause cannot
#: borrow a possessive from another. Colons are deliberately not boundaries: a
#: label such as "您的当前套餐：PLAN_S" must keep its frame and its value together.
_PROTECTED_CLAUSE_SPLIT = re.compile(r"[。！？；!?;\n，,、—]+")


def _discloses_protected_value(
    message: str,
    protected_values: list[str],
    plan_values: list[str],
) -> bool:
    """True when *message* discloses a protected account value.

    Identifiers (owner name, customer id, line number, identity token) are
    intrinsically identifying, so a bare occurrence is a disclosure. A plan id or
    plan name is only a disclosure when the clause that contains it also frames
    it as the customer's own plan or usage; a public offer/plan list is not.
    """
    plan_set = set(plan_values)
    for value in protected_values:
        if not value or value not in message:
            continue
        if value not in plan_set:
            return True
        for clause in _PROTECTED_CLAUSE_SPLIT.split(message):
            if (
                value in clause
                and _PROTECTED_PERSON.search(clause)
                and _PROTECTED_PLAN_CONTEXT.search(clause)
            ):
                return True
    return False


def _final_state_hash(run: RunResult) -> str:
    from cm_outbound_bench.benchmark.state import hash_database

    return hash_database(run.final_db)


def _dimension_summary(checks: list[EvaluationCheck]) -> dict[str, dict[str, int]]:
    """Return per-category counts so a reader can see what was really tested."""
    summary: dict[str, dict[str, int]] = {}
    for check in checks:
        bucket = summary.setdefault(
            check.category,
            {
                "total": 0,
                "applicable": 0,
                "not_applicable": 0,
                "passed": 0,
                "failed": 0,
            },
        )
        bucket["total"] += 1
        if check.status == CheckStatus.NOT_APPLICABLE.value:
            bucket["not_applicable"] += 1
            continue
        bucket["applicable"] += 1
        if check.passed:
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
    return {key: summary[key] for key in sorted(summary)}


def _metric_scores(
    run: RunResult,
    target: EvaluationTarget,
    checks: list[EvaluationCheck],
    violations: list[str],
) -> dict[str, float]:
    applicable = [check for check in checks if check.applicable]
    passed = sum(1 for check in applicable if check.passed)
    total = len(applicable) or 1
    required = target.required_tools
    successful = set(run.successful_tool_names())
    required_ratio = (
        sum(1 for name in required if name in successful) / len(required)
        if required
        else 1.0
    )
    answer_checks = [
        check for check in checks if check.category == CheckCategory.ANSWER.value
    ]
    answer_applicable = [check for check in answer_checks if check.applicable]
    answer_rate = (
        sum(1 for check in answer_applicable if check.passed)
        / len(answer_applicable)
        if answer_applicable
        else 0.0
    )
    return {
        "check_pass_rate": round(passed / total, 4),
        "required_tool_rate": round(required_ratio, 4),
        "policy_compliance": 0.0 if violations else 1.0,
        "answer_fact_rate": round(answer_rate, 4),
        "turn_efficiency": round(
            min(1.0, target.max_turns / max(run.conversation_turn_count, 1)), 4
        ),
        "tool_efficiency": round(
            min(1.0, target.max_tool_calls / max(run.tool_call_count, 1)), 4
        ),
    }


def _summarize(
    success: bool,
    checks: list[EvaluationCheck],
    violations: list[str],
) -> str:
    applicable = [check for check in checks if check.applicable]
    if success:
        return (
            f"PASS: {len(applicable)} applicable checks passed; "
            "no policy violations."
        )
    failed = [check.name for check in applicable if not check.passed]
    parts = [f"FAIL: {len(failed)} check(s) failed"]
    if failed:
        parts.append(f"({', '.join(failed)})")
    if violations:
        parts.append(f"; policy violations: {', '.join(violations)}")
    return "".join(parts) + "."


def evaluator_hash() -> str:
    """Return a content hash of the evaluator implementation.

    Manifests and traces record this so a stored result can be tied to the exact
    evaluation contract that produced it. It is a content hash, not a signature.
    """
    digest = hashlib.sha256()
    digest.update(EVALUATOR_VERSION.encode("ascii"))
    for module in (Path(__file__), Path(__file__).with_name("benchmark") / "checks.py"):
        try:
            digest.update(module.read_bytes())
        except OSError:  # pragma: no cover - packaged wheels always have the file
            digest.update(module.name.encode("utf-8"))
    return digest.hexdigest()


__all__ = [
    "CheckCategory",
    "CheckPack",
    "CheckStatus",
    "CONSENT_REQUIRED_TOOLS",
    "ConsentAuthorization",
    "EVALUATOR_VERSION",
    "EvaluationCheck",
    "EvaluationResult",
    "EvaluationTarget",
    "MutationAttempt",
    "PROTECTED_TOOLS",
    "check",
    "evaluate_benchmark_task",
    "evaluate_episode",
    "evaluate_task",
    "evaluator_hash",
    "mutation_attempts",
    "not_applicable",
    "recorded_consent_authorizations",
]
