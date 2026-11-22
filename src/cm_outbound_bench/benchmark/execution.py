"""Execution of declarative benchmark tasks through the shared runtime.

This is the orchestration layer: it resolves the domain, loads the explicit
initial state, validates the task against that state, builds the composed
toolkit, runs the shared conversation loop and evaluates the result.

It also records the reproducibility metadata a stored result needs: real
wall-clock start/end times, monotonic stage durations, task/policy/prompt/
evaluator content hashes, the episode identity and any provider token usage.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cm_outbound_bench.benchmark.domains import (
    check_pack_for_domain,
    get_domain,
    policy_for_domain,
    states_for_domain,
)
from cm_outbound_bench.benchmark.state import InitialState, validate_state_for_line
from cm_outbound_bench.benchmark.tasks import BenchmarkTask
from cm_outbound_bench.benchmark.toolkits import (
    TASK_TOOLS,
    build_toolkit,
    tool_surface_hash,
)
from cm_outbound_bench.evaluator import (
    EvaluationCheck,
    EvaluationResult,
    evaluate_benchmark_task,
    evaluator_hash,
)
from cm_outbound_bench.providers.base import ProviderError
from cm_outbound_bench.runner import (
    EpisodeError,
    EpisodeSpec,
    Runner,
    RunResult,
    StopReason,
)

BENCHMARK_VERSION = "1.0.0"
AGENT_DEMO = "benchmark_demo"

#: Seed handling values recorded in manifests and traces.
SEED_UNUSED_DETERMINISTIC = "unused_deterministic_component"
SEED_APPLIED = "applied"
SEED_IGNORED = "ignored_by_component"
SEED_UNSUPPORTED = "unsupported_by_component"


class TaskValidationError(RuntimeError):
    """Raised when a task does not match its declared initial state."""


def validate_task_against_state(
    task: BenchmarkTask,
    state: InitialState,
) -> list[str]:
    """Return every structural problem between a task and its initial state."""
    problems = validate_state_for_line(state, task.phone_number_id)
    db = state.db

    customer = next(
        (item for item in db.customers if item.customer_id == task.customer_id), None
    )
    if customer is None:
        problems.append("task_customer_missing_from_state")
    line = next(
        (
            item
            for item in db.phone_numbers
            if item.phone_number_id == task.phone_number_id
        ),
        None,
    )
    if line is not None and line.owner_customer_id != task.customer_id:
        problems.append("task_customer_does_not_own_line")
    if task.offer_id is not None and not any(
        item.offer_id == task.offer_id for item in db.offers
    ):
        problems.append("task_offer_missing_from_state")

    consent_steps = [
        step for step in task.simulator.steps if step.consent_action is not None
    ]
    for step in consent_steps:
        action = step.consent_action
        target = step.consent_target.replace("{offer_id}", task.offer_id or "")
        if action is not None and action.value == "OFFER_ENROLLMENT" and not task.offer_id:
            problems.append("offer_consent_without_offer")
        if action is not None and action.value == "RECHARGE":
            account = next(
                (
                    item
                    for item in db.billing_accounts
                    if item.phone_number_id == task.phone_number_id
                ),
                None,
            )
            if account is None or account.account_id != target:
                problems.append("recharge_consent_target_mismatch")
        if action is not None and action.value == "PLAN_CHANGE":
            if not any(item.plan_id == target for item in db.plans):
                problems.append("plan_change_consent_target_unknown")
        if action is not None and action.value == "CANCELLATION":
            known = {item.offer_id for item in db.offers} | {
                item.plan_id for item in db.plans
            }
            if target and target not in known:
                problems.append("cancellation_consent_target_unknown")

    if task.simulator.verification_token:
        if customer is not None and (
            task.simulator.verification_token != customer.identity_token
        ):
            problems.append("simulator_token_does_not_match_customer")
    elif any(
        step.intent.value == "provide_identity" for step in task.simulator.steps
    ):
        problems.append("identity_step_without_token")

    problems.extend(_check_allowed_tools(task))
    return sorted(set(problems))


def _check_allowed_tools(task: BenchmarkTask) -> list[str]:
    known = set(TASK_TOOLS)
    problems: list[str] = []
    for name in task.allowed_tools:
        if name not in known:
            problems.append(f"unknown_allowed_tool:{name}")
    for name in task.required_tools:
        if name not in known:
            problems.append(f"unknown_required_tool:{name}")
    return problems


def load_initial_state(task: BenchmarkTask) -> InitialState:
    store = states_for_domain(task.domain_id)
    try:
        return store.get(task.initial_state_id)
    except KeyError as error:
        raise TaskValidationError(str(error)) from error


def build_episode_spec(task: BenchmarkTask, state: InitialState) -> EpisodeSpec:
    """Assemble the episode specification for one task and initial state.

    Shared by normal execution and by deterministic replay so both drive the same
    simulator, the same allow-list and the same budgets.
    """
    del state  # the state is instantiated by the caller; the spec is state-free
    consent_allowed, consent_action, consent_target = task.consent_capability()
    criteria = task.success_criteria
    return EpisodeSpec(
        task_id=task.task_id,
        agent_view=task.agent_view(task.domain_id.lower()),
        simulator=_build_simulator(task),
        consent_phone_number_id=task.phone_number_id,
        consent_offer_id=task.offer_id,
        consent_allowed=consent_allowed,
        allowed_tools=frozenset(task.allowed_tools),
        initial_state_id=task.initial_state_id,
        metadata={"consent_action": consent_action, "consent_target": consent_target},
        max_turns=criteria.max_turns,
        max_tool_calls=criteria.max_tool_calls,
        max_tool_errors=criteria.max_tool_errors,
        deadline_seconds=criteria.deadline_seconds,
    )


def build_runner(
    task: BenchmarkTask,
    state: InitialState,
    *,
    agent_factory,
    max_steps: int | None = None,
) -> Runner:
    """Build the shared runner for one episode over a fresh state copy."""
    spec_entry = get_domain(task.domain_id)
    return Runner(
        initial_db=state.instantiate(),
        policy=policy_for_domain(task.domain_id),
        agent_factory=agent_factory,
        max_steps=max_steps or spec_entry.max_steps,
        toolkit_factory=build_toolkit,
    )


def hash_policy_text(policy: str) -> str:
    return hashlib.sha256(policy.encode("utf-8")).hexdigest()


def hash_prompt_text(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@dataclass
class EpisodeOutcome:
    """One executed and evaluated episode plus its reproducibility metadata."""

    task: BenchmarkTask
    state: InitialState
    run: RunResult
    evaluation: EvaluationResult
    latency_ms: int
    agent_name: str
    agent_metadata: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    episode_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    setup_ms: int = 0
    episode_ms: int = 0
    evaluation_ms: int = 0
    total_ms: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)
    seed: int | None = None
    seed_status: str = ""
    prompt_hash: str = ""
    prompt_version: str = ""
    policy_hash: str = ""
    task_hash: str = ""
    initial_tools_hash: str = ""
    evaluator_hash: str = ""

    @property
    def success(self) -> bool:
        return self.evaluation.success

    @property
    def run_status(self) -> str:
        """Machine-readable episode status for aggregation."""
        if self.run.stop_reason is StopReason.PROTOCOL_ERROR:
            return "runtime_failure"
        if not self.run.episode_completed:
            return "incomplete"
        return "success" if self.evaluation.success else "evaluation_failure"


def new_run_id() -> str:
    return f"RUN-{uuid.uuid4().hex[:16]}"


def new_episode_id() -> str:
    return f"EP-{uuid.uuid4().hex[:16]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def execute_task(
    task: BenchmarkTask,
    *,
    agent_factory,
    agent_name: str = AGENT_DEMO,
    agent_metadata: dict[str, Any] | None = None,
    max_steps: int | None = None,
    strict: bool = True,
    seed: int | None = None,
    prompt_text: str = "",
) -> EpisodeOutcome:
    """Run one benchmark task end to end with a fresh state copy."""
    metadata = dict(agent_metadata or {})
    total_started = time.perf_counter()
    started_at = _utc_now()

    setup_started = time.perf_counter()
    state = load_initial_state(task)
    problems = validate_task_against_state(task, state)
    if problems and strict:
        raise TaskValidationError(
            f"Task {task.task_id} does not match state {task.initial_state_id}: "
            + ", ".join(problems)
        )

    policy = policy_for_domain(task.domain_id)
    episode = build_episode_spec(task, state)
    runner = build_runner(
        task, state, agent_factory=agent_factory, max_steps=max_steps
    )
    setup_ms = int((time.perf_counter() - setup_started) * 1000)
    episode_started = time.perf_counter()
    protocol_error: ProviderError | None = None
    try:
        run = runner.run(episode)
    except ProviderError as error:
        protocol_error = error
        run = _protocol_failure_result(task, state, error)
    episode_ms = int((time.perf_counter() - episode_started) * 1000)

    evaluation_started = time.perf_counter()
    evaluation = evaluate_benchmark_task(task, run, initial_state=state)
    if protocol_error is not None:
        evaluation = _apply_protocol_error(evaluation, protocol_error)
    evaluation_ms = int((time.perf_counter() - evaluation_started) * 1000)
    total_ms = int((time.perf_counter() - total_started) * 1000)

    usage = dict(run.agent_usage)
    if not usage:
        declared = metadata.get("token_usage")
        if isinstance(declared, dict):
            usage = {str(key): int(value) for key, value in declared.items()}

    same_seed_component = str(metadata.get("seed_component", ""))
    if not prompt_text:
        prompt_text = _default_prompt_text(agent_name, episode.agent_view, policy)
    prompt_version = str(metadata.get("prompt_version", "")) or (
        _default_prompt_version(agent_name)
    )
    return EpisodeOutcome(
        task=task,
        state=state,
        run=run,
        evaluation=evaluation,
        latency_ms=episode_ms + evaluation_ms,
        agent_name=agent_name,
        agent_metadata=metadata,
        run_id=new_run_id(),
        episode_id=new_episode_id(),
        started_at=started_at,
        completed_at=_utc_now(),
        setup_ms=setup_ms,
        episode_ms=episode_ms,
        evaluation_ms=evaluation_ms,
        total_ms=total_ms,
        token_usage=usage,
        seed=seed,
        seed_status=_seed_status(seed, same_seed_component),
        prompt_hash=hash_prompt_text(prompt_text) if prompt_text else "",
        prompt_version=prompt_version,
        policy_hash=hash_policy_text(policy),
        task_hash=task.task_hash(),
        initial_tools_hash=tool_surface_hash(task.allowed_tools),
        evaluator_hash=evaluator_hash(),
    )


def _seed_status(seed: int | None, component: str) -> str:
    """Record whether a requested seed was used, ignored or unsupported."""
    if seed is None:
        return ""
    if component == "provider":
        return SEED_APPLIED
    return SEED_UNUSED_DETERMINISTIC


def _default_prompt_version(agent_name: str) -> str:
    """Return the instruction-set version label for one agent kind."""
    if agent_name == "llm":
        from cm_outbound_bench.llm_agent import PROMPT_VERSION

        return PROMPT_VERSION
    return "scripted-v1"


def _default_prompt_text(agent_name: str, view, policy: str) -> str:
    """Return the agent-facing instruction text whose hash identifies the run."""
    if agent_name == "llm":
        from cm_outbound_bench.llm_agent import build_system_prompt

        return build_system_prompt(view, policy)
    from cm_outbound_bench.benchmark.demo_agent import SCRIPTED_AGENT_PROMPT

    return SCRIPTED_AGENT_PROMPT


def _protocol_failure_result(
    task: BenchmarkTask,
    state: InitialState,
    error: ProviderError,
) -> RunResult:
    """A model that emits a protocol-invalid action fails the episode cleanly."""
    return RunResult(
        task_id=task.task_id,
        stop_reason=StopReason.PROTOCOL_ERROR,
        steps_executed=0,
        conversation=[],
        tool_trace=[],
        consent_events=[],
        final_db=state.instantiate(),
        initial_state_id=task.initial_state_id,
        allowed_tools=sorted(task.allowed_tools),
        error=EpisodeError(
            code=f"agent_protocol_error:{error.code}",
            error_type="ProviderError",
            stage="agent_protocol",
            message=error.message,
        ),
    )


def _apply_protocol_error(
    evaluation: EvaluationResult,
    error: ProviderError,
) -> EvaluationResult:
    """Record an agent protocol violation as a failed, explained episode."""
    code = f"agent_protocol_error:{error.code}"
    violations = sorted(set(evaluation.policy_violations) | {code})
    checks = [
        EvaluationCheck(
            name="agent_protocol",
            passed=False,
            detail=f"The agent produced an invalid action ({error.code}).",
            category="policy",
            status="REQUIRED",
        ),
        *evaluation.checks,
    ]
    return evaluation.model_copy(
        update={
            "success": False,
            "checks": checks,
            "policy_violations": violations,
            "summary": (
                "FAIL: the agent produced a protocol-invalid action "
                f"({error.code}); no episode was executed."
            ),
            "completion_reason": code,
            "partial_completion": 0.0,
        }
    )


def _build_simulator(task: BenchmarkTask):
    from cm_outbound_bench.benchmark.simulator import ScriptedSimulator

    return ScriptedSimulator(task.simulator, offer_id=task.offer_id)


def validate_task_identity(tasks: list[BenchmarkTask]) -> list[str]:
    """Reject task and scenario identifiers that repeat across domain files.

    Per-file uniqueness is enforced by the Pydantic file model; this is the
    catalogue-wide check that stops a duplicate in one domain from silently
    shadowing a task in another.
    """
    problems: list[str] = []
    seen_tasks: dict[str, str] = {}
    seen_scenarios: dict[str, str] = {}
    for task in tasks:
        if task.task_id in seen_tasks:
            problems.append(
                f"duplicate_task_id:{task.task_id} in {task.domain_id} and "
                f"{seen_tasks[task.task_id]}"
            )
        seen_tasks[task.task_id] = task.domain_id
        if task.scenario_id in seen_scenarios:
            problems.append(
                f"duplicate_scenario_id:{task.scenario_id} in {task.domain_id} and "
                f"{seen_scenarios[task.scenario_id]}"
            )
        seen_scenarios[task.scenario_id] = task.domain_id
    return problems


def validate_state_identity(state_ids: list[tuple[str, str]]) -> list[str]:
    """Reject state identifiers that repeat across domain state files."""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for state_id, domain_id in state_ids:
        if state_id in seen:
            problems.append(
                f"duplicate_state_id:{state_id} in {domain_id} and {seen[state_id]}"
            )
        seen[state_id] = domain_id
    return problems


def validate_catalog() -> list[str]:
    """Validate every packaged task against its domain, state and tool set.

    This is the load-time gate: it checks cross-file identity uniqueness, that
    each state expectation names real fields with well-typed values, and that
    every task has an evaluation pack. A malformed task must be rejected here,
    before any episode can abort a batch at evaluation time.
    """
    from cm_outbound_bench.benchmark.domains import (
        all_tasks,
        domain_ids,
        states_for_domain,
    )

    problems: list[str] = []
    tasks = all_tasks()
    problems.extend(validate_task_identity(tasks))
    problems.extend(
        validate_state_identity(
            [
                (state.state_id, domain_id)
                for domain_id in domain_ids()
                for state in states_for_domain(domain_id).list_states()
            ]
        )
    )

    for task in tasks:
        try:
            state = load_initial_state(task)
        except (KeyError, TaskValidationError) as error:
            problems.append(f"{task.task_id}: {error}")
            continue
        for problem in validate_task_against_state(task, state):
            problems.append(f"{task.task_id}: {problem}")
        if check_pack_for_domain(task.domain_id) is None:
            problems.append(f"{task.task_id}: no evaluation pack for domain")
    return problems
