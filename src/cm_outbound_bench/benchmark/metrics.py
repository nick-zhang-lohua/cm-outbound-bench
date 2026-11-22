"""Run manifests, aggregated metrics and cross-run comparison.

A manifest is the compact, machine-readable index record of one benchmark
episode. It never contains API keys, prompts, transcript text, the conversation
or the runtime database: those live in the episode trace that the manifest
references by path and SHA-256.

Two artefacts, two jobs:

* ``trace.json`` — the detailed source of truth for one episode;
* ``RUN-*.json`` — a small summary for searching, filtering and aggregation.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from cm_outbound_bench.benchmark.execution import BENCHMARK_VERSION, EpisodeOutcome
from cm_outbound_bench.evaluator import EVALUATOR_VERSION, evaluator_hash

MANIFEST_VERSION = "2.0"

RunStatus = Literal["success", "evaluation_failure", "runtime_failure", "incomplete"]


class RunFailure(BaseModel):
    """A structured, sanitized record of a task that could not complete."""

    error_code: str
    error_type: str = ""
    stage_of_failure: str = ""
    message: str = ""
    task_id: str = ""
    domain_id: str = ""
    run_id: str = ""


class RunManifest(BaseModel):
    manifest_version: str = MANIFEST_VERSION
    run_id: str
    episode_id: str = ""
    benchmark_version: str = BENCHMARK_VERSION
    domain_id: str
    task_id: str
    task_version: str
    scenario_id: str
    scenario_status: str = "CANDIDATE"
    rule_provenance_status: str = ""
    initial_state_id: str
    initial_state_hash: str
    initial_tools_hash: str = ""
    agent_type: str
    provider_protocol: str = ""
    provider_name: str = ""
    model_name: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    policy_hash: str = ""
    task_hash: str = ""
    evaluator_hash: str = ""
    sampling: dict[str, Any] = Field(default_factory=dict)
    random_seed: int | None = None
    seed_status: str = ""
    repeat_index: int = 0
    repeat_count: int = 1
    started_at: str = ""
    completed_at: str = ""
    setup_ms: int = 0
    episode_ms: int = 0
    evaluation_ms: int = 0
    total_ms: int = 0
    turn_count: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    run_status: RunStatus = "success"
    task_success: bool
    partial_completion: float = 0.0
    metric_scores: dict[str, float] = Field(default_factory=dict)
    dimensions: dict[str, dict[str, int]] = Field(default_factory=dict)
    policy_violations: list[str] = Field(default_factory=list)
    check_results: list[dict[str, Any]] = Field(default_factory=list)
    not_applicable_checks: list[str] = Field(default_factory=list)
    answer_fact_results: list[dict[str, Any]] = Field(default_factory=list)
    final_state_hash: str = ""
    evaluator_version: str = ""
    completion_reason: str = ""
    trace_path: str = ""
    trace_sha256: str = ""
    trace_schema_version: str = ""
    error: RunFailure | None = None


class DomainAggregate(BaseModel):
    domain_id: str
    runs: int
    successes: int
    success_rate: float
    evaluation_failures: int = 0
    runtime_failures: int = 0
    incomplete: int = 0
    policy_violation_rate: float
    mean_turns: float
    mean_tool_calls: float
    mean_latency_ms: float


class TaskAggregate(BaseModel):
    task_id: str
    domain_id: str
    runs: int
    repetitions: int = 0
    successes: int
    success_rate: float
    success_rate_stdev: float = 0.0
    success_rate_ci95: float | None = None
    evaluation_failures: int = 0
    runtime_failures: int = 0
    incomplete: int = 0
    policy_violation_rate: float = 0.0
    mean_turns: float = 0.0
    mean_tool_calls: float = 0.0
    mean_latency_ms: float = 0.0
    failure_categories: dict[str, int] = Field(default_factory=dict)


class AgentAggregate(BaseModel):
    agent_type: str
    provider_name: str = ""
    model_name: str = ""
    runs: int
    successes: int
    success_rate: float
    policy_violation_rate: float = 0.0
    mean_turns: float = 0.0
    mean_tool_calls: float = 0.0
    mean_latency_ms: float = 0.0
    token_usage_totals: dict[str, int] = Field(default_factory=dict)


class AggregateReport(BaseModel):
    manifests: int
    benchmark_version: str = BENCHMARK_VERSION
    task_success_rate: float
    policy_violation_rate: float
    mean_turns: float
    mean_tool_calls: float
    mean_latency_ms: float
    token_usage_totals: dict[str, int] = Field(default_factory=dict)
    run_status_counts: dict[str, int] = Field(default_factory=dict)
    failure_categories: dict[str, int] = Field(default_factory=dict)
    domains: list[DomainAggregate] = Field(default_factory=list)
    tasks: list[TaskAggregate] = Field(default_factory=list)
    agents: list[AgentAggregate] = Field(default_factory=list)
    distinct_agents: list[str] = Field(default_factory=list)
    benchmark_versions: list[str] = Field(default_factory=list)
    evaluator_versions: list[str] = Field(default_factory=list)
    seed_statuses: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TaskComparison(BaseModel):
    task_id: str
    domain_id: str
    left_runs: int
    left_success_rate: float | None = None
    right_runs: int
    right_success_rate: float | None = None
    success_rate_delta: float | None = None
    compatible: bool = True
    incompatibility: str = ""


class ComparisonReport(BaseModel):
    left_dirs: list[str] = Field(default_factory=list)
    right_dirs: list[str] = Field(default_factory=list)
    left_label: str = ""
    right_label: str = ""
    left_manifests: int = 0
    right_manifests: int = 0
    compatible: bool = True
    incompatibilities: list[str] = Field(default_factory=list)
    tasks: list[TaskComparison] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_manifest(
    outcome: EpisodeOutcome,
    *,
    agent_type: str,
    provider_protocol: str = "",
    provider_name: str = "",
    model_name: str = "",
    prompt_version: str = "",
    sampling: dict[str, Any] | None = None,
    random_seed: int | None = None,
    token_usage: dict[str, int] | None = None,
    repeat_index: int = 0,
    repeat_count: int = 1,
) -> RunManifest:
    evaluation = outcome.evaluation
    return RunManifest(
        run_id=outcome.run_id or f"RUN-{uuid.uuid4().hex[:16]}",
        episode_id=outcome.episode_id,
        domain_id=outcome.task.domain_id,
        task_id=outcome.task.task_id,
        task_version=outcome.task.task_version,
        scenario_id=outcome.task.scenario_id,
        scenario_status=outcome.task.scenario_status,
        rule_provenance_status=outcome.task.rule_provenance_status,
        initial_state_id=outcome.state.state_id,
        initial_state_hash=outcome.state.state_hash,
        initial_tools_hash=outcome.initial_tools_hash,
        agent_type=agent_type,
        provider_protocol=provider_protocol,
        provider_name=provider_name,
        model_name=model_name,
        prompt_version=prompt_version or outcome.prompt_version,
        prompt_hash=outcome.prompt_hash,
        policy_hash=outcome.policy_hash,
        task_hash=outcome.task_hash,
        evaluator_hash=outcome.evaluator_hash,
        sampling=dict(sampling or {}),
        random_seed=random_seed if random_seed is not None else outcome.seed,
        seed_status=outcome.seed_status,
        repeat_index=repeat_index,
        repeat_count=repeat_count,
        started_at=outcome.started_at,
        completed_at=outcome.completed_at,
        setup_ms=outcome.setup_ms,
        episode_ms=outcome.episode_ms,
        evaluation_ms=outcome.evaluation_ms,
        total_ms=outcome.total_ms,
        turn_count=evaluation.conversation_turn_count,
        tool_call_count=evaluation.tool_call_count,
        latency_ms=outcome.latency_ms,
        token_usage=dict(token_usage if token_usage is not None else outcome.token_usage),
        run_status=outcome.run_status,
        task_success=evaluation.success,
        partial_completion=evaluation.partial_completion,
        metric_scores=dict(evaluation.metric_scores),
        dimensions=dict(evaluation.dimensions),
        policy_violations=list(evaluation.policy_violations),
        check_results=[
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
                "category": check.category,
                "status": check.status,
            }
            for check in evaluation.checks
        ],
        not_applicable_checks=list(evaluation.not_applicable_checks),
        answer_fact_results=list(evaluation.answer_fact_results),
        final_state_hash=evaluation.final_state_hash,
        evaluator_version=evaluation.evaluator_version,
        completion_reason=evaluation.completion_reason,
    )


def build_failure_manifest(
    *,
    task: Any,
    failure: RunFailure,
    agent_type: str,
    provider_protocol: str = "",
    provider_name: str = "",
    model_name: str = "",
    sampling: dict[str, Any] | None = None,
    random_seed: int | None = None,
    repeat_index: int = 0,
    repeat_count: int = 1,
    initial_state_hash: str = "",
) -> RunManifest:
    """Build a manifest for a task whose execution raised.

    The failure is recorded as data. It is never converted into a pass or a
    partial success.
    """
    return RunManifest(
        run_id=failure.run_id or f"RUN-{uuid.uuid4().hex[:16]}",
        episode_id="",
        domain_id=failure.domain_id or getattr(task, "domain_id", ""),
        task_id=failure.task_id or getattr(task, "task_id", ""),
        task_version=getattr(task, "task_version", ""),
        scenario_id=getattr(task, "scenario_id", ""),
        scenario_status=getattr(task, "scenario_status", "CANDIDATE"),
        rule_provenance_status=getattr(task, "rule_provenance_status", ""),
        initial_state_id=getattr(task, "initial_state_id", ""),
        initial_state_hash=initial_state_hash,
        evaluator_version=EVALUATOR_VERSION,
        evaluator_hash=evaluator_hash(),
        agent_type=agent_type,
        provider_protocol=provider_protocol,
        provider_name=provider_name,
        model_name=model_name,
        sampling=dict(sampling or {}),
        random_seed=random_seed,
        repeat_index=repeat_index,
        repeat_count=repeat_count,
        started_at=_utc_now(),
        completed_at=_utc_now(),
        run_status="runtime_failure",
        task_success=False,
        completion_reason=failure.error_code,
        policy_violations=[],
        error=failure,
    )


def write_manifest(manifest: RunManifest, directory: str | Path) -> Path:
    """Atomically write one manifest and return its path."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{manifest.run_id}.json"
    payload = (
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=target_dir,
        prefix=".manifest-",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def load_manifests(directory: str | Path) -> list[RunManifest]:
    """Load every run manifest in one directory.

    Files are recognised by the ``RUN-`` prefix so an aggregate report or a
    trace written into the same directory is not mistaken for a manifest.
    """
    manifests: list[RunManifest] = []
    for path in sorted(Path(directory).glob("RUN-*.json")):
        try:
            manifests.append(
                RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except Exception as error:  # noqa: BLE001 - one bad file must not hide the rest
            raise ValueError(
                f"Manifest {path.name} is not a valid run manifest: {error}"
            ) from error
    return manifests


def load_manifests_from_dirs(directories: list[str | Path]) -> list[RunManifest]:
    manifests: list[RunManifest] = []
    for directory in directories:
        manifests.extend(load_manifests(directory))
    return manifests


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return round(math.sqrt(variance), 4)


def _ci95(values: list[float]) -> float | None:
    """Return a normal-approximation half-width, or None for tiny samples."""
    if len(values) < 2:
        return None
    return round(1.96 * _stdev(values) / math.sqrt(len(values)), 4)


def _status_counts(manifests: list[RunManifest]) -> dict[str, int]:
    counts: dict[str, int] = {
        "success": 0,
        "evaluation_failure": 0,
        "runtime_failure": 0,
        "incomplete": 0,
    }
    for manifest in manifests:
        counts[manifest.run_status] = counts.get(manifest.run_status, 0) + 1
    return counts


def _failure_categories(manifests: list[RunManifest]) -> dict[str, int]:
    categories: dict[str, int] = {}
    for manifest in manifests:
        if manifest.run_status == "success":
            continue
        code = (
            (manifest.error.error_code if manifest.error is not None else "")
            or manifest.completion_reason
            or manifest.run_status
        )
        categories[code] = categories.get(code, 0) + 1
        for violation in manifest.policy_violations:
            categories[f"violation:{violation}"] = (
                categories.get(f"violation:{violation}", 0) + 1
            )
    return dict(sorted(categories.items()))


def aggregate(manifests: list[RunManifest]) -> AggregateReport:
    if not manifests:
        return AggregateReport(
            manifests=0,
            task_success_rate=0.0,
            policy_violation_rate=0.0,
            mean_turns=0.0,
            mean_tool_calls=0.0,
            mean_latency_ms=0.0,
            notes=["No manifests were found."],
        )

    by_domain: dict[str, list[RunManifest]] = {}
    by_task: dict[str, list[RunManifest]] = {}
    by_agent: dict[tuple[str, str, str], list[RunManifest]] = {}
    for manifest in manifests:
        by_domain.setdefault(manifest.domain_id, []).append(manifest)
        by_task.setdefault(manifest.task_id, []).append(manifest)
        by_agent.setdefault(
            (manifest.agent_type, manifest.provider_name, manifest.model_name), []
        ).append(manifest)

    token_totals: dict[str, int] = {}
    for manifest in manifests:
        for key, value in manifest.token_usage.items():
            token_totals[key] = token_totals.get(key, 0) + int(value)

    def _statuses(items: list[RunManifest]) -> dict[str, int]:
        return _status_counts(items)

    domains = [
        DomainAggregate(
            domain_id=domain_id,
            runs=len(items),
            successes=sum(1 for item in items if item.run_status == "success"),
            success_rate=round(
                sum(1 for item in items if item.run_status == "success") / len(items),
                4,
            ),
            evaluation_failures=_statuses(items)["evaluation_failure"],
            runtime_failures=_statuses(items)["runtime_failure"],
            incomplete=_statuses(items)["incomplete"],
            policy_violation_rate=round(
                sum(1 for item in items if item.policy_violations) / len(items), 4
            ),
            mean_turns=_mean([float(item.turn_count) for item in items]),
            mean_tool_calls=_mean([float(item.tool_call_count) for item in items]),
            mean_latency_ms=_mean([float(item.latency_ms) for item in items]),
        )
        for domain_id, items in sorted(by_domain.items())
    ]

    tasks: list[TaskAggregate] = []
    for task_id, items in sorted(by_task.items()):
        outcomes = [1.0 if item.run_status == "success" else 0.0 for item in items]
        stdev = _stdev(outcomes)
        ci95 = _ci95(outcomes)
        counts = _statuses(items)
        task_categories: dict[str, int] = {}
        for item in items:
            if item.run_status == "success":
                continue
            code = (
                (item.error.error_code if item.error is not None else "")
                or item.completion_reason
                or item.run_status
            )
            task_categories[code] = task_categories.get(code, 0) + 1
        tasks.append(
            TaskAggregate(
                task_id=task_id,
                domain_id=items[0].domain_id,
                runs=len(items),
                repetitions=max(item.repeat_index for item in items) + 1,
                successes=int(sum(outcomes)),
                success_rate=round(sum(outcomes) / len(outcomes), 4),
                success_rate_stdev=stdev,
                success_rate_ci95=ci95,
                evaluation_failures=counts["evaluation_failure"],
                runtime_failures=counts["runtime_failure"],
                incomplete=counts["incomplete"],
                policy_violation_rate=round(
                    sum(1 for item in items if item.policy_violations) / len(items),
                    4,
                ),
                mean_turns=_mean([float(item.turn_count) for item in items]),
                mean_tool_calls=_mean([float(item.tool_call_count) for item in items]),
                mean_latency_ms=_mean([float(item.latency_ms) for item in items]),
                failure_categories=dict(sorted(task_categories.items())),
            )
        )

    agents = [
        AgentAggregate(
            agent_type=agent_type,
            provider_name=provider_name,
            model_name=model_name,
            runs=len(items),
            successes=sum(1 for item in items if item.run_status == "success"),
            success_rate=round(
                sum(1 for item in items if item.run_status == "success") / len(items),
                4,
            ),
            policy_violation_rate=round(
                sum(1 for item in items if item.policy_violations) / len(items), 4
            ),
            mean_turns=_mean([float(item.turn_count) for item in items]),
            mean_tool_calls=_mean([float(item.tool_call_count) for item in items]),
            mean_latency_ms=_mean([float(item.latency_ms) for item in items]),
            token_usage_totals=dict(
                sorted(
                    {
                        key: sum(
                            int(item.token_usage.get(key, 0)) for item in items
                        )
                        for key in {
                            token_key
                            for item in items
                            for token_key in item.token_usage
                        }
                    }.items()
                )
            ),
        )
        for (agent_type, provider_name, model_name), items in sorted(
            by_agent.items()
        )
    ]

    notes = [
        "Aggregates are descriptive. Success rates over fewer than two runs per "
        "task carry no interval estimate.",
        "Runtime failures are counted in the denominator; they are not discarded "
        "and they are never converted into a pass.",
    ]
    if any(item.repeat_count < 2 for item in manifests):
        notes.append(
            "At least one manifest came from a single repetition; repeated-run "
            "stability is not established."
        )

    return AggregateReport(
        manifests=len(manifests),
        task_success_rate=round(
            sum(1 for item in manifests if item.run_status == "success")
            / len(manifests),
            4,
        ),
        policy_violation_rate=round(
            sum(1 for item in manifests if item.policy_violations) / len(manifests),
            4,
        ),
        mean_turns=_mean([float(item.turn_count) for item in manifests]),
        mean_tool_calls=_mean([float(item.tool_call_count) for item in manifests]),
        mean_latency_ms=_mean([float(item.latency_ms) for item in manifests]),
        token_usage_totals=dict(sorted(token_totals.items())),
        run_status_counts=_status_counts(manifests),
        failure_categories=_failure_categories(manifests),
        domains=domains,
        tasks=tasks,
        agents=agents,
        distinct_agents=sorted({item.agent_type for item in manifests}),
        benchmark_versions=sorted({item.benchmark_version for item in manifests}),
        evaluator_versions=sorted(
            {item.evaluator_version for item in manifests if item.evaluator_version}
        ),
        seed_statuses=sorted({item.seed_status for item in manifests if item.seed_status}),
        notes=notes,
    )


def render_markdown(report: AggregateReport) -> str:
    lines = [
        "# Benchmark Aggregate Report",
        "",
        f"Manifests: {report.manifests}",
        f"Benchmark version: {', '.join(report.benchmark_versions) or '-'}",
        f"Evaluator version: {', '.join(report.evaluator_versions) or '-'}",
        f"Agents: {', '.join(report.distinct_agents) or '-'}",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Task success rate | {report.task_success_rate:.4f} |",
        f"| Policy violation rate | {report.policy_violation_rate:.4f} |",
        f"| Mean turns | {report.mean_turns:.2f} |",
        f"| Mean tool calls | {report.mean_tool_calls:.2f} |",
        f"| Mean latency (ms) | {report.mean_latency_ms:.1f} |",
        "",
        "## Run status",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for status, count in report.run_status_counts.items():
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## Per domain",
            "",
            "| Domain | Runs | Success rate | Violation rate | Mean turns |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for domain in report.domains:
        lines.append(
            f"| {domain.domain_id} | {domain.runs} | {domain.success_rate:.4f} | "
            f"{domain.policy_violation_rate:.4f} | {domain.mean_turns:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Per task",
            "",
            "| Task | Domain | Runs | Success rate | Std dev | 95% CI | Failures |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for task in report.tasks:
        ci = "n/a" if task.success_rate_ci95 is None else f"±{task.success_rate_ci95:.4f}"
        failures = task.evaluation_failures + task.runtime_failures + task.incomplete
        lines.append(
            f"| {task.task_id} | {task.domain_id} | {task.runs} | "
            f"{task.success_rate:.4f} | {task.success_rate_stdev:.4f} | "
            f"{ci} | {failures} |"
        )
    if report.failure_categories:
        lines.extend(["", "## Failure categories", "", "| Category | Count |", "|---|---:|"])
        for name, count in report.failure_categories.items():
            lines.append(f"| {name} | {count} |")
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in report.notes)
    return "\n".join(lines) + "\n"


def compare_runs(
    left: list[RunManifest],
    right: list[RunManifest],
    *,
    left_label: str = "left",
    right_label: str = "right",
    left_dirs: list[str] | None = None,
    right_dirs: list[str] | None = None,
) -> ComparisonReport:
    """Compare two sets of manifests and refuse unfair comparisons.

    Compatibility requires the same benchmark version, the same task versions and
    the same initial-state hash for every shared task. Different initial states
    are reported as incompatibilities, never averaged away.
    """
    incompatibilities: list[str] = []
    benchmark_versions = {item.benchmark_version for item in left + right}
    if len(benchmark_versions) > 1:
        incompatibilities.append(
            "benchmark_version differs: " + ", ".join(sorted(benchmark_versions))
        )

    left_tasks = {item.task_id: item for item in left}
    right_tasks = {item.task_id: item for item in right}
    for task_id in sorted(set(left_tasks) & set(right_tasks)):
        first, second = left_tasks[task_id], right_tasks[task_id]
        if first.task_version != second.task_version:
            incompatibilities.append(
                f"{task_id}: task_version differs "
                f"({first.task_version} vs {second.task_version})"
            )
        if first.initial_state_id != second.initial_state_id:
            incompatibilities.append(
                f"{task_id}: initial_state_id differs "
                f"({first.initial_state_id} vs {second.initial_state_id})"
            )
        elif first.initial_state_hash != second.initial_state_hash:
            incompatibilities.append(
                f"{task_id}: initial_state_hash differs "
                f"({first.initial_state_hash[:12]} vs "
                f"{second.initial_state_hash[:12]})"
            )
        if first.task_hash and second.task_hash and first.task_hash != second.task_hash:
            incompatibilities.append(f"{task_id}: task content hash differs")

    def _by_task(items: list[RunManifest]) -> dict[str, list[RunManifest]]:
        grouped: dict[str, list[RunManifest]] = {}
        for item in items:
            grouped.setdefault(item.task_id, []).append(item)
        return grouped

    grouped_left = _by_task(left)
    grouped_right = _by_task(right)
    comparisons: list[TaskComparison] = []
    for task_id in sorted(set(grouped_left) | set(grouped_right)):
        left_items = grouped_left.get(task_id, [])
        right_items = grouped_right.get(task_id, [])

        def _rate(items: list[RunManifest]) -> float | None:
            if not items:
                return None
            return round(
                sum(1 for item in items if item.run_status == "success") / len(items),
                4,
            )

        left_rate = _rate(left_items)
        right_rate = _rate(right_items)
        incompatible = any(task_id in message for message in incompatibilities)
        delta = (
            round(right_rate - left_rate, 4)
            if left_rate is not None and right_rate is not None
            else None
        )
        comparisons.append(
            TaskComparison(
                task_id=task_id,
                domain_id=(left_items or right_items)[0].domain_id,
                left_runs=len(left_items),
                left_success_rate=left_rate,
                right_runs=len(right_items),
                right_success_rate=right_rate,
                success_rate_delta=delta,
                compatible=not incompatible,
                incompatibility=(
                    "conditions differ for this task" if incompatible else ""
                ),
            )
        )

    notes = [
        "A rate difference is descriptive, not a significance test.",
        "A task present on only one side is reported with n/a rates rather than a "
        "zero that would look like a real difference.",
    ]
    return ComparisonReport(
        left_dirs=list(left_dirs or []),
        right_dirs=list(right_dirs or []),
        left_label=left_label,
        right_label=right_label,
        left_manifests=len(left),
        right_manifests=len(right),
        compatible=not incompatibilities,
        incompatibilities=incompatibilities,
        tasks=comparisons,
        notes=notes,
    )


__all__ = [
    "AggregateReport",
    "ComparisonReport",
    "MANIFEST_VERSION",
    "RunFailure",
    "RunManifest",
    "TaskAggregate",
    "aggregate",
    "build_failure_manifest",
    "build_manifest",
    "compare_runs",
    "load_manifests",
    "load_manifests_from_dirs",
    "render_markdown",
    "write_manifest",
]
