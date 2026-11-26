"""Milestone 1.2 command group: LLM-gold annotation, freeze, Group A scoring.

Commands registered under the existing ``decision`` group:

    decision annotation inspect|pilot|train-r|dev|validate|freeze
    decision evaluate-group-a
    decision report-group-a

Test access is structurally blocked: the pipeline only loads Train-R and Dev
sampling plans, and ``allow_test`` does not exist here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from cm_outbound_bench.decisions import paths
from cm_outbound_bench.decisions.config import load_dataset_config
from cm_outbound_bench.decisions.gold.annotator import (
    AnnotationContext,
    recompare_stage,
    run_annotation_stage,
)
from cm_outbound_bench.decisions.gold.audit import (
    build_overlap_report,
    summarize_stage,
    tasks_artifact_hash,
)
from cm_outbound_bench.decisions.gold.config import (
    JudgeConfig,
    JudgeConfigError,
    load_judge_config,
    resolve_api_key,
    resolve_header_values,
)
from cm_outbound_bench.decisions.gold.models import FinalGoldState
from cm_outbound_bench.decisions.gold.provider import JudgeClient
from cm_outbound_bench.decisions.gold.storage import (
    FEEDBACK_DIRNAME,
    FROZEN_MANIFEST_FILENAME,
    OVERLAP_REPORT_FILENAME,
    PILOT_DIRNAME,
    RUN_MANIFEST_FILENAME,
    GoldStorage,
    StorageError,
    utc_now,
)
from cm_outbound_bench.decisions.gold.validation import (
    FreezeError,
    apply_finals,
    validate_final,
    write_annotation_summary,
    write_frozen_manifest,
    write_references,
)
from cm_outbound_bench.decisions.sampling import load_sampling_plan
from cm_outbound_bench.decisions.scoring.cli import ScoringCLIError
from cm_outbound_bench.decisions.store import (
    TaskStore,
    iter_public_tasks,
    iter_references,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

MAIN_DIRNAME = "main"

#: Stage layout per dataset.
STAGE_PLAN: dict[str, tuple[str, str, str]] = {
    # command -> (split, stage dirname, sample plan filename)
    "pilot": ("train_r", PILOT_DIRNAME, "train_r_annotation_pilot.json"),
    "train-r": ("train_r", FEEDBACK_DIRNAME, "train_r_feedback_sample.json"),
    "dev": ("dev", MAIN_DIRNAME, "dev_baseline_evaluation_sample.json"),
}


def add_parsers(sub: Any) -> None:
    annotation = sub.add_parser(
        "annotation",
        help="DeepSeek V4 Pro gold-label annotation pipeline (Train-R/Dev only).",
    )
    annotation_sub = annotation.add_subparsers(
        dest="annotation_command", required=True
    )

    inspect_parser = annotation_sub.add_parser(
        "inspect", help="Report the configured judge, samples and existing work."
    )
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.add_argument("--config", type=Path, default=None)

    for name in ("pilot", "train-r", "dev"):
        parser = annotation_sub.add_parser(
            name, help=f"Annotate the {STAGE_PLAN[name][2]} dataset."
        )
        _add_common_run_options(parser)
        parser.add_argument("--config", type=Path, default=None)

    validate_parser = annotation_sub.add_parser(
        "validate",
        help="Validate every finalized gold annotation against its visible task.",
    )
    validate_parser.add_argument("--json", action="store_true")

    freeze_parser = annotation_sub.add_parser(
        "freeze", help="Freeze Train-R and Dev Gold Labels into immutable artifacts."
    )
    freeze_parser.add_argument("--json", action="store_true")
    freeze_parser.add_argument("--config", type=Path, default=None)

    evaluate = sub.add_parser(
        "evaluate-group-a",
        help="Blind two-pass V4 Pro scoring of the existing Group A outputs.",
    )
    _add_common_run_options(evaluate)
    evaluate.add_argument("--config", type=Path, default=None)
    evaluate.add_argument(
        "--run-set",
        type=Path,
        default=None,
        help="Group A run-set directory (defaults to the frozen 400-task run).",
    )
    evaluate.add_argument("--bootstrap-seed", type=int, default=None)
    evaluate.add_argument(
        "--reaggregate",
        action="store_true",
        help=(
            "Re-derive finals from the saved scorer passes and saved "
            "adjudications only (no API calls); used to re-apply the aggregation "
            "rules to an existing evaluation."
        ),
    )

    report = sub.add_parser(
        "report-group-a",
        help="Regenerate the Group A business baseline reports from saved results.",
    )
    report.add_argument("--json", action="store_true")


def _add_common_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=None, help="Override the judge model id.")
    parser.add_argument(
        "--prompt-version", default=None, help="Override the prompt version."
    )
    parser.add_argument("--resume", action="store_true", help="Reuse cached records.")
    parser.add_argument("--limit", type=int, default=None, help="Smoke-test first N.")
    parser.add_argument("--dry-run", action="store_true", help="No API calls.")
    parser.add_argument("--seed", type=int, default=None, help="Recorded seed.")
    parser.add_argument(
        "--concurrency", type=int, default=None, help="Parallel API workers."
    )
    parser.add_argument("--json", action="store_true", help="JSON output.")
    parser.add_argument(
        "--recompare",
        action="store_true",
        help=(
            "Re-derive comparisons and finals from cached pass records only "
            "(no pass API calls); used after a refined comparison rubric."
        ),
    )


def dispatch_gold(args: argparse.Namespace) -> int | None:
    """Dispatch one Milestone 1.2 subcommand, or return None when not ours."""
    command = getattr(args, "decision_command", None)
    if command != "annotation":
        if command == "evaluate-group-a":
            return _run_evaluate_group_a(args)
        if command == "report-group-a":
            return _run_report_group_a(args)
        return None
    handlers = {
        "inspect": _run_inspect,
        "pilot": _run_annotate,
        "train-r": _run_annotate,
        "dev": _run_annotate,
        "validate": _run_validate,
        "freeze": _run_freeze,
    }
    handler = handlers.get(args.annotation_command)
    if handler is None:
        return None
    try:
        return handler(args)
    except (JudgeConfigError, StorageError, FreezeError, ScoringCLIError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _judge_config(args: argparse.Namespace) -> JudgeConfig:
    config = load_judge_config(getattr(args, "config", None))
    if getattr(args, "model", None):
        config = config.model_copy(update={"model": args.model})
    if getattr(args, "prompt_version", None):
        annotation = config.annotation.model_copy(
            update={"prompt_version": args.prompt_version}
        )
        evaluation = config.evaluation.model_copy(
            update={"prompt_version": args.prompt_version}
        )
        config = config.model_copy(
            update={"annotation": annotation, "evaluation": evaluation}
        )
    config.model_checked()  # raises on any unverified substitution
    return config


def _client_factory(config: JudgeConfig) -> Callable[[], JudgeClient]:
    def factory() -> JudgeClient:
        api_key = resolve_api_key(config)
        headers = resolve_header_values(config)
        return JudgeClient(config).with_credentials(api_key, headers)

    return factory


def _storage() -> tuple[GoldStorage, TaskStore]:
    dataset_config = load_dataset_config()
    store = TaskStore.load(paths.tasks_dir(dataset_config))
    storage = GoldStorage.default(
        dataset_version=_dataset_of_tasks(paths.tasks_dir(dataset_config)),
        tasks_artifact_sha256=tasks_artifact_hash(paths.tasks_dir(dataset_config)),
    )
    return storage, store


def _dataset_of_tasks(tasks_dir: Path) -> str:
    manifest = tasks_dir / "tasks.manifest.json"
    if manifest.is_file():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return str(payload.get("dataset_version", "cm_outbound_v2"))
    return "cm_outbound_v2"


def _plan_tasks(stage_key: str, limit: int | None) -> tuple[list[str], Any]:
    dataset_config = load_dataset_config()
    split, _stage, plan_filename = STAGE_PLAN[stage_key]
    plan_path = paths.samples_dir(dataset_config) / plan_filename
    plan = load_sampling_plan(plan_path)
    if plan.split != split:
        raise StorageError(
            f"sample plan {plan_path.name} targets {plan.split!r}, not {split!r}"
        )
    task_ids = list(plan.task_ids)
    if limit is not None:
        task_ids = task_ids[:limit]
    return task_ids, plan


def _load_public_tasks(store: TaskStore, task_ids: list[str]) -> dict[str, Any]:
    return {task_id: store.load_public_task(task_id) for task_id in task_ids}


def _print_stage_summary(summary: dict[str, Any]) -> None:
    print(f"tasks finalized: {summary['tasks_finalized']}/{summary['tasks_expected']}")
    print(f"scored: {summary['scored']}  unscored: {summary['unscored']}")
    print(f"pass comparison: {summary['pass_comparison_distribution']}")
    print(f"adjudication: {summary['adjudication_outcome_distribution']}")
    print(f"answerability: {summary['answerability_distribution']}")
    print(f"categories: {summary['primary_category_distribution']}")
    print(f"confidence: {summary['confidence_distribution']}")
    print(f"external policy dependency: {summary['external_policy_dependency_tasks']}")
    print(f"total tokens: {summary['total_tokens']}")
    if summary["tasks_missing"]:
        print(f"missing: {len(summary['tasks_missing'])} task(s)")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def _run_inspect(args: argparse.Namespace) -> int:
    config = _judge_config(args)
    storage, store = _storage()
    payload: dict[str, Any] = {
        "judge": {
            "label": config.label,
            "model": config.model,
            "base_url_host": config.base_url,
            "temperature": config.temperature,
            "max_output_tokens": config.max_output_tokens,
            "annotation_prompt_version": config.annotation.prompt_version,
            "evaluation_prompt_version": config.evaluation.prompt_version,
            "annotation_config_hash": config.config_hash(stage="annotation"),
            "evaluation_config_hash": config.config_hash(stage="evaluation"),
        },
        "samples": {},
        "stages": {},
        "frozen": (storage.manifests_dir() / FROZEN_MANIFEST_FILENAME).is_file(),
        "test_locked": True,
    }
    dataset_config = load_dataset_config()
    for key, (split, stage, plan_filename) in STAGE_PLAN.items():
        plan = load_sampling_plan(paths.samples_dir(dataset_config) / plan_filename)
        payload["samples"][key] = {
            "split": split,
            "plan_id": plan.plan_id,
            "tasks": plan.selected_size,
            "selection_sha256": plan.selection_sha256,
        }
        payload["stages"][key] = summarize_stage(
            storage, split, stage, list(plan.task_ids)
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"judge model: {payload['judge']['model']} "
              f"({payload['judge']['label']})")
        print(f"annotation prompt version {config.annotation.prompt_version} "
              f"config hash: {payload['judge']['annotation_config_hash'][:16]}")
        print(f"evaluation prompt version {config.evaluation.prompt_version} "
              f"config hash: {payload['judge']['evaluation_config_hash'][:16]}")
        for key, sample in payload["samples"].items():
            print(f"sample {key}: {sample['plan_id']} tasks={sample['tasks']} "
                  f"finalized={payload['stages'][key]['tasks_finalized']}")
        print(f"frozen manifest present: {payload['frozen']}")
        print("test access: locked")
    return EXIT_OK


# ---------------------------------------------------------------------------
# annotate
# ---------------------------------------------------------------------------


def _run_annotate(args: argparse.Namespace) -> int:
    config = _judge_config(args)
    storage, store = _storage()
    split, stage, _plan = STAGE_PLAN[args.annotation_command]
    task_ids, plan = _plan_tasks(args.annotation_command, args.limit)
    tasks = _load_public_tasks(store, task_ids)
    context = AnnotationContext(
        config=config,
        storage=storage,
        client_factory=_client_factory(config),
        model=config.model,
        provider_label=config.label,
        annotation_config_hash=config.config_hash(stage="annotation"),
        prompt_version=config.annotation.prompt_version,
        dry_run=args.dry_run,
    )
    if args.recompare:
        finals = recompare_stage(
            context, split, stage, [tasks[task_id] for task_id in task_ids]
        )
    else:
        finals = run_annotation_stage(
            context,
            split,
            stage,
            [tasks[task_id] for task_id in task_ids],
            concurrency=args.concurrency or config.annotation.concurrency,
        )
    summary = summarize_stage(storage, split, stage, task_ids)
    if not args.dry_run:
        _record_run_manifest(storage, split, stage, plan, config, summary)
    if args.json:
        print(
            json.dumps(
                {
                    "split": split,
                    "stage": stage,
                    "finals": len(finals),
                    "summary": summary,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"split={split} stage={stage} dry_run={args.dry_run}")
        _print_stage_summary(summary)
        print(f"records: {storage.stage_dir(split, stage)}")
    return EXIT_OK


def _record_run_manifest(
    storage: GoldStorage,
    split: str,
    stage: str,
    plan: Any,
    config: JudgeConfig,
    summary: dict[str, Any],
) -> None:
    # Per-stage manifest: concurrent annotation pipelines must never race on one
    # shared file. The aggregate manifest is rebuilt at freeze time.
    manifest_path = storage.stage_dir(split, stage) / "run_manifest.json"
    entry = {
        "run_id": (
            f"annotation-{utc_now().replace(':', '').replace('-', '').replace('+', '')}"
        ),
        "split": split,
        "stage": stage,
        "sample_plan": plan.plan_id,
        "sample_selection_sha256": plan.selection_sha256,
        "model": config.model,
        "provider": config.label,
        "annotation_prompt_version": config.annotation.prompt_version,
        "annotation_config_hash": config.config_hash(stage="annotation"),
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "requested_at": utc_now(),
        "summary": summary,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# validate / freeze
# ---------------------------------------------------------------------------


def _load_finals(storage: GoldStorage, split: str, stage: str) -> dict[str, FinalGoldState]:
    finals: dict[str, FinalGoldState] = {}
    path = storage.finals_path(split, stage)
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    final = FinalGoldState.model_validate_json(line)
                except Exception:
                    continue
                finals[final.task_id] = final
    return finals


def _run_validate(args: argparse.Namespace) -> int:
    storage, store = _storage()
    payload: dict[str, Any] = {"stages": {}, "problems": []}
    ok = True
    for key, (split, stage, plan_filename) in STAGE_PLAN.items():
        task_ids, _plan = _plan_tasks(key, None)
        finals = _load_finals(storage, split, stage)
        tasks = _load_public_tasks(store, task_ids)
        problems: list[str] = []
        for task_id in sorted(set(task_ids)):
            final = finals.get(task_id)
            if final is None:
                problems.append(f"{task_id}: no final state")
                ok = False
                continue
            issues = validate_final(final, tasks[task_id])
            if issues:
                problems.append(f"{task_id}: {'; '.join(issues)}")
                ok = False
        payload["stages"][key] = {
            "tasks": len(task_ids),
            "finalized": len(set(task_ids) & set(finals)),
            "problems": len(problems),
        }
        payload["problems"].extend(problems)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for key, stage_info in payload["stages"].items():
            print(f"{key}: {stage_info['finalized']}/{stage_info['tasks']} finalized, "
                  f"{stage_info['problems']} problem(s)")
        for problem in payload["problems"][:20]:
            print(f"  problem: {problem}")
    return EXIT_OK if ok else EXIT_FAILED


def _run_freeze(args: argparse.Namespace) -> int:
    config = _judge_config(args)
    storage, store = _storage()
    dataset_config = load_dataset_config()
    dataset_version_value = _dataset_of_tasks(paths.tasks_dir(dataset_config))
    base_references = {
        reference.task_id: reference
        for reference in iter_references(
            paths.tasks_dir(dataset_config) / "references.jsonl"
        )
    }
    tasks = {
        task.task_id: task
        for task in iter_public_tasks(paths.tasks_dir(dataset_config) / "tasks.jsonl")
    }

    # Train-R = pilot + feedback, deduplicated by task id.
    train_r_finals: dict[str, FinalGoldState] = {}
    train_r_finals.update(_load_finals(storage, "train_r", PILOT_DIRNAME))
    train_r_finals.update(_load_finals(storage, "train_r", FEEDBACK_DIRNAME))
    dev_finals = _load_finals(storage, "dev", MAIN_DIRNAME)

    train_r_refs, train_r_problems = apply_finals(
        finals=train_r_finals.values(),
        references=base_references,
        tasks=tasks,
        model=config.model,
        prompt_version=config.annotation.prompt_version,
        config_hash=config.config_hash(stage="annotation"),
        provider_label=config.label,
    )
    dev_refs, dev_problems = apply_finals(
        finals=dev_finals.values(),
        references=base_references,
        tasks=tasks,
        model=config.model,
        prompt_version=config.annotation.prompt_version,
        config_hash=config.config_hash(stage="annotation"),
        provider_label=config.label,
    )

    train_r_info = write_references(
        storage.split_root("train_r"), list(train_r_refs.values())
    )
    dev_info = write_references(storage.split_root("dev"), list(dev_refs.values()))
    write_annotation_summary(
        storage.split_root("train_r"),
        {
            "split": "train_r",
            **train_r_info,
            "problems": train_r_problems,
        },
    )
    write_annotation_summary(
        storage.split_root("dev"),
        {"split": "dev", **dev_info, "problems": dev_problems},
    )
    frozen_path = write_frozen_manifest(
        storage.manifests_dir(),
        train_r={
            "pilot_tasks": 100,
            "feedback_tasks": 300,
            **train_r_info,
        },
        dev=dev_info,
        prompt_version=config.annotation.prompt_version,
        dataset_version=dataset_version_value,
        model=config.model,
    )

    # Sample integrity and overlap report over the actual manifests.
    plans_payload = {}
    _overlap_keys = {"pilot": "pilot", "train-r": "feedback", "dev": "dev"}
    for key, (_split, _stage, plan_filename) in STAGE_PLAN.items():
        plan = load_sampling_plan(paths.samples_dir(dataset_config) / plan_filename)
        plans_payload[_overlap_keys[key]] = plan.model_dump(mode="json")
    overlap = build_overlap_report(
        store=store,
        plans=plans_payload,
        run_set_dir=paths.traces_root(dataset_config)
        / "20261125T164504Z-group-a-dev-eval",
    )
    overlap_path = storage.manifests_dir() / OVERLAP_REPORT_FILENAME
    overlap_path.parent.mkdir(parents=True, exist_ok=True)
    overlap_path.write_text(
        json.dumps(overlap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Aggregate the per-stage run manifests into the single run manifest.
    runs = []
    for split, stage in (("train_r", PILOT_DIRNAME), ("train_r", FEEDBACK_DIRNAME), ("dev", MAIN_DIRNAME)):
        stage_manifest = storage.stage_dir(split, stage) / "run_manifest.json"
        if stage_manifest.is_file():
            try:
                runs.append(json.loads(stage_manifest.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    run_manifest_path = storage.manifests_dir() / RUN_MANIFEST_FILENAME
    run_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    run_manifest_path.write_text(
        json.dumps(
            {"runs": runs, "last_run": runs[-1] if runs else None},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    if args.json:
        print(
            json.dumps(
                {
                    "frozen_manifest": str(frozen_path),
                    "train_r": train_r_info,
                    "dev": dev_info,
                    "train_r_problems": train_r_problems,
                    "dev_problems": dev_problems,
                    "overlap_report": str(overlap_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"train_r references: {train_r_info}")
        print(f"dev references: {dev_info}")
        print(f"train_r problems: {train_r_problems}")
        print(f"dev problems: {dev_problems}")
        print(f"frozen manifest: {frozen_path}")
        print(f"overlap report: {overlap_path}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# evaluate-group-a / report-group-a
# ---------------------------------------------------------------------------


def _run_evaluate_group_a(args: argparse.Namespace) -> int:
    from cm_outbound_bench.decisions.scoring.cli import (
        ScoringCLIError,
        run_group_a_evaluation,
    )

    try:
        return run_group_a_evaluation(args)
    except ScoringCLIError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED


def _run_report_group_a(args: argparse.Namespace) -> int:
    from cm_outbound_bench.decisions.scoring.cli import run_group_a_report

    return run_group_a_report(args)


__all__ = ["add_parsers", "dispatch_gold"]
