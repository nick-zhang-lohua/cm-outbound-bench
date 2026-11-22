"""Command-line surface for the multi-domain benchmark.

Command families
----------------
Legacy marketing (deprecated for new work, kept for regression):
    ``cm-outbound run``, ``cm-outbound run-all``, ``cm-outbound list-tasks``
Multi-domain benchmark (current):
    ``cm-outbound benchmark ...``

The ``benchmark`` family can list, validate, execute, evaluate a saved trace,
replay a saved trace, aggregate existing run directories and compare two sets of
runs. Every executed task is persisted immediately, and one task's failure never
aborts the rest of a batch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from cm_outbound_bench.benchmark import replay as replay_module
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import (
    DOMAIN_SPECS,
    all_tasks,
    domain_ids,
    find_task,
    get_domain,
    tasks_for_domain,
)
from cm_outbound_bench.benchmark.execution import (
    AGENT_DEMO,
    BENCHMARK_VERSION,
    EpisodeOutcome,
    execute_task,
    validate_catalog,
)
from cm_outbound_bench.benchmark.metrics import (
    RunFailure,
    RunManifest,
    aggregate,
    build_failure_manifest,
    build_manifest,
    compare_runs,
    load_manifests_from_dirs,
    render_markdown,
    write_manifest,
)
from cm_outbound_bench.benchmark.trace import (
    TRACE_FILENAME,
    TRACE_SCHEMA_VERSION,
    EpisodeTrace,
    TraceError,
    build_trace,
    load_trace,
    trace_file_sha256,
    write_trace,
)
from cm_outbound_bench.providers.base import ProviderError

#: Environment variable that overrides the default run directory.
RUN_DIR_ENV = "CM_OUTBOUND_RUN_DIR"

MAX_ERROR_MESSAGE = 400


def default_run_dir() -> Path:
    """Return a safe, user-controlled default output directory.

    It is resolved from the current working directory, never from the location of
    the installed package, so an installed CLI cannot write into site-packages.
    """
    configured = os.environ.get(RUN_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / "outputs" / "runs"


DEFAULT_RUN_DIR = default_run_dir()


def add_parser(subparsers: Any) -> None:
    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Run and report the multi-domain benchmark tasks.",
    )
    sub = benchmark_parser.add_subparsers(dest="benchmark_command", required=True)

    sub.add_parser("list-domains", help="List benchmark domains.")
    list_tasks_parser = sub.add_parser("list-tasks", help="List benchmark tasks.")
    list_tasks_parser.add_argument(
        "--domain", default=None, help="Restrict to one domain ID."
    )
    verify_parser = sub.add_parser(
        "verify",
        help="Validate every task against its domain, state and tool set.",
    )
    verify_parser.add_argument("--json", action="store_true", help="JSON output.")

    run_parser = sub.add_parser("run", help="Run benchmark tasks.")
    run_parser.add_argument(
        "--task",
        action="append",
        default=None,
        help="Task ID to run; repeatable. Omit to run every task.",
    )
    run_parser.add_argument(
        "--domain",
        action="append",
        default=None,
        help="Restrict to a domain ID; repeatable.",
    )
    run_parser.add_argument(
        "--agent",
        default="demo",
        choices=["demo", "llm"],
        help="Agent implementation for this run.",
    )
    run_parser.add_argument(
        "--provider", default=None, help="Provider profile label for --agent llm."
    )
    run_parser.add_argument(
        "--provider-config",
        type=Path,
        default=None,
        help="Path to a local provider TOML profile file.",
    )
    run_parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for run manifests and traces "
            f"(default: {RUN_DIR_ENV} or ./outputs/runs)."
        ),
    )
    run_parser.add_argument(
        "--max-steps", type=int, default=None, help="Override the step budget."
    )
    run_parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Run each selected task N times, producing N separate episodes.",
    )
    run_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Recorded experiment seed. It is passed to components that accept "
            "one; the scripted simulator and scripted agent use no RNG."
        ),
    )
    run_parser.add_argument("--json", action="store_true", help="JSON output.")

    evaluate_parser = sub.add_parser(
        "evaluate",
        help="Re-evaluate a saved trace without re-running the agent.",
    )
    evaluate_parser.add_argument(
        "--trace", type=Path, required=True, help="Path to a trace.json file."
    )
    evaluate_parser.add_argument(
        "--historical-diagnostic",
        action="store_true",
        help=(
            "Permit re-evaluating a trace whose recorded task version differs "
            "from the installed catalogue. The result is labelled as a "
            "cross-version diagnostic; the stored trace is never modified and "
            "the observation is never release evidence."
        ),
    )
    evaluate_parser.add_argument("--json", action="store_true", help="JSON output.")

    replay_parser = sub.add_parser(
        "replay",
        help="Deterministically replay a saved trace from its initial state.",
    )
    replay_parser.add_argument(
        "--trace", type=Path, required=True, help="Path to a trace.json file."
    )
    replay_parser.add_argument("--json", action="store_true", help="JSON output.")

    aggregate_parser = sub.add_parser(
        "aggregate",
        help="Aggregate existing run directories without re-running tasks.",
    )
    aggregate_parser.add_argument(
        "--dir",
        action="append",
        type=Path,
        required=True,
        help="Run directory to read; repeatable.",
    )
    aggregate_parser.add_argument("--json", action="store_true", help="JSON output.")
    aggregate_parser.add_argument(
        "--output", type=Path, default=None, help="Write the report to a file."
    )

    compare_parser = sub.add_parser(
        "compare",
        help="Compare two sets of existing run directories.",
    )
    compare_parser.add_argument(
        "--dir",
        action="append",
        type=Path,
        required=True,
        help="Run directory; the first group is compared with the second.",
    )
    compare_parser.add_argument(
        "--split",
        type=int,
        default=None,
        help=(
            "Index at which --dir values switch from the left group to the right "
            "group. Defaults to splitting the list in half."
        ),
    )
    compare_parser.add_argument("--json", action="store_true", help="JSON output.")
    compare_parser.add_argument(
        "--output", type=Path, default=None, help="Write the comparison to a file."
    )


def dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "list-domains": _run_list_domains,
        "list-tasks": _run_list_tasks,
        "verify": _run_verify,
        "run": _run_tasks,
        "evaluate": _run_evaluate_trace,
        "replay": _run_replay_trace,
        "aggregate": _run_aggregate,
        "compare": _run_compare,
    }
    handler = handlers.get(args.benchmark_command)
    if handler is None:  # pragma: no cover - argparse prevents this
        print(
            f"error: unknown benchmark command {args.benchmark_command!r}",
            file=sys.stderr,
        )
        return 2
    return handler(args)


# ---------------------------------------------------------------------------
# Informational commands
# ---------------------------------------------------------------------------


def _run_list_domains(args: argparse.Namespace) -> int:
    for domain_id in domain_ids():
        spec = get_domain(domain_id)
        task_count = len(tasks_for_domain(domain_id))
        print(
            f"{spec.domain_id}\t{spec.domain_class}\t{task_count} task(s)\t"
            f"{spec.name}"
        )
    return 0


def _run_list_tasks(args: argparse.Namespace) -> int:
    tasks = all_tasks()
    if args.domain:
        tasks = [task for task in tasks if task.domain_id == args.domain]
    for task in tasks:
        print(
            f"{task.task_id}\t{task.domain_id}\t{task.scenario_id}\t"
            f"{task.initial_state_id}\t{task.rule_provenance_status}\t"
            f"{task.scenario_status}"
        )
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    problems = validate_catalog()
    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
        return 0 if not problems else 1
    if problems:
        for problem in problems:
            print(f"problem: {problem}", file=sys.stderr)
        print(f"Catalog validation failed with {len(problems)} problem(s).")
        return 1
    print(f"Catalog OK: {len(all_tasks())} tasks across {len(DOMAIN_SPECS)} domains.")
    return 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _run_tasks(args: argparse.Namespace) -> int:
    if args.repeat < 1:
        print("error: --repeat must be at least 1", file=sys.stderr)
        return 2
    try:
        tasks = _select_tasks(args)
        agent_factory, agent_name, provider_info = _build_agent(args, seed=args.seed)
    except (ValueError, KeyError, ProviderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    problems = validate_catalog()
    if problems:
        print("error: catalog validation failed", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else default_run_dir()
    manifests: list[RunManifest] = []
    failures = 0
    for task in tasks:
        for repeat_index in range(args.repeat):
            manifest, failed = _run_one_task(
                task,
                output_dir=output_dir,
                agent_factory=agent_factory,
                agent_name=agent_name,
                provider_info=provider_info,
                max_steps=args.max_steps,
                seed=args.seed,
                repeat_index=repeat_index,
                repeat_count=args.repeat,
                quiet=bool(args.json),
            )
            manifests.append(manifest)
            failures += int(failed)

    report = aggregate(manifests)
    if args.json:
        print(
            json.dumps(
                {
                    "runs": [
                        manifest.model_dump(mode="json") for manifest in manifests
                    ],
                    "aggregate": report.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print()
        print(render_markdown(report))
        print(f"Manifests written to {output_dir}")
        if failures:
            print(f"{failures} of {len(manifests)} episode(s) did not succeed.")
    return 0 if failures == 0 else 1


def _run_one_task(
    task,
    *,
    output_dir: Path,
    agent_factory,
    agent_name: str,
    provider_info: dict[str, Any],
    max_steps: int | None,
    seed: int | None,
    repeat_index: int,
    repeat_count: int,
    quiet: bool,
) -> tuple[RunManifest, bool]:
    """Run one task inside its own failure boundary and persist the result.

    Returns ``(manifest, failed)``. Exceptions are turned into a structured
    runtime-failure manifest; ``KeyboardInterrupt`` and ``SystemExit`` are
    deliberately not caught.
    """
    try:
        outcome = execute_task(
            task,
            agent_factory=agent_factory,
            agent_name=agent_name,
            agent_metadata=provider_info,
            max_steps=max_steps,
            seed=seed,
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:  # noqa: BLE001 - recorded, never swallowed
        failure = RunFailure(
            error_code=_classify_error(error),
            error_type=type(error).__name__,
            stage_of_failure=_failure_stage(error),
            message=_sanitize_message(str(error)),
            task_id=task.task_id,
            domain_id=task.domain_id,
            run_id=f"RUN-{os.urandom(8).hex()}",
        )
        manifest = build_failure_manifest(
            task=task,
            failure=failure,
            agent_type=agent_name,
            provider_protocol=str(provider_info.get("protocol", "")),
            provider_name=str(provider_info.get("provider", "")),
            model_name=str(provider_info.get("model", "")),
            sampling=dict(provider_info.get("sampling", {})),
            random_seed=seed,
            repeat_index=repeat_index,
            repeat_count=repeat_count,
        )
        write_manifest(manifest, output_dir)
        _write_failure_record(manifest, output_dir)
        if not quiet:
            print(
                f"{task.task_id}\tRUNTIME_FAILURE\t{failure.error_code}\t"
                f"{failure.message}",
                file=sys.stderr,
            )
        return manifest, True

    manifest = build_manifest(
        outcome,
        agent_type=agent_name,
        provider_protocol=str(provider_info.get("protocol", "")),
        provider_name=str(provider_info.get("provider", "")),
        model_name=str(provider_info.get("model", "")),
        prompt_version=str(provider_info.get("prompt_version", "")),
        sampling=dict(provider_info.get("sampling", {})),
        random_seed=seed,
        repeat_index=repeat_index,
        repeat_count=repeat_count,
    )
    manifest = _persist_episode(outcome, manifest, output_dir)
    if not quiet:
        _print_manifest(manifest)
    return manifest, manifest.run_status != "success"


def _persist_episode(
    outcome: EpisodeOutcome,
    manifest: RunManifest,
    output_dir: Path,
) -> RunManifest:
    """Write the trace atomically, then write the manifest that references it."""
    trace: EpisodeTrace = build_trace(
        run_id=manifest.run_id,
        episode_id=outcome.episode_id,
        task=outcome.task,
        run=outcome.run,
        evaluation=outcome.evaluation,
        benchmark_version=BENCHMARK_VERSION,
        agent=_trace_agent(manifest),
    )
    run_dir = output_dir / manifest.run_id
    trace_path, _ = write_trace(trace, run_dir)
    file_hash = trace_file_sha256(trace_path)
    manifest = manifest.model_copy(
        update={
            "trace_path": f"{manifest.run_id}/{TRACE_FILENAME}",
            "trace_sha256": file_hash,
            "trace_schema_version": TRACE_SCHEMA_VERSION,
        }
    )
    write_manifest(manifest, output_dir)
    return manifest


def _trace_agent(manifest: RunManifest):
    from cm_outbound_bench.benchmark.trace import TraceAgent

    return TraceAgent(
        agent_type=manifest.agent_type,
        provider_protocol=manifest.provider_protocol,
        provider_name=manifest.provider_name,
        model_name=manifest.model_name,
        prompt_hash=manifest.prompt_hash,
        prompt_version=manifest.prompt_version,
        policy_hash=manifest.policy_hash,
        sampling=dict(manifest.sampling),
        seed=manifest.random_seed,
        seed_status=manifest.seed_status,
        token_usage=dict(manifest.token_usage),
    )


def _write_failure_record(manifest: RunManifest, output_dir: Path) -> Path:
    """Persist a structured failure record for a run that produced no episode."""
    target_dir = output_dir / manifest.run_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "failure.json"
    payload = manifest.model_dump(mode="json")
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def _classify_error(error: BaseException) -> str:
    name = type(error).__name__
    mapping = {
        "TaskValidationError": "task_validation_failed",
        "ValidationError": "task_data_invalid",
        "AttributeError": "state_expectation_invalid",
        "KeyError": "missing_task_or_state",
        "FileNotFoundError": "missing_asset",
        "TimeoutError": "episode_timeout",
    }
    return mapping.get(name, "unexpected_runtime_error")


def _failure_stage(error: BaseException) -> str:
    text = traceback.format_exception_only(type(error), error)[-1].strip()
    if isinstance(error, AttributeError):
        return "evaluation"
    if "state" in text.lower() and "expectation" in text.lower():
        return "evaluation"
    return "execution"


def _sanitize_message(message: str) -> str:
    """Redact credential-shaped content and bound the recorded message."""
    import re

    sanitized = re.sub(
        r"(sk-[A-Za-z0-9]{6,}|Bearer\s+\S+|api[_-]?key\s*[=:]\s*\S+)",
        "[REDACTED]",
        message,
        flags=re.IGNORECASE,
    )
    sanitized = " ".join(sanitized.split())
    if len(sanitized) > MAX_ERROR_MESSAGE:
        sanitized = sanitized[:MAX_ERROR_MESSAGE] + "…"
    return sanitized


def _print_manifest(manifest: RunManifest) -> None:
    status = {
        "success": "PASS",
        "evaluation_failure": "FAIL",
        "runtime_failure": "RUNTIME_FAILURE",
        "incomplete": "INCOMPLETE",
    }.get(manifest.run_status, manifest.run_status.upper())
    print(
        f"{manifest.task_id}\t{status}\tturns={manifest.turn_count}\t"
        f"tools={manifest.tool_call_count}\t"
        f"violations={','.join(manifest.policy_violations) or 'none'}"
    )


def _select_tasks(args: argparse.Namespace):
    if args.task:
        return [find_task(task_id) for task_id in args.task]
    tasks = all_tasks()
    if args.domain:
        domains = set(args.domain)
        tasks = [task for task in tasks if task.domain_id in domains]
    if not tasks:
        raise ValueError("No benchmark tasks selected")
    return tasks


def _build_agent(args: argparse.Namespace, *, seed: int | None = None):
    if args.agent == "demo":
        return (
            BenchmarkDemoAgent,
            AGENT_DEMO,
            {
                "prompt_version": "scripted-v1",
                "seed_component": "deterministic",
                "sampling": {},
            },
        )

    from cm_outbound_bench.llm_agent import PROMPT_VERSION, LLMBackedAgent
    from cm_outbound_bench.providers import (
        create_provider_client,
        default_provider_config_path,
        load_provider_profile,
        resolve_provider_profile,
    )

    if not args.provider:
        raise ValueError("--provider is required with --agent llm")
    config_path = args.provider_config or default_provider_config_path()
    profile = load_provider_profile(config_path, args.provider)
    resolved = resolve_provider_profile(profile)
    seed_component = ""
    if seed is not None:
        if "seed" in resolved.extra_params:
            resolved = resolved.model_copy(update={"extra_params": dict(resolved.extra_params)})
            seed_component = "ignored"
        else:
            extra = dict(resolved.extra_params)
            extra["seed"] = seed
            resolved = resolved.model_copy(update={"extra_params": extra})
            seed_component = "provider"
    info = {
        "provider": args.provider,
        "protocol": getattr(resolved, "api_protocol", "openai_compatible"),
        "model": getattr(resolved, "model", ""),
        "prompt_version": PROMPT_VERSION,
        "sampling": resolved.public_parameters,
        "seed_component": seed_component,
    }

    def factory():
        return LLMBackedAgent(create_provider_client(resolved))

    return factory, "llm", info


# ---------------------------------------------------------------------------
# evaluate / replay
# ---------------------------------------------------------------------------


def _run_evaluate_trace(args: argparse.Namespace) -> int:
    try:
        trace = load_trace(args.trace)
        result = replay_module.evaluate_trace(
            trace,
            allow_task_version_mismatch=bool(
                getattr(args, "historical_diagnostic", False)
            ),
        )
    except TraceError as error:
        return _trace_error(error, args.json)
    payload = {
        "trace": str(args.trace),
        "run_id": trace.run_id,
        "episode_id": trace.episode_id,
        "task_id": trace.task_id,
        "historical_diagnostic": result.historical,
        "task_version_recorded": result.task_version_recorded,
        "task_version_installed": result.task_version_installed,
        "matches_recorded": result.matches_recorded,
        "success": result.evaluation.success,
        "recorded_success": trace.evaluation.success,
        "failed_checks": result.evaluation.failed_checks,
        "policy_violations": result.evaluation.policy_violations,
        "differences": result.differences,
        "evaluation": result.evaluation.model_dump(mode="json"),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Trace: {args.trace}")
        print(f"Task: {trace.task_id}")
        if result.historical:
            print(
                "MODE: cross-version historical diagnostic "
                f"(recorded task version {result.task_version_recorded}, "
                f"installed task version {result.task_version_installed}). "
                "Read-only observation; not release evidence."
            )
        print(f"Result: {'PASS' if result.evaluation.success else 'FAIL'}")
        print(f"Matches recorded result: {result.matches_recorded}")
        if result.differences:
            for difference in result.differences:
                print(f"  difference: {difference}")
        print(f"Summary: {result.evaluation.summary}")
    return 0 if result.evaluation.success else 1


def _run_replay_trace(args: argparse.Namespace) -> int:
    try:
        trace = load_trace(args.trace)
        result = replay_module.replay_trace(trace)
    except TraceError as error:
        return _trace_error(error, args.json)
    except replay_module.TraceReplayError as error:
        print(f"error: replay rejected: {error}", file=sys.stderr)
        return 1
    payload = {
        "trace": str(args.trace),
        "run_id": trace.run_id,
        "task_id": trace.task_id,
        "ok": result.ok,
        "divergences": result.divergences,
        "comparisons": result.comparisons,
        "recorded_final_state_hash": result.recorded_final_state_hash,
        "replayed_final_state_hash": result.replayed_final_state_hash,
        "recorded_success": trace.evaluation.success,
        "replayed_success": result.replayed_evaluation.success,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Trace: {args.trace}")
        print(f"Task: {trace.task_id}")
        print(f"Deterministic replay reproduced the episode: {result.ok}")
        print(
            f"Final state hash: recorded {result.recorded_final_state_hash[:12]}, "
            f"replayed {result.replayed_final_state_hash[:12]}"
        )
        for divergence in result.divergences:
            print(f"  divergence: {divergence}")
    return 0 if result.ok else 1


def _trace_error(error: TraceError, as_json: bool) -> int:
    payload = {
        "ok": False,
        "error_category": type(error).__name__,
        "error": str(error),
    }
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# aggregate / compare
# ---------------------------------------------------------------------------


def _run_aggregate(args: argparse.Namespace) -> int:
    try:
        manifests = load_manifests_from_dirs(args.dir)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not manifests:
        print(f"error: no run manifests found in {args.dir}", file=sys.stderr)
        return 2
    report = aggregate(manifests)
    if args.json:
        text = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
    else:
        text = render_markdown(report)
    if args.output:
        Path(args.output).write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(text)
    return 0


def _run_compare(args: argparse.Namespace) -> int:
    directories = list(args.dir)
    split = args.split
    if split is None:
        if len(directories) < 2:
            print(
                "error: compare needs at least two --dir values, or --split",
                file=sys.stderr,
            )
            return 2
        split = max(1, len(directories) // 2)
    left_dirs, right_dirs = directories[:split], directories[split:]
    if not left_dirs or not right_dirs:
        print("error: --split must leave a directory on each side", file=sys.stderr)
        return 2
    try:
        left = load_manifests_from_dirs(left_dirs)
        right = load_manifests_from_dirs(right_dirs)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not left or not right:
        print("error: both sides need at least one run manifest", file=sys.stderr)
        return 2
    report = compare_runs(
        left,
        right,
        left_label=", ".join(str(path) for path in left_dirs),
        right_label=", ".join(str(path) for path in right_dirs),
        left_dirs=[str(path) for path in left_dirs],
        right_dirs=[str(path) for path in right_dirs],
    )
    if args.json:
        text = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
    else:
        lines = [
            "# Benchmark Agent Comparison",
            "",
            f"Left: {report.left_label} ({report.left_manifests} runs)",
            f"Right: {report.right_label} ({report.right_manifests} runs)",
            f"Comparable conditions: {report.compatible}",
        ]
        if report.incompatibilities:
            lines.extend(["", "## Incompatibilities", ""])
            lines.extend(f"- {item}" for item in report.incompatibilities)
        lines.extend(
            [
                "",
                "| Task | Domain | Left runs | Left rate | Right runs | Right rate | Δ |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for task in report.tasks:
            left_rate = (
                "n/a"
                if task.left_success_rate is None
                else f"{task.left_success_rate:.4f}"
            )
            right_rate = (
                "n/a"
                if task.right_success_rate is None
                else f"{task.right_success_rate:.4f}"
            )
            delta = (
                "n/a"
                if task.success_rate_delta is None
                else f"{task.success_rate_delta:+.4f}"
            )
            lines.append(
                f"| {task.task_id} | {task.domain_id} | {task.left_runs} | "
                f"{left_rate} | {task.right_runs} | {right_rate} | {delta} |"
            )
        lines.extend(["", "## Notes", ""])
        lines.extend(f"- {note}" for note in report.notes)
        text = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Comparison written to {args.output}")
    else:
        print(text)
    return 0 if report.compatible else 1
