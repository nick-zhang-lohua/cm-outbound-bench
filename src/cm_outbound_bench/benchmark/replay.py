"""Deterministic evaluation and replay of a saved episode trace.

Two distinct capabilities share one trace format:

``evaluate_trace``
    Rebuild the evaluation inputs from the persisted trace and re-run the
    deterministic evaluator. No agent and no provider is involved.

``replay_trace``
    Restore the original initial state, apply the recorded normalized agent
    actions in order, reproduce tool execution and simulator replies, and compare
    the rebuilt result with the recorded result.

Nothing here is a cryptographic anti-cheating mechanism. The trace hash detects
accidental or uncoordinated modification; a party that rewrites both the content
and the hash is out of scope for this integrity check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cm_outbound_bench.benchmark.execution import (
    build_episode_spec,
    build_runner,
    load_initial_state,
)
from cm_outbound_bench.benchmark.state import hash_database
from cm_outbound_bench.benchmark.tasks import BenchmarkTask
from cm_outbound_bench.benchmark.trace import (
    EpisodeTrace,
    TraceIntegrityError,
    TraceSchemaError,
)
from cm_outbound_bench.evaluator import EvaluationResult, evaluate_benchmark_task
from cm_outbound_bench.runner import RecordedActionAgent, RunResult

#: Divergence categories reported by a replay.
DIVERGENCE_KINDS: tuple[str, ...] = (
    "task_identity_mismatch",
    "task_version_mismatch",
    "initial_state_mismatch",
    "action_sequence_mismatch",
    "conversation_mismatch",
    "simulator_event_mismatch",
    "tool_trace_mismatch",
    "consent_event_mismatch",
    "stop_reason_mismatch",
    "final_state_mismatch",
    "evaluation_mismatch",
)


class TraceReplayError(Exception):
    """The trace cannot be replayed at all (identity or state mismatch)."""


@dataclass
class EvaluationReplay:
    """The result of re-evaluating a saved trace."""

    trace: EpisodeTrace
    evaluation: EvaluationResult
    matches_recorded: bool
    differences: list[str] = field(default_factory=list)
    #: True when the trace's task version differs from the installed catalogue
    #: and the caller explicitly asked for a historical diagnostic. Such a
    #: result is a labelled read-only observation, never release evidence.
    historical: bool = False
    task_version_recorded: str = ""
    task_version_installed: str = ""

    @property
    def ok(self) -> bool:
        return self.matches_recorded


@dataclass
class ReplayOutcome:
    """The result of deterministically re-executing a recorded episode."""

    trace: EpisodeTrace
    replayed_run: RunResult
    replayed_evaluation: EvaluationResult
    divergences: list[str] = field(default_factory=list)
    comparisons: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.divergences

    @property
    def replayed_final_state_hash(self) -> str:
        return hash_database(self.replayed_run.final_db)

    @property
    def recorded_final_state_hash(self) -> str:
        return self.trace.final.final_state_hash


# ---------------------------------------------------------------------------
# Task resolution
# ---------------------------------------------------------------------------


def resolve_task(
    trace: EpisodeTrace,
    *,
    allow_task_version_mismatch: bool = False,
) -> BenchmarkTask:
    """Resolve the exact task version a trace refers to.

    By default the trace's task version must equal the installed catalogue
    version, because re-evaluating an older recording against a newer
    specification answers a different question than "is this result still
    reproducible?".

    ``allow_task_version_mismatch`` (used only by the explicitly labelled
    ``benchmark evaluate --historical-diagnostic`` path) permits the mismatch so
    an historical failure can be re-read against the current specification
    without rewriting the stored artefact. Task, domain and scenario identity
    are still enforced; only the version equality check is relaxed.
    """
    from cm_outbound_bench.benchmark.domains import find_task

    try:
        task = find_task(trace.task_id)
    except ValueError as error:
        raise TraceSchemaError(
            f"The trace refers to an unknown task {trace.task_id!r}."
        ) from error
    if task.domain_id != trace.domain_id:
        raise TraceSchemaError(
            f"The trace declares domain {trace.domain_id!r} but task "
            f"{task.task_id!r} belongs to {task.domain_id!r}."
        )
    if (
        trace.task_version
        and task.task_version != trace.task_version
        and not allow_task_version_mismatch
    ):
        raise TraceSchemaError(
            f"The trace was produced with task version {trace.task_version!r}; "
            f"the installed catalogue has {task.task_version!r}."
        )
    if trace.scenario_id and task.scenario_id != trace.scenario_id:
        raise TraceSchemaError(
            "The trace's scenario_id does not match the installed task catalogue."
        )
    return task


# ---------------------------------------------------------------------------
# Re-evaluation
# ---------------------------------------------------------------------------


def evaluate_trace(
    trace: EpisodeTrace,
    *,
    task: BenchmarkTask | None = None,
    allow_task_version_mismatch: bool = False,
) -> EvaluationReplay:
    """Re-run the deterministic evaluator against a saved trace.

    The trace is only read. When ``allow_task_version_mismatch`` is set the
    result is marked ``historical`` and carries both version labels, so a caller
    can never mistake a cross-version observation for current evidence.
    """
    resolved = task or resolve_task(
        trace, allow_task_version_mismatch=allow_task_version_mismatch
    )
    historical = bool(
        trace.task_version and trace.task_version != resolved.task_version
    )
    run = trace.to_run_result()
    recomputed = hash_database(run.final_db)
    if trace.final.final_state_hash and recomputed != trace.final.final_state_hash:
        raise TraceIntegrityError(
            "The trace's final database snapshot does not reproduce its recorded "
            "final_state_hash."
        )
    evaluation = evaluate_benchmark_task(
        resolved, run, initial_state=load_initial_state(resolved)
    )

    differences: list[str] = []
    if historical:
        differences.append(
            "cross-version diagnostic: trace task version "
            f"{trace.task_version!r} was re-evaluated against installed task "
            f"version {resolved.task_version!r}; the recorded result is not "
            "rewritten"
        )
    recorded = trace.evaluation
    if evaluation.success != recorded.success:
        differences.append(
            f"success changed: recorded {recorded.success}, "
            f"re-evaluated {evaluation.success}"
        )
    if sorted(evaluation.policy_violations) != sorted(recorded.policy_violations):
        differences.append(
            "policy violations changed: recorded "
            f"{sorted(recorded.policy_violations)}, re-evaluated "
            f"{sorted(evaluation.policy_violations)}"
        )
    if recorded.failed_checks:
        if sorted(evaluation.failed_checks) != sorted(recorded.failed_checks):
            differences.append(
                "failed checks changed: recorded "
                f"{sorted(recorded.failed_checks)}, re-evaluated "
                f"{sorted(evaluation.failed_checks)}"
            )
    if evaluation.final_state_hash != recorded_reference_hash(trace):
        differences.append("final state hash changed between record and re-evaluation")
    return EvaluationReplay(
        trace=trace,
        evaluation=evaluation,
        matches_recorded=not differences,
        differences=differences,
        historical=historical,
        task_version_recorded=trace.task_version or "",
        task_version_installed=resolved.task_version,
    )


def recorded_reference_hash(trace: EpisodeTrace) -> str:
    return trace.final.final_state_hash


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


def replay_trace(
    trace: EpisodeTrace,
    *,
    task: BenchmarkTask | None = None,
) -> ReplayOutcome:
    """Re-execute the recorded action sequence from the original initial state."""
    resolved = task or resolve_task(trace)

    state = load_initial_state(resolved)
    if trace.initial_state_id and state.state_id != trace.initial_state_id:
        raise TraceReplayError(
            f"The trace started from state {trace.initial_state_id!r}; the task "
            f"catalogue resolves {state.state_id!r}."
        )
    if trace.initial_state_hash and state.state_hash != trace.initial_state_hash:
        raise TraceIntegrityError(
            "The recorded initial state hash does not match the installed state "
            f"({state.state_hash} != {trace.initial_state_hash})."
        )

    spec = build_episode_spec(resolved, state)
    replayed_agent = RecordedActionAgent(list(trace.actions))
    runner = build_runner(
        resolved, state, agent_factory=lambda: replayed_agent
    )
    replayed = runner.run(spec)
    evaluation = evaluate_benchmark_task(
        resolved, replayed, initial_state=state
    )

    divergences: list[str] = []
    comparisons: dict[str, bool] = {}

    def _compare(kind: str, same: bool) -> None:
        comparisons[kind] = same
        if not same:
            divergences.append(kind)

    _compare(
        "action_sequence_mismatch",
        _same_actions(trace, replayed),
    )
    _compare(
        "conversation_mismatch",
        _dump(trace.conversation) == _dump(replayed.conversation),
    )
    _compare(
        "simulator_event_mismatch",
        _dump(trace.simulator_events) == _dump(replayed.simulator_events),
    )
    _compare("tool_trace_mismatch", _same_tool_trace(trace, replayed))
    _compare(
        "consent_event_mismatch",
        _dump(trace.consent_events) == _dump(replayed.consent_events)
        and _dump(trace.action_consent_events)
        == _dump(replayed.action_consent_events),
    )
    _compare(
        "stop_reason_mismatch",
        trace.stop.stop_reason == replayed.stop_reason.value,
    )
    _compare(
        "final_state_mismatch",
        hash_database(replayed.final_db) == trace.final.final_state_hash,
    )
    _compare(
        "evaluation_mismatch",
        evaluation.success == trace.evaluation.success
        and sorted(evaluation.policy_violations)
        == sorted(trace.evaluation.policy_violations),
    )

    return ReplayOutcome(
        trace=trace,
        replayed_run=replayed,
        replayed_evaluation=evaluation,
        divergences=divergences,
        comparisons=comparisons,
    )


def _dump(items: Any) -> str:
    import json

    def _one(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        return item

    return json.dumps(
        [_one(item) for item in items],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _same_actions(trace: EpisodeTrace, replayed: RunResult) -> bool:
    recorded = [
        (
            action.step,
            action.action_type,
            action.message,
            action.tool_name,
            _canonical(action.arguments),
            action.call_id,
        )
        for action in trace.actions
    ]
    replayed_keys = [
        (
            action.step,
            action.action_type,
            action.message,
            action.tool_name,
            _canonical(action.arguments),
            action.call_id,
        )
        for action in replayed.actions
    ]
    return recorded == replayed_keys


def _same_tool_trace(trace: EpisodeTrace, replayed: RunResult) -> bool:
    recorded = [
        (
            entry.step,
            entry.tool_name,
            _canonical(entry.arguments),
            entry.result.ok,
            entry.result.error.code if entry.result.error is not None else "",
            _canonical(entry.result.data),
        )
        for entry in trace.tool_calls
    ]
    replayed_keys = [
        (
            entry.step,
            entry.tool_name,
            _canonical(entry.arguments),
            entry.result.ok,
            entry.result.error.code if entry.result.error is not None else "",
            _canonical(entry.result.data),
        )
        for entry in replayed.tool_trace
    ]
    return recorded == replayed_keys


def _canonical(payload: Any) -> str:
    import json

    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


__all__ = [
    "DIVERGENCE_KINDS",
    "EvaluationReplay",
    "ReplayOutcome",
    "TraceReplayError",
    "evaluate_trace",
    "replay_trace",
    "resolve_task",
]
