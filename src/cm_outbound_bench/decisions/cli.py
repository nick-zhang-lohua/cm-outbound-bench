"""Command line for the decision-point benchmark pipeline.

Eight commands mirror the pipeline stages:

``validate-data``  validate the de-identified corpus
``build-calls``    reconstruct complete calls
``split``          build a call-level Train-F / Train-R / Dev / Test split
``build-tasks``    build decision-point task candidates
``check``          validate the public and private task artifacts
``run``            run the Group A baseline on one development split
``evaluate``       re-evaluate a run set from its traces
``report``         export an experiment report

Output policy: status lines contain identifiers, counts and rates only. They never
print conversation text, prompts, agent output or credentials. Artifacts that do
contain text are written to git-ignored local paths.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from cm_outbound_bench.corpus.manifest import CorpusInputError, validate_inputs
from cm_outbound_bench.decisions import paths
from cm_outbound_bench.decisions.agent import (
    AGENT_VERSION,
    AgentConfigError,
    OpenAICompatibleChatAgent,
    ScriptedDecisionAgent,
    load_agent_config,
)
from cm_outbound_bench.decisions.audit import audit_task_prefixes
from cm_outbound_bench.decisions.builder import DecisionTaskBuilder
from cm_outbound_bench.decisions.cli_extra import (
    add_parsers as add_extra_parsers,
)
from cm_outbound_bench.decisions.cli_extra import dispatch_extra
from cm_outbound_bench.decisions.config import (
    ConfigError,
    load_dataset_config,
    load_split_config,
)
from cm_outbound_bench.decisions.evaluator import aggregate_evaluations
from cm_outbound_bench.decisions.runner import (
    AgentRunInfo,
    BenchmarkRunner,
    RunnerConfig,
    RunnerError,
)
from cm_outbound_bench.decisions.sampling import (
    SamplingError,
    load_sampling_plan,
)
from cm_outbound_bench.decisions.schema import BENCHMARK_VERSION
from cm_outbound_bench.decisions.store import (
    PRIVATE_REFERENCES_FILENAME,
    PUBLIC_TASKS_FILENAME,
    TASK_MANIFEST_FILENAME,
    TaskArtifactError,
    TaskStore,
    iter_references,
    validate_task_artifacts,
    write_task_artifacts,
)
from cm_outbound_bench.decisions.trace import (
    TraceError,
    TraceManager,
    iter_traces,
    new_run_set_id,
    utc_now,
)
from cm_outbound_bench.decisions.verification import verify_trace_inputs
from cm_outbound_bench.transcripts.ingestion import (
    TranscriptCorpusError,
    dataset_version,
    iter_units,
    read_dataset_manifest,
)
from cm_outbound_bench.transcripts.models import NormalizedCall
from cm_outbound_bench.transcripts.reconstruction import reconstruct_calls
from cm_outbound_bench.transcripts.splitting import (
    ALL_SPLITS,
    SplitError,
    assign_splits,
    load_split_manifest,
    save_split_manifest,
    verify_split_manifest,
)
from cm_outbound_bench.transcripts.store import (
    CALLS_MANIFEST_FILENAME,
    CallArtifactError,
    iter_calls,
    iter_fingerprints,
    sha256_file,
    write_calls,
)
from cm_outbound_bench.transcripts.validation import CorpusValidator

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def add_parser(subparsers: Any) -> None:
    """Register the ``decision`` command group."""
    parser = subparsers.add_parser(
        "decision",
        help="Real-transcript decision-point benchmark (Group A baseline).",
        description=(
            "Build and run the real China Mobile outbound decision-point "
            "benchmark. Group A only: no flowchart, no workflow, no simulator."
        ),
    )
    sub = parser.add_subparsers(dest="decision_command", required=True)

    validate = sub.add_parser("validate-data", help="Validate the corpus.")
    _add_corpus_option(validate)
    validate.add_argument("--json", action="store_true", help="JSON output.")

    build = sub.add_parser("build-calls", help="Reconstruct complete calls.")
    _add_corpus_option(build)
    build.add_argument("--out", type=Path, default=None, help="Output calls.jsonl.")
    build.add_argument(
        "--limit", type=int, default=None, help="Only process the first N units."
    )
    build.add_argument("--json", action="store_true", help="JSON output.")

    split = sub.add_parser("split", help="Build a call-level grouped split.")
    split.add_argument("--calls", type=Path, default=None, help="calls.jsonl path.")
    split.add_argument("--seed", type=int, default=None, help="Override the seed.")
    split.add_argument("--out", type=Path, default=None, help="Split manifest path.")
    split.add_argument("--json", action="store_true", help="JSON output.")

    tasks = sub.add_parser("build-tasks", help="Build decision-point tasks.")
    tasks.add_argument("--calls", type=Path, default=None, help="calls.jsonl path.")
    tasks.add_argument(
        "--split-manifest", type=Path, default=None, help="Split manifest path."
    )
    tasks.add_argument("--out-dir", type=Path, default=None, help="Task artifact dir.")
    tasks.add_argument(
        "--max-per-call", type=int, default=None, help="Max tasks per call."
    )
    tasks.add_argument(
        "--min-gap", type=int, default=None, help="Min speech turns between cutoffs."
    )
    tasks.add_argument(
        "--limit-calls", type=int, default=None, help="Only process the first N calls."
    )
    tasks.add_argument("--json", action="store_true", help="JSON output.")

    check = sub.add_parser("check", help="Validate task artifacts and the split.")
    check.add_argument("--tasks-dir", type=Path, default=None, help="Task artifact dir.")
    check.add_argument(
        "--split-manifest", type=Path, default=None, help="Split manifest path."
    )
    check.add_argument(
        "--calls",
        type=Path,
        default=None,
        help="calls.jsonl for the prefix audit (defaults to the artifact path).",
    )
    check.add_argument(
        "--skip-prefix-audit",
        action="store_true",
        help="Skip the prefix-leakage audit against the reconstructed calls.",
    )
    check.add_argument("--json", action="store_true", help="JSON output.")

    run = sub.add_parser("run", help="Run the Group A baseline.")
    run.add_argument(
        "--split",
        choices=list(ALL_SPLITS),
        default="dev",
        help="Split to run. Test requires --allow-test.",
    )
    run.add_argument("--task-limit", type=int, default=None, help="First N tasks.")
    run.add_argument(
        "--task-id", action="append", default=[], help="Explicit task id (repeatable)."
    )
    run.add_argument(
        "--sample-plan",
        type=Path,
        default=None,
        help=(
            "Run exactly the tasks of a saved sampling plan. The plan must target "
            "the split being run, otherwise the runner refuses."
        ),
    )
    run.add_argument(
        "--agent",
        choices=["scripted", "live"],
        default="scripted",
        help="scripted = offline pipeline smoke test; live = configured LLM endpoint.",
    )
    run.add_argument("--agent-config", type=Path, default=None, help="Agent TOML.")
    run.add_argument(
        "--tasks-dir", type=Path, default=None, help="Task artifact directory."
    )
    run.add_argument(
        "--split-manifest", type=Path, default=None, help="Split manifest path."
    )
    run.add_argument(
        "--output-dir", type=Path, default=None, help="Trace root directory."
    )
    run.add_argument("--label", default="baseline-a", help="Run-set label.")
    run.add_argument(
        "--allow-test",
        action="store_true",
        help="Explicitly allow a run on the isolated Test split.",
    )
    run.add_argument("--json", action="store_true", help="JSON output.")

    evaluate = sub.add_parser("evaluate", help="Re-evaluate a run set from traces.")
    evaluate.add_argument("--run-set", type=Path, required=True, help="Run-set dir.")
    evaluate.add_argument(
        "--tasks-dir",
        type=Path,
        default=None,
        help="Task artifact dir; enables prompt-hash verification against traces.",
    )
    evaluate.add_argument("--json", action="store_true", help="JSON output.")

    report = sub.add_parser("report", help="Export an experiment report.")
    report.add_argument("--run-set", type=Path, required=True, help="Run-set dir.")
    report.add_argument("--out", type=Path, default=None, help="Report path.")
    report.add_argument("--json", action="store_true", help="JSON output.")

    # Milestone 1.1: governance audits, annotation workflow and the business
    # baseline report. Registered here so the operator sees a single pipeline.
    add_extra_parsers(sub)

    # Milestone 1.2: DeepSeek V4 Pro gold-label annotation, freezing and the blind
    # Group A output evaluation. Registered here so the operator sees a single
    # pipeline.
    from cm_outbound_bench.decisions.gold.cli import (
        add_parsers as add_gold_parsers,
    )

    add_gold_parsers(sub)


def _add_corpus_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="De-identified corpus directory (defaults to CM_OUTBOUND_CORPUS_DIR).",
    )


def dispatch(args: argparse.Namespace) -> int:
    """Dispatch one ``decision`` subcommand."""
    handlers = {
        "validate-data": _run_validate_data,
        "build-calls": _run_build_calls,
        "split": _run_split,
        "build-tasks": _run_build_tasks,
        "check": _run_check,
        "run": _run_baseline,
        "evaluate": _run_evaluate,
        "report": _run_report,
    }
    handler = handlers.get(args.decision_command)
    if handler is None:
        extra = dispatch_extra(args)
        if extra is not None:
            return extra
        from cm_outbound_bench.decisions.gold.cli import dispatch_gold

        gold = dispatch_gold(args)
        if gold is not None:
            return gold
        # pragma: no cover - argparse enforces the set
        print(f"error: unknown decision command {args.decision_command!r}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return handler(args)
    except (
        AgentConfigError,
        CallArtifactError,
        ConfigError,
        CorpusInputError,
        RunnerError,
        SamplingError,
        SplitError,
        TaskArtifactError,
        TraceError,
        TranscriptCorpusError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE


# ---------------------------------------------------------------------------
# validate-data
# ---------------------------------------------------------------------------


def _run_validate_data(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    corpus_dir = paths.resolve_corpus_dir(config, args.corpus)
    validation = validate_inputs(corpus_dir)
    if not validation.ok:
        print(
            "error: corpus does not match its manifest: " + ", ".join(validation.errors),
            file=sys.stderr,
        )
        return EXIT_FAILED

    version = dataset_version(corpus_dir)
    validator = CorpusValidator(
        near_duplicate_max_hamming=config.near_duplicate_max_hamming
    )
    units = 0
    for call in reconstruct_calls(
        iter_units(corpus_dir), corpus=version, corpus_schema_version="1.0"
    ):
        units += 1
        validator.add(call)
    report = validator.finish()

    manifest = read_dataset_manifest(corpus_dir)
    expected = {
        "units": validation.expected_units,
        "records": validation.expected_records,
        "quarantine_units": validation.expected_quarantine_units,
    }
    counts_match = units == validation.expected_units
    payload = {
        "corpus_dir": corpus_dir.name,
        "dataset_version": version,
        "manifest_counts": expected,
        "manifest_counts_match": counts_match,
        "observed_units": units,
        "validation": report.model_dump(mode="json"),
        "source_manifest_generated_at": manifest.get("generated_at"),
        "pipeline_version": manifest.get("pipeline_version"),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"corpus: {corpus_dir.name} ({version})")
        print(f"manifest counts: {expected}")
        print(f"units read: {units} (match={counts_match})")
        print(f"records: {report.records_total}")
        print(f"speech records: {report.speech_records_total}")
        print(f"event records: {report.event_records_total}")
        print(f"channels: {report.channel_record_counts}")
        print(f"data status: {report.data_status_counts}")
        print(f"quality flags: {report.flag_counts}")
        print(f"calls with both speakers: {report.calls_with_both_speakers}")
        print(f"task-eligible calls: {report.calls_task_eligible}")
        print(
            "duplicate groups: "
            f"{report.duplicate_groups} "
            f"(calls in groups: {report.calls_in_duplicate_groups})"
        )
        print(
            "duration ms: p50={p50} p90={p90} max={max}".format(
                p50=report.duration_ms_p50,
                p90=report.duration_ms_p90,
                max=report.duration_ms_max,
            )
        )
        print(f"issues: {[issue.code for issue in report.issues]}")
    return EXIT_OK if counts_match else EXIT_FAILED


# ---------------------------------------------------------------------------
# build-calls
# ---------------------------------------------------------------------------


def _run_build_calls(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    corpus_dir = paths.resolve_corpus_dir(config, args.corpus)
    version = dataset_version(corpus_dir)
    target = args.out or paths.calls_artifact_path(config)

    def _calls() -> Iterator[NormalizedCall]:
        seen = 0
        for call in reconstruct_calls(
            iter_units(corpus_dir), corpus=version, corpus_schema_version="1.0"
        ):
            if args.limit is not None and seen >= args.limit:
                break
            seen += 1
            yield call

    manifest = write_calls(target, _calls())
    manifest["dataset_version"] = version
    manifest["reconstruction"] = {
        "channel_mapping": {"1": "historical_agent", "2": "customer"},
        "unit_equals_call": True,
        "unit_equals_call_evidence": (
            "Format audit 3.6: monotonic timestamps, no overlap, no clock reset, "
            "max 191.8 s, distinct content hashes; no call_id exists in the source."
        ),
    }
    manifest_path = target.parent / CALLS_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.json:
        _print_json(manifest)
    else:
        print(f"calls written: {manifest['calls']}")
        print(
            "status: valid={v} suspect={s} invalid={i}".format(
                v=manifest["calls_valid"],
                s=manifest["calls_suspect"],
                i=manifest["calls_invalid"],
            )
        )
        print(f"artifact: {target}")
        print(f"sha256: {manifest['sha256']}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------


def _run_split(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    split_config = load_split_config()
    calls_path = args.calls or paths.calls_artifact_path(config)
    target = args.out or paths.split_manifest_path(config)
    seed = split_config.seed if args.seed is None else args.seed

    dataset = ""
    calls_manifest = calls_path.parent / CALLS_MANIFEST_FILENAME
    if calls_manifest.is_file():
        dataset = json.loads(calls_manifest.read_text(encoding="utf-8")).get(
            "dataset_version", ""
        )

    manifest = assign_splits(
        iter_fingerprints(calls_path),
        seed=seed,
        targets=split_config.targets,
        near_duplicate_max_hamming=config.near_duplicate_max_hamming,
        dataset_version=dataset,
    )
    save_split_manifest(target, manifest)
    if not manifest.verification.ok:
        print(
            "error: split verification failed: "
            + ", ".join(manifest.verification.failures()),
            file=sys.stderr,
        )
        return EXIT_FAILED

    payload = {
        "split_manifest": str(target),
        "seed": manifest.seed,
        "eligible_calls_total": manifest.eligible_calls_total,
        "excluded_invalid_calls": manifest.excluded_invalid_calls,
        "groups_total": manifest.groups_total,
        "calls_in_duplicate_groups": manifest.calls_in_duplicate_groups,
        "calls_by_split": manifest.calls_by_split,
        "groups_by_split": manifest.groups_by_split,
        "actual_percentage": manifest.actual_percentage,
        "target_calls_by_split": manifest.target_calls_by_split,
        "deviation_percentage_points": manifest.deviation_percentage_points,
        "development_pool_percentage": manifest.development_pool_percentage,
        "test_percentage": manifest.test_percentage,
        "verification": manifest.verification.model_dump(),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"seed: {manifest.seed}")
        print(f"eligible calls: {manifest.eligible_calls_total}")
        print(f"duplicate groups: {manifest.groups_total}")
        print(f"calls by split: {manifest.calls_by_split}")
        print(f"actual %: {manifest.actual_percentage}")
        print(f"target calls: {manifest.target_calls_by_split}")
        print(f"deviation (pp): {manifest.deviation_percentage_points}")
        print(f"verification: {manifest.verification.model_dump()}")
        print(f"manifest: {target}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# build-tasks
# ---------------------------------------------------------------------------


def _run_build_tasks(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    calls_path = args.calls or paths.calls_artifact_path(config)
    split_path = args.split_manifest or paths.split_manifest_path(config)
    out_dir = args.out_dir or paths.tasks_dir(config)

    split_manifest = load_split_manifest(split_path)
    if not split_manifest.verification.ok:
        print(
            "error: refusing to build tasks from an unverified split: "
            + ", ".join(split_manifest.verification.failures()),
            file=sys.stderr,
        )
        return EXIT_FAILED

    builder_config = config.task_construction.model_copy(deep=True)
    if args.max_per_call is not None:
        builder_config.max_tasks_per_call = args.max_per_call
    if args.min_gap is not None:
        builder_config.min_cutoff_gap = args.min_gap
    builder = DecisionTaskBuilder(builder_config)
    split_by_call = split_manifest.split_by_call

    def _pairs() -> Iterator[Any]:
        for index, call in enumerate(iter_calls(calls_path)):
            if args.limit_calls is not None and index >= args.limit_calls:
                break
            split = split_by_call.get(call.call_id)
            if split is None:
                continue
            yield from builder.add_call(call, split=split)

    public_path = out_dir / PUBLIC_TASKS_FILENAME
    reference_path = out_dir / PRIVATE_REFERENCES_FILENAME
    counts = write_task_artifacts(public_path, reference_path, _pairs())

    stats = builder.stats
    manifest = {
        "artifact_version": counts["artifact_version"],
        "benchmark_version": BENCHMARK_VERSION,
        "dataset_version": split_manifest.dataset_version,
        "generated_at": utc_now(),
        "builder": stats.model_dump(mode="json"),
        "builder_config": builder_config.model_dump(mode="json"),
        "split_manifest_sha256": sha256_file(split_path),
        "tasks": counts["tasks"],
        "tasks_by_split": counts["tasks_by_split"],
        "public_sha256": counts["public_sha256"],
        "private_sha256": counts["private_sha256"],
        "public_filename": PUBLIC_TASKS_FILENAME,
        "private_filename": PRIVATE_REFERENCES_FILENAME,
        "scoring_status_default": "unscored",
        "annotation_status_default": "pending",
        "notes": [
            "Every task is a CANDIDATE built from a real conversation prefix.",
            "The historical agent's next response is stored as an observed "
            "reference only and is never a gold label.",
            "No verified business rule or human annotation exists yet, so every "
            "task is unscored.",
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / TASK_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if args.json:
        _print_json(manifest)
    else:
        print(f"tasks built: {counts['tasks']}")
        print(f"tasks by split: {counts['tasks_by_split']}")
        print(f"calls with tasks: {stats.calls_with_tasks}")
        print(f"calls skipped: {stats.calls_skipped}")
        print(f"rejections: {stats.rejection_counts}")
        print(f"tasks per call: {stats.tasks_per_call_counts}")
        print(f"public: {public_path}")
        print(f"private: {reference_path}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _run_check(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    tasks_dir = args.tasks_dir or paths.tasks_dir(config)
    split_path = args.split_manifest or paths.split_manifest_path(config)
    public_path = tasks_dir / PUBLIC_TASKS_FILENAME
    reference_path = tasks_dir / PRIVATE_REFERENCES_FILENAME

    artifacts = validate_task_artifacts(public_path, reference_path)
    split_manifest = load_split_manifest(split_path)
    split_verification = verify_split_manifest(split_manifest)

    cross_errors: list[str] = []
    references = list(iter_references(reference_path))
    for reference in references:
        expected_split = split_manifest.split_by_call.get(reference.call_id)
        if expected_split is None:
            cross_errors.append(f"{reference.task_id}: call missing from split manifest")
        elif expected_split != reference.split:
            cross_errors.append(
                f"{reference.task_id}: split disagrees with split manifest"
            )

    payload = {
        "artifacts": artifacts.model_dump(mode="json"),
        "split_verification": split_verification.model_dump(mode="json"),
        "cross_check_errors": cross_errors,
        "ok": artifacts.ok and split_verification.ok and not cross_errors,
    }

    prefix_audit = None
    calls_path = args.calls or paths.calls_artifact_path(config)
    if not args.skip_prefix_audit and calls_path.is_file():
        prefix_audit = audit_task_prefixes(
            public_path,
            reference_path,
            calls_path,
            split_by_call=split_manifest.split_by_call,
        )
        payload["prefix_audit"] = prefix_audit.model_dump(mode="json")
        payload["ok"] = payload["ok"] and prefix_audit.ok
    elif not args.skip_prefix_audit:
        payload["prefix_audit"] = {"skipped": "calls artifact not found"}
        payload["ok"] = False

    if args.json:
        _print_json(payload)
    else:
        print(f"public tasks: {artifacts.public_tasks}")
        print(f"private references: {artifacts.private_references}")
        print(f"tasks by split: {artifacts.tasks_by_split}")
        print(f"artifact validation ok: {artifacts.ok}")
        if artifacts.errors:
            print(f"artifact errors: {artifacts.errors}")
        print(f"split verification ok: {split_verification.ok}")
        if not split_verification.ok:
            print(f"split failures: {split_verification.failures()}")
        print(f"cross-check errors: {len(cross_errors)}")
        if prefix_audit is not None:
            print(f"prefix audit tasks: {prefix_audit.tasks_checked}")
            print(f"prefix audit failures: {prefix_audit.failures}")
            if prefix_audit.failures:
                print(
                    "prefix audit detail: "
                    f"cutoff_not_speech={prefix_audit.cutoff_not_speech} "
                    f"cutoff_seq={prefix_audit.cutoff_seq_mismatch} "
                    f"prefix={prefix_audit.visible_prefix_mismatch} "
                    f"length={prefix_audit.visible_length_mismatch} "
                    f"tail={prefix_audit.visible_tail_mismatch} "
                    f"next_action={prefix_audit.next_action_mismatch} "
                    f"hidden_segment={prefix_audit.hidden_segment_leak} "
                    f"call_id_leak={prefix_audit.call_id_leak} "
                    f"segment_leak={prefix_audit.segment_id_leak} "
                    f"split={prefix_audit.split_mismatch} "
                    f"missing_calls={prefix_audit.missing_calls}"
                )
        elif not args.skip_prefix_audit:
            print(
                "prefix audit: NOT RUN (calls artifact not found); "
                "pass --calls or --skip-prefix-audit"
            )
        print("OK" if payload["ok"] else "FAILED")
    return EXIT_OK if payload["ok"] else EXIT_FAILED


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _run_baseline(args: argparse.Namespace) -> int:
    config = load_dataset_config()
    tasks_dir = args.tasks_dir or paths.tasks_dir(config)
    store = TaskStore.load(tasks_dir)
    split_path = args.split_manifest or paths.split_manifest_path(config)

    if args.agent == "live":
        resolved = load_agent_config(args.agent_config)

        def factory() -> OpenAICompatibleChatAgent:
            return OpenAICompatibleChatAgent(resolved)

        agent_info = AgentRunInfo(
            label=resolved.label,
            model_name=resolved.model,
            model_parameters=resolved.public_parameters,
            config_hash=resolved.config_hash,
            agent_version=AGENT_VERSION,
            base_url_host=resolved.base_url_host,
            secrets=tuple(resolved.secret_values()),
        )
    else:
        factory = ScriptedDecisionAgent
        agent_info = AgentRunInfo(
            label="scripted",
            model_name="none",
            model_parameters={},
            config_hash="",
            agent_version=AGENT_VERSION,
            base_url_host="",
            secrets=(),
        )

    output_root = args.output_dir or paths.traces_root(config)
    run_set_id = new_run_set_id(args.label)
    trace_manager = TraceManager(
        output_root, run_set_id, secrets=agent_info.secrets
    )
    sampled_task_ids = list(args.task_id)
    plan_digest = ""
    if args.sample_plan is not None:
        plan = load_sampling_plan(args.sample_plan)
        if plan.split != args.split:
            raise RunnerError(
                f"sample plan targets split {plan.split!r}, not {args.split!r}"
            )
        sampled_task_ids.extend(plan.task_ids)
        plan_digest = plan.selection_sha256
    runner = BenchmarkRunner(
        store,
        factory,
        trace_manager,
        agent_info=agent_info,
        split_manifest_sha256=(
            sha256_file(split_path) if split_path.is_file() else ""
        ),
        tasks_artifact_sha256=_tasks_sha256(tasks_dir),
        dataset_version=_dataset_version(tasks_dir),
    )
    manifest = runner.run(
        RunnerConfig(
            split=args.split,
            task_limit=args.task_limit,
            task_ids=sampled_task_ids,
            allow_test=args.allow_test,
            run_set_label=args.label,
        )
    )
    if plan_digest:
        manifest.notes.append(f"sample_plan_selection_sha256: {plan_digest}")
        trace_manager.write_manifest(manifest)

    if args.json:
        _print_json(
            {
                "run_set_id": manifest.run_set_id,
                "split": manifest.split,
                "directory": str(trace_manager.directory),
                "tasks": manifest.tasks_requested,
                "aggregate": manifest.aggregate.model_dump(mode="json"),
            }
        )
    else:
        print(f"run set: {manifest.run_set_id}")
        print(f"split: {manifest.split}")
        print(f"tasks executed: {manifest.tasks_executed}")
        print(f"execution status: {manifest.aggregate.execution_status_counts}")
        print(f"technical: {manifest.aggregate.technical}")
        print(f"scoring status: {manifest.aggregate.scoring_status_counts}")
        print(
            "decision_success_rate: "
            f"{manifest.aggregate.business_metrics.get('decision_success')}"
        )
        print(f"scored tasks: {manifest.aggregate.scored_tasks}")
        print(f"traces: {trace_manager.directory}")
    return EXIT_OK


def _tasks_sha256(tasks_dir: Path) -> str:
    public = tasks_dir / PUBLIC_TASKS_FILENAME
    return sha256_file(public) if public.is_file() else ""


def _dataset_version(tasks_dir: Path) -> str:
    manifest_path = tasks_dir / TASK_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return ""
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return str(payload.get("dataset_version", ""))


# ---------------------------------------------------------------------------
# evaluate / report
# ---------------------------------------------------------------------------


def _run_evaluate(args: argparse.Namespace) -> int:
    traces = iter_traces(args.run_set)
    evaluations = [trace.evaluation for trace in traces]
    aggregate = aggregate_evaluations(evaluations)
    payload = {
        "run_set": args.run_set.name,
        "traces": len(traces),
        "aggregate": aggregate.model_dump(mode="json"),
    }
    verification = None
    if args.tasks_dir is not None:
        verification = verify_trace_inputs(traces, TaskStore.load(args.tasks_dir))
        payload["trace_input_verification"] = verification.model_dump(mode="json")
    (args.run_set / "evaluation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.json:
        _print_json(payload)
    else:
        print(f"traces: {len(traces)}")
        print(f"execution status: {aggregate.execution_status_counts}")
        print(f"technical: {aggregate.technical}")
        print(f"scoring status: {aggregate.scoring_status_counts}")
        print(f"decision_success_rate: {aggregate.business_metrics.get('decision_success')}")
        print(f"scored tasks: {aggregate.scored_tasks}")
        if verification is not None:
            print(f"trace input verification: {verification.model_dump()}")
        for note in aggregate.notes:
            print(f"note: {note}")
    if verification is not None and not verification.ok:
        return EXIT_FAILED
    return EXIT_OK


def _run_report(args: argparse.Namespace) -> int:
    traces = iter_traces(args.run_set)
    if not traces:
        print("error: no traces found", file=sys.stderr)
        return EXIT_FAILED
    manifest_path = args.run_set / "run_set_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    aggregate = aggregate_evaluations([trace.evaluation for trace in traces])
    first = traces[0]
    report = {
        "report_version": "1.0",
        "generated_from": args.run_set.name,
        "condition": first.condition,
        "benchmark_version": first.benchmark_version,
        "dataset_version": first.dataset_version,
        "split": first.split,
        "model_provider": first.model_provider,
        "model_name": first.model_name,
        "model_parameters": first.model_parameters,
        "prompt_version": first.prompt_version,
        "evaluator_version": first.evaluator_version,
        "agent_version": first.agent_version,
        "config_hash": first.config_hash,
        "run_set": manifest,
        "aggregate": aggregate.model_dump(mode="json"),
    }
    output = args.out or (paths.reports_dir(load_dataset_config()) / f"{args.run_set.name}.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render_markdown(report), encoding="utf-8")
    json_output = output.with_suffix(".json")
    json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.json:
        _print_json({"report": str(output), "aggregate": report["aggregate"]})
    else:
        print(f"report: {output}")
        print(f"report json: {json_output}")
        print(f"tasks: {aggregate.tasks_total}")
        print(f"decision_success_rate: {aggregate.business_metrics.get('decision_success')}")
        print(f"scored tasks: {aggregate.scored_tasks}")
        print(f"unscored tasks: {aggregate.unscored_tasks}")
    return EXIT_OK


def _render_markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    technical = aggregate.get("technical", {})
    lines = [
        f"# Baseline run report — {report['generated_from']}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Condition | {report['condition']} (no workflow, no flowchart) |",
        f"| Benchmark | {report['benchmark_version']} |",
        f"| Dataset | {report['dataset_version']} |",
        f"| Split | {report['split']} |",
        f"| Provider label | {report['model_provider']} |",
        f"| Model | {report['model_name']} |",
        f"| Model parameters | `{json.dumps(report['model_parameters'], ensure_ascii=False)}` |",
        f"| Prompt version | {report['prompt_version']} |",
        f"| Evaluator version | {report['evaluator_version']} |",
        f"| Config hash | `{report['config_hash']}` |",
        "",
        "## Technical evaluation",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tasks executed | {aggregate['tasks_total']} |",
        f"| Request success rate | {technical.get('request_success_rate')} |",
        f"| Valid output rate | {technical.get('valid_output_rate')} |",
        f"| Timeout rate | {technical.get('timeout_rate')} |",
        f"| Latency p50 / p90 (ms) | {technical.get('latency_ms_p50')} / {technical.get('latency_ms_p90')} |",
        f"| Tasks with token usage | {technical.get('tasks_with_token_usage')} |",
        f"| Total tokens | {technical.get('total_tokens')} |",
        "",
        "## Business evaluation",
        "",
        "| Metric | Value | Denominator |",
        "|---|---|---|",
    ]
    for metric, value in aggregate.get("business_metrics", {}).items():
        denominator = aggregate.get("business_metric_denominators", {}).get(metric, 0)
        shown = "not available" if value is None else value
        lines.append(f"| {metric} | {shown} | {denominator} |")
    lines.extend(
        [
            "",
            f"Scoring status counts: `{json.dumps(aggregate.get('scoring_status_counts', {}), ensure_ascii=False)}`",
            "",
            f"Scored tasks: {aggregate['scored_tasks']}; unscored: {aggregate['unscored_tasks']}; "
            f"candidate: {aggregate['candidate_tasks']}; invalid: {aggregate['invalid_tasks']}",
            "",
        ]
    )
    unavailable = aggregate.get("unavailable_metrics", {})
    if unavailable:
        lines.append("Unavailable metrics (no verified reference or no tool contract):")
        lines.append("")
        for name, reason in unavailable.items():
            lines.append(f"- `{name}` — {reason}")
        lines.append("")
    if aggregate.get("notes"):
        lines.append("## Notes")
        lines.append("")
        for note in aggregate["notes"]:
            lines.append(f"- {note}")
        lines.append("")
    lines.extend(
        [
            "## Integrity",
            "",
            "This report is generated from the traces in the run-set directory. It "
            "contains aggregate measurements only: no conversation text, no prompt "
            "text and no credential.",
            "",
        ]
    )
    return "\n".join(lines)


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


__all__ = ["add_parser", "dispatch"]
