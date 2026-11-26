"""Minimum benchmark runner for Group A (no workflow, no flowchart).

For every selected task the runner:

1. loads the **agent-visible payload only** and rebuilds the task from it;
2. builds the versioned baseline prompt;
3. creates a **fresh agent instance**, so no state can cross a task boundary;
4. executes exactly one decision;
5. evaluates the execution (technical always, business only if a verified
   reference exists);
6. writes an immutable trace and appends one index entry.

The runner knows nothing about the provider. It depends only on the
``DecisionAgent`` protocol and an :class:`AgentRunInfo` describing what to record.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

from pydantic import Field

from cm_outbound_bench.decisions.agent import AGENT_VERSION
from cm_outbound_bench.decisions.agent_prompt import (
    PROMPT_VERSION,
    build_agent_request,
    prompt_bytes,
    prompt_sha256,
)
from cm_outbound_bench.decisions.evaluator import (
    EVALUATOR_VERSION,
    aggregate_evaluations,
    evaluate_invocation,
)
from cm_outbound_bench.decisions.schema import (
    BENCHMARK_VERSION,
    AgentInvocation,
    AgentRequest,
    PublicDecisionTask,
)
from cm_outbound_bench.decisions.store import TaskStore
from cm_outbound_bench.decisions.trace import (
    CONDITION_BASELINE,
    RunSetManifest,
    TraceIndexEntry,
    TraceManager,
    TraceRecord,
    new_run_id,
    utc_now,
)
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.providers.base import redact_secrets

#: Splits a development run may use. Test requires an explicit opt-in.
DEVELOPMENT_RUN_SPLITS = ("train_f", "train_r", "dev")


class RunnerConfig(DomainModel):
    """Selection policy for one run."""

    split: str = "dev"
    task_limit: int | None = Field(default=None, ge=1)
    task_ids: list[str] = Field(default_factory=list)
    allow_test: bool = False
    run_set_label: str = "baseline-a"


class RunnerError(RuntimeError):
    """Raised when a run cannot be started."""


@dataclass(frozen=True)
class AgentRunInfo:
    """What to record about the agent implementation. Never holds a secret."""

    label: str
    model_name: str
    model_parameters: dict[str, object] = field(default_factory=dict)
    config_hash: str = ""
    agent_version: str = AGENT_VERSION
    base_url_host: str = ""
    secrets: tuple[str, ...] = ()


class BenchmarkRunner:
    """Execute Group A over an explicitly selected split."""

    def __init__(
        self,
        store: TaskStore,
        agent_factory: Callable[[], object],
        trace_manager: TraceManager,
        *,
        agent_info: AgentRunInfo,
        split_manifest_sha256: str = "",
        tasks_artifact_sha256: str = "",
        dataset_version: str = "",
    ) -> None:
        self._store = store
        self._agent_factory = agent_factory
        self._trace_manager = trace_manager
        self._agent_info = agent_info
        self._split_manifest_sha256 = split_manifest_sha256
        self._tasks_artifact_sha256 = tasks_artifact_sha256
        self._dataset_version = dataset_version

    def select_task_ids(self, config: RunnerConfig) -> list[str]:
        """Resolve the task list for one run and enforce the split policy."""
        if config.split not in DEVELOPMENT_RUN_SPLITS and not config.allow_test:
            raise RunnerError(
                f"split {config.split!r} is isolated: pass allow_test explicitly to "
                "run it, and do not use it for development or tuning"
            )
        if config.task_ids:
            selected = list(dict.fromkeys(config.task_ids))
            for task_id in selected:
                actual_split = self._store.split_of(task_id)
                if actual_split != config.split:
                    raise RunnerError(
                        f"task {task_id} belongs to split {actual_split!r}, "
                        f"not {config.split!r}"
                    )
        else:
            selected = self._store.task_ids_for_split(config.split)
        if not selected:
            raise RunnerError(f"no tasks available in split {config.split!r}")
        if config.task_limit is not None:
            selected = selected[: config.task_limit]
        return selected

    def run(self, config: RunnerConfig) -> RunSetManifest:
        """Execute every selected task and write the run-set manifest."""
        task_ids = self.select_task_ids(config)
        started_at = utc_now()
        entries: list[TraceIndexEntry] = []
        evaluations = []

        for task_id in task_ids:
            public_payload = self._store.agent_visible_payload(task_id)
            # Rebuild the task from the guarded payload, so the prompt is derived
            # from exactly the fields the loader allows through.
            task = PublicDecisionTask.model_validate(public_payload)
            request = build_agent_request(task)
            reference = self._store.require_reference(task_id)
            invocation = self._invoke(request)
            evaluation = evaluate_invocation(task, reference, invocation)
            evaluations.append(evaluation)

            trace = TraceRecord(
                run_id=new_run_id(),
                run_set_id=self._trace_manager.run_set_id,
                task_id=task_id,
                condition=CONDITION_BASELINE,
                benchmark_version=BENCHMARK_VERSION,
                dataset_version=self._dataset_version,
                split=reference.split,
                executed_at=utc_now(),
                model_provider=self._agent_info.label,
                model_name=self._agent_info.model_name,
                model_parameters=dict(self._agent_info.model_parameters),
                agent_version=self._agent_info.agent_version,
                prompt_version=PROMPT_VERSION,
                evaluator_version=EVALUATOR_VERSION,
                input_hash=prompt_sha256(request),
                input_bytes=prompt_bytes(request),
                config_hash=self._agent_info.config_hash,
                agent_output=(
                    invocation.output.model_dump(mode="json")
                    if invocation.output is not None
                    else None
                ),
                raw_output_text=(
                    None if evaluation.technical.output_valid else invocation.raw_text
                ),
                evaluation=evaluation,
                latency_ms=invocation.latency_ms,
                token_usage=invocation.token_usage,
                attempts=invocation.attempts,
                execution_status=evaluation.technical.execution_status,
                error=(
                    {
                        "code": invocation.error_code,
                        "message": _redact(
                            invocation.error_message or "", self._agent_info.secrets
                        ),
                    }
                    if invocation.error_code
                    else None
                ),
            )
            path = self._trace_manager.write_trace(trace)
            entries.append(
                TraceIndexEntry(
                    run_id=trace.run_id,
                    task_id=task_id,
                    split=reference.split,
                    execution_status=trace.execution_status,
                    scoring_status=evaluation.business.scoring_status,
                    latency_ms=trace.latency_ms,
                    total_tokens=trace.token_usage.get("total_tokens"),
                    trace_file=path.name,
                )
            )

        aggregate = aggregate_evaluations(evaluations)
        manifest = RunSetManifest(
            run_set_id=self._trace_manager.run_set_id,
            condition=CONDITION_BASELINE,
            benchmark_version=BENCHMARK_VERSION,
            dataset_version=self._dataset_version,
            split=config.split,
            created_at=started_at,
            completed_at=utc_now(),
            agent_label=self._agent_info.label,
            model_name=self._agent_info.model_name,
            base_url_host=self._agent_info.base_url_host,
            model_parameters=dict(self._agent_info.model_parameters),
            config_hash=self._agent_info.config_hash,
            prompt_version=PROMPT_VERSION,
            evaluator_version=EVALUATOR_VERSION,
            agent_version=self._agent_info.agent_version,
            split_manifest_sha256=self._split_manifest_sha256,
            tasks_artifact_sha256=self._tasks_artifact_sha256,
            tasks_requested=len(task_ids),
            tasks_executed=len(entries),
            tasks_failed=sum(
                1 for entry in entries if entry.execution_status != "success"
            ),
            aggregate=aggregate,
            entries=entries,
            notes=list(aggregate.notes),
        )
        self._trace_manager.write_manifest(manifest)
        return manifest

    def _invoke(self, request: AgentRequest) -> AgentInvocation:
        """Create one independent agent session and invoke it exactly once.

        Every returned invocation has its error text redacted **here**, before it is
        evaluated. Redacting only at trace-writing time would leave the raw message
        inside ``technical.error_message``, which is part of the persisted
        evaluation.
        """
        started = time.monotonic()
        try:
            agent = self._agent_factory()
        except Exception as error:  # pragma: no cover - factory is ours
            return AgentInvocation(
                status="provider_error",
                error_code="agent_initialization_failed",
                error_message=_redact(str(error), self._agent_info.secrets),
                latency_ms=_elapsed_ms(started),
            )
        try:
            invocation = agent.decide(request)
        except Exception as error:
            return AgentInvocation(
                status="provider_error",
                error_code="agent_exception",
                error_message=_redact(
                    f"{type(error).__name__}: {error}", self._agent_info.secrets
                ),
                latency_ms=_elapsed_ms(started),
            )
        if not isinstance(invocation, AgentInvocation):
            return AgentInvocation(
                status="provider_error",
                error_code="invalid_agent_return",
                error_message="agent did not return an AgentInvocation",
                latency_ms=_elapsed_ms(started),
            )
        return _redacted_invocation(invocation, self._agent_info.secrets)


def _redact(message: str, secrets: Sequence[str]) -> str:
    return redact_secrets(message, list(secrets))[:300]


def _redacted_invocation(
    invocation: AgentInvocation, secrets: Sequence[str]
) -> AgentInvocation:
    """Return the invocation with any secret removed from its error text.

    The validated agent output is kept as-is; only the free-text error message is
    normalised, because that is the only field that can carry a provider response.
    """
    if not secrets or not invocation.error_message:
        return invocation
    redacted = _redact(invocation.error_message, secrets)
    if redacted == invocation.error_message:
        return invocation
    return invocation.model_copy(update={"error_message": redacted})


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.monotonic() - started) * 1000)))


__all__ = [
    "DEVELOPMENT_RUN_SPLITS",
    "AgentRunInfo",
    "BenchmarkRunner",
    "RunnerConfig",
    "RunnerError",
]
