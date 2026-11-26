"""CLI flow for the blind Group A output evaluation and its reports.

Artifacts: ``outputs/decision/evaluation/group_a/`` (scorer records, adjudicated
results, evaluation manifest, metrics, error analysis) and the three reports
under ``outputs/decision/reports/``. The frozen Ground Truth is only ever read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from cm_outbound_bench.decisions import paths
from cm_outbound_bench.decisions.config import load_dataset_config
from cm_outbound_bench.decisions.gold.audit import summarize_stage
from cm_outbound_bench.decisions.gold.config import (
    load_judge_config,
    resolve_api_key,
    resolve_header_values,
)
from cm_outbound_bench.decisions.gold.provider import JudgeClient
from cm_outbound_bench.decisions.gold.storage import (
    FEEDBACK_DIRNAME,
    FINAL_REFERENCES_FILENAME,
    FROZEN_MANIFEST_FILENAME,
    PILOT_DIRNAME,
    GoldStorage,
    utc_now,
)
from cm_outbound_bench.decisions.schema import (
    AgentDecisionOutput,
    PrivateTaskReference,
)
from cm_outbound_bench.decisions.scoring.metrics import compute_metrics
from cm_outbound_bench.decisions.scoring.reports import (
    render_baseline_report,
    render_error_analysis_report,
    render_quality_report,
    write_report,
)
from cm_outbound_bench.decisions.scoring.schema import (
    OUTCOME_UNSCORABLE,
    FinalScore,
    ScoreAdjudication,
    ScoreRecord,
)
from cm_outbound_bench.decisions.scoring.scorer import (
    ScoringContext,
    aggregate_saved_scores,
    score_dataset,
)
from cm_outbound_bench.decisions.scoring.statistics import (
    bootstrap_rate_ci,
    describe,
)
from cm_outbound_bench.decisions.store import TaskStore, iter_references
from cm_outbound_bench.decisions.trace import iter_traces
from cm_outbound_bench.decisions.verification import verify_trace_inputs

EXIT_OK = 0
EXIT_FAILED = 1

DEFAULT_GROUP_A_RUN_SET = "20261125T164504Z-group-a-dev-eval"
EVALUATION_DIRNAME = "evaluation"
GROUP_A_DIRNAME = "group_a"
SCORER_A_FILENAME = "scorer_a.jsonl"
SCORER_B_FILENAME = "scorer_b.jsonl"
ADJUDICATED_FILENAME = "adjudicated_results.jsonl"
EVALUATION_MANIFEST_FILENAME = "evaluation_manifest.json"
METRICS_FILENAME = "metrics.json"
ERROR_ANALYSIS_FILENAME = "error_analysis.json"


class ScoringCLIError(RuntimeError):
    """Raised when the evaluation cannot run."""


def evaluation_dir() -> Path:
    from cm_outbound_bench.decisions.paths import outputs_root

    return outputs_root(load_dataset_config()) / EVALUATION_DIRNAME / GROUP_A_DIRNAME


def run_group_a_evaluation(args: argparse.Namespace) -> int:
    dataset_config = load_dataset_config()
    config = load_judge_config(getattr(args, "config", None))
    if getattr(args, "model", None):
        config = config.model_copy(update={"model": args.model})
    if getattr(args, "prompt_version", None):
        evaluation = config.evaluation.model_copy(
            update={"prompt_version": args.prompt_version}
        )
        config = config.model_copy(update={"evaluation": evaluation})
    config.model_checked()

    storage = GoldStorage.default(
        dataset_version=_dataset_of_tasks(paths.tasks_dir(dataset_config)),
        tasks_artifact_sha256=_tasks_hash(paths.tasks_dir(dataset_config)),
    )
    frozen_path = storage.manifests_dir() / FROZEN_MANIFEST_FILENAME
    if not frozen_path.is_file():
        raise ScoringCLIError(
            "no frozen Ground Truth manifest: run `decision annotation freeze` "
            "before scoring; scoring never proceeds against unfrozen references"
        )
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    # Record which immutable manifest this evaluation was checked against.
    frozen["__path__"] = str(frozen_path)

    store = TaskStore.load(paths.tasks_dir(dataset_config))
    dev_refs_path = storage.split_root("dev") / FINAL_REFERENCES_FILENAME
    if not dev_refs_path.is_file():
        raise ScoringCLIError("frozen Dev references are missing")
    dev_references: dict[str, PrivateTaskReference] = {
        reference.task_id: reference for reference in iter_references(dev_refs_path)
    }

    run_set_dir = args.run_set or (
        paths.traces_root(dataset_config) / DEFAULT_GROUP_A_RUN_SET
    )
    traces = iter_traces(run_set_dir)
    if len(traces) != 400:
        raise ScoringCLIError(
            f"expected the 400-task Group A run set, found {len(traces)} traces in "
            f"{run_set_dir}"
        )
    trace_ids = {trace.task_id for trace in traces}
    if trace_ids != set(dev_references):
        raise ScoringCLIError(
            "the Dev gold references do not match the Group A traces exactly; "
            "scoring is refused"
        )
    verification = verify_trace_inputs(traces, store)
    if not verification.ok:
        raise ScoringCLIError(
            "trace input verification failed; scoring is refused: "
            f"{verification.model_dump()}"
        )

    agent_outputs: dict[str, AgentDecisionOutput | None] = {}
    for trace in traces:
        if trace.agent_output is None:
            agent_outputs[trace.task_id] = None
            continue
        agent_outputs[trace.task_id] = AgentDecisionOutput.model_validate(
            trace.agent_output
        )

    tasks = {
        task_id: store.load_public_task(task_id) for task_id in dev_references
    }

    context = ScoringContext(
        prompt_version=config.evaluation.prompt_version,
        model=config.model,
        provider_label=config.label,
        config_hash=config.config_hash(stage="evaluation"),
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        ground_truth_version=str(frozen.get("ground_truth_version", "")),
        client_factory=_client_factory(config),
        dry_run=args.dry_run,
    )

    cache = _load_score_cache()
    task_ids = sorted(tasks)
    if args.limit is not None:
        task_ids = task_ids[: args.limit]

    if getattr(args, "reaggregate", False):
        # Re-derive finals from the saved passes and saved adjudications. No
        # pass or adjudication API call is made; used to re-apply the aggregation
        # rules (such as the is_scored gold gate) to an existing evaluation.
        finals, problems = aggregate_saved_scores(
            gold={task_id: dev_references[task_id] for task_id in task_ids},
            records_a=cache["A"],
            records_b=cache["B"],
            adjudications=_load_saved_adjudications(),
        )
        records_a = {
            task_id: cache["A"][task_id]
            for task_id in task_ids
            if task_id in cache["A"]
        }
        records_b = {
            task_id: cache["B"][task_id]
            for task_id in task_ids
            if task_id in cache["B"]
        }
    else:
        finals, records_a, records_b, problems = score_dataset(
            context,
            tasks={task_id: tasks[task_id] for task_id in task_ids},
            gold={task_id: dev_references[task_id] for task_id in task_ids},
            agent_outputs={
                task_id: (
                    agent_outputs[task_id].model_dump(mode="json")
                    if agent_outputs[task_id] is not None
                    else None
                )
                for task_id in task_ids
            },
            cache=cache,
            concurrency=config.evaluation.concurrency,
            on_record=_save_one_score_pair,
        )

    # The finals artifact is derived from the finals, not from API calls, so it
    # is rewritten on both paths; re-aggregation must not leave stale results.
    _save_score_records(records_a, records_b, finals)

    outputs = {
        task_id: agent_outputs[task_id]
        for task_id in task_ids
        if agent_outputs.get(task_id) is not None
    }
    metrics = compute_metrics(
        finals=finals,
        references={task_id: dev_references[task_id] for task_id in task_ids},
        outputs=outputs,
        executed_tasks=len(traces),
    )

    bootstrap = _bootstrap_metrics(finals, executed=len(traces), seed=args.bootstrap_seed)
    statistics = describe(
        executed_tasks=len(traces),
        scored_tasks=metrics["scored_tasks"],
        independent_sampling_unit="conversation_unit",
        bootstrap=bootstrap,
    )

    scorer_stats = _scorer_stats(finals, records_a, records_b)
    gold_summaries = _gold_summaries(storage)

    _write_evaluation_artifacts(
        finals=finals,
        records_a=records_a,
        records_b=records_b,
        metrics=metrics,
        statistics=statistics,
        scorer_stats=scorer_stats,
        problems=problems,
        config=config,
        frozen=frozen,
        run_set=run_set_dir.name,
    )

    reports_root = paths.reports_dir(dataset_config)
    write_report(
        reports_root / "group_a_llm_gold_baseline_report.md",
        render_baseline_report(
            metrics=metrics,
            statistics=statistics,
            ground_truth_version=context.ground_truth_version,
            model=config.model,
            prompt_version=config.evaluation.prompt_version,
            agent_model=traces[0].model_name,
            run_set=run_set_dir.name,
        ),
    )
    write_report(
        reports_root / "llm_gold_quality_report.md",
        render_quality_report(
            gold_summaries=gold_summaries,
            scorer_stats=scorer_stats,
            model=config.model,
            prompt_version=config.annotation.prompt_version,
        ),
    )
    write_report(
        reports_root / "group_a_error_analysis_report.md",
        render_error_analysis_report(finals=finals),
    )
    (reports_root / "group_a_llm_gold_baseline_report.json").write_text(
        json.dumps(
            {
                "metrics": metrics,
                "statistics": statistics,
                "scorer_stats": scorer_stats,
            },
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
                    "metrics": metrics,
                    "statistics": statistics,
                    "scorer_stats": scorer_stats,
                    "problems": problems,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"scored tasks: {metrics['scored_tasks']}/{metrics['executed_tasks']}")
        print(f"DSR: {metrics['llm_gold_decision_success_rate']}")
        print(f"DAR: {metrics['decision_acceptance_rate']}")
        print(f"PDR: {metrics['partial_decision_rate']}")
        print(f"IDR: {metrics['incorrect_decision_rate']}")
        print(f"coverage: {metrics['scored_task_coverage']}")
        print(f"critical error rate: {metrics['critical_error_rate']}")
        print(f"DSR 95% CI: {bootstrap.get('dsr')}")
        print(f"problems: {problems}")
    return EXIT_OK


def run_group_a_report(args: argparse.Namespace) -> int:
    directory = evaluation_dir()
    metrics_path = directory / METRICS_FILENAME
    if not metrics_path.is_file():
        print("error: no saved evaluation; run `decision evaluate-group-a` first",
              file=sys.stderr)
        return EXIT_FAILED
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_OK


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _dataset_of_tasks(tasks_dir: Path) -> str:
    manifest = tasks_dir / "tasks.manifest.json"
    if manifest.is_file():
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return str(payload.get("dataset_version", "cm_outbound_v2"))
    return "cm_outbound_v2"


def _tasks_hash(tasks_dir: Path) -> str:
    from cm_outbound_bench.decisions.gold.audit import tasks_artifact_hash

    return tasks_artifact_hash(tasks_dir)


def _client_factory(config):
    def factory() -> JudgeClient:
        api_key = resolve_api_key(config)
        headers = resolve_header_values(config)
        return JudgeClient(config).with_credentials(api_key, headers)

    return factory


def _load_score_cache() -> dict[str, dict[str, ScoreRecord]]:
    root = evaluation_dir() / "records"
    cache: dict[str, dict[str, ScoreRecord]] = {"A": {}, "B": {}}
    for scorer in ("a", "b"):
        key = "A" if scorer == "a" else "B"
        for path in sorted((root / f"pass_{scorer}").glob("*.json")):
            if not path.is_file():
                continue
            try:
                record = ScoreRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except Exception:
                continue
            cache[key][record.task_id] = record
    return cache


def _load_saved_adjudications() -> dict[str, ScoreAdjudication]:
    """Read already-paid score adjudications from the saved results artifact.

    Re-aggregation reuses these so applying a refined rule never silently buys a
    new judge verdict; a disagreement without a saved adjudication stays
    ``adjudication_unavailable`` rather than being re-asked.
    """
    path = evaluation_dir() / ADJUDICATED_FILENAME
    if not path.is_file():
        return {}
    result: dict[str, ScoreAdjudication] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            final = FinalScore.model_validate_json(line)
        except Exception:
            continue
        if final.adjudication is not None:
            result[final.task_id] = final.adjudication
    return result


def _save_one_score_pair(
    task_id: str, record_a: ScoreRecord, record_b: ScoreRecord
) -> None:
    """Persist one task's two pass records as soon as they complete."""
    directory = evaluation_dir() / "records"
    for scorer, record in (("a", record_a), ("b", record_b)):
        if record.parsing_status == "failed":
            continue
        target_dir = directory / f"pass_{scorer}"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{task_id}.json").write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _save_score_records(
    records_a: dict[str, ScoreRecord],
    records_b: dict[str, ScoreRecord],
    finals: dict[str, FinalScore],
) -> None:
    directory = evaluation_dir()
    for scorer, records in (("a", records_a), ("b", records_b)):
        target_dir = directory / "records" / f"pass_{scorer}"
        target_dir.mkdir(parents=True, exist_ok=True)
        for record in records.values():
            if record.parsing_status == "failed":
                continue
            (target_dir / f"{record.task_id}.json").write_text(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    adjudicated = directory / ADJUDICATED_FILENAME
    adjudicated.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(final.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        for final in sorted(finals.values(), key=lambda item: item.task_id)
    ]
    adjudicated.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bootstrap_metrics(
    finals: dict[str, FinalScore],
    *,
    executed: int,
    seed: int | None,
) -> dict[str, dict[str, float]]:
    from cm_outbound_bench.decisions.scoring.statistics import (
        DEFAULT_BOOTSTRAP_REPLICATIONS,
        DEFAULT_BOOTSTRAP_SEED,
    )

    unit_ids = sorted(finals)
    scored = [
        1 if finals[task_id].outcome != OUTCOME_UNSCORABLE else 0
        for task_id in unit_ids
    ]
    numerators = {
        "dsr": [
            1
            if finals[task_id].outcome == "fully_correct"
            and finals[task_id].outcome != OUTCOME_UNSCORABLE
            else 0
            for task_id in unit_ids
        ],
        "dar": [
            1
            if finals[task_id].outcome
            in {"fully_correct", "partially_correct"}
            and finals[task_id].outcome != OUTCOME_UNSCORABLE
            else 0
            for task_id in unit_ids
        ],
        "idr": [
            1
            if finals[task_id].outcome == "incorrect"
            and finals[task_id].outcome != OUTCOME_UNSCORABLE
            else 0
            for task_id in unit_ids
        ],
        "pdr": [
            1
            if finals[task_id].outcome == "partially_correct"
            and finals[task_id].outcome != OUTCOME_UNSCORABLE
            else 0
            for task_id in unit_ids
        ],
    }
    return bootstrap_rate_ci(
        numerators=numerators,
        denominators=scored,
        metric_names=["dsr", "dar", "idr", "pdr"],
        unit_ids=unit_ids,
        replications=DEFAULT_BOOTSTRAP_REPLICATIONS,
        seed=seed if seed is not None else DEFAULT_BOOTSTRAP_SEED,
    )


def _scorer_stats(
    finals: dict[str, FinalScore],
    records_a: dict[str, ScoreRecord],
    records_b: dict[str, ScoreRecord],
) -> dict[str, Any]:
    agreements = 0
    adjudications = 0
    compared = 0
    critical: Counter = Counter()
    outcomes: Counter = Counter()
    for task_id, final in finals.items():
        outcomes[final.outcome] += 1
        for error in final.critical_errors:
            critical[error.kind] += 1
        if final.comparison is not None:
            compared += 1
            if final.comparison.classification == "agreement":
                agreements += 1
        if final.adjudication is not None:
            adjudications += 1
    scorer_disagreements = compared - agreements
    return {
        "tasks_with_judge_outcome": sum(
            1
            for final in finals.values()
            if final.source != "gold_reference_unscored"
        ),
        "tasks_with_unscored_gold_reference": sum(
            1 for final in finals.values() if final.source == "gold_reference_unscored"
        ),
        "scorer_agreement_rate": (
            round(agreements / compared, 4) if compared else None
        ),
        "scorer_disagreements": scorer_disagreements,
        "scoring_adjudication_rate": (
            round(adjudications / compared, 4) if compared else None
        ),
        "final_outcome_distribution": dict(sorted(outcomes.items())),
        "critical_error_distribution": dict(sorted(critical.items())),
    }


def _gold_summaries(storage: GoldStorage) -> dict[str, Any]:
    from cm_outbound_bench.decisions.sampling import load_sampling_plan

    dataset_config = load_dataset_config()
    result = {}
    for key, (split, stage, plan_filename) in {
        "train_r_pilot": ("train_r", PILOT_DIRNAME, "train_r_annotation_pilot.json"),
        "train_r_feedback": (
            "train_r",
            FEEDBACK_DIRNAME,
            "train_r_feedback_sample.json",
        ),
        "dev": ("dev", "main", "dev_baseline_evaluation_sample.json"),
    }.items():
        plan = load_sampling_plan(paths.samples_dir(dataset_config) / plan_filename)
        result[key] = summarize_stage(storage, split, stage, list(plan.task_ids))
    return result


def _write_evaluation_artifacts(
    *,
    finals: dict[str, FinalScore],
    records_a: dict[str, ScoreRecord],
    records_b: dict[str, ScoreRecord],
    metrics: dict[str, Any],
    statistics: dict[str, Any],
    scorer_stats: dict[str, Any],
    problems: list[str],
    config,
    frozen: dict[str, Any],
    run_set: str,
) -> None:
    directory = evaluation_dir()
    directory.mkdir(parents=True, exist_ok=True)
    scorer_a = directory / SCORER_A_FILENAME
    scorer_a.write_text(
        "\n".join(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            for record in sorted(records_a.values(), key=lambda item: item.task_id)
        )
        + "\n",
        encoding="utf-8",
    )
    scorer_b = directory / SCORER_B_FILENAME
    scorer_b.write_text(
        "\n".join(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            for record in sorted(records_b.values(), key=lambda item: item.task_id)
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "evaluation_version": "1.0",
        "generated_at": utc_now(),
        "ground_truth_version": frozen.get("ground_truth_version"),
        "ground_truth_source": frozen.get("ground_truth_source"),
        "judge_model": config.model,
        "judge_provider": config.label,
        "evaluation_prompt_version": config.evaluation.prompt_version,
        "evaluation_config_hash": config.config_hash(stage="evaluation"),
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "run_set": run_set,
        "tasks": len(finals),
        "problems": problems,
        "frozen_manifest_path": str(frozen.get("__path__", "")),
    }
    (directory / EVALUATION_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / METRICS_FILENAME).write_text(
        json.dumps(
            {"metrics": metrics, "statistics": statistics, "scorer_stats": scorer_stats},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    error_analysis = {
        "critical_error_distribution": scorer_stats["critical_error_distribution"],
        "incorrect_tasks": sorted(
            task_id
            for task_id, final in finals.items()
            if final.outcome in {"incorrect", "partially_correct"}
        ),
        "adjudicated_tasks": sorted(
            task_id for task_id, final in finals.items() if final.adjudication
        ),
    }
    (directory / ERROR_ANALYSIS_FILENAME).write_text(
        json.dumps(error_analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = ["run_group_a_evaluation", "run_group_a_report"]
