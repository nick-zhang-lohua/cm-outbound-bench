"""Report generation for the Milestone 1.2 Group A business evaluation.

Reports under ``outputs/decision/reports/`` are git-ignored and may carry derived
judge rationales (never raw conversation text beyond what a summary table needs).
The committed research-readiness report in ``docs/reports/`` is aggregates only.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from cm_outbound_bench.decisions.scoring.schema import FinalScore


def _table(rows: list[tuple[str, object]]) -> str:
    lines = ["| Field | Value |", "|---|---|"]
    for name, value in rows:
        rendered = str(value)
        if value is None:
            rendered = "not available"
        lines.append(f"| {name} | {rendered} |")
    return "\n".join(lines)


def render_baseline_report(
    *,
    metrics: Mapping[str, Any],
    statistics: Mapping[str, Any],
    ground_truth_version: str,
    model: str,
    prompt_version: str,
    agent_model: str,
    run_set: str,
) -> str:
    lines = [
        "# Group A LLM-Gold Business Baseline Report",
        "",
        _table(
            [
                ("Condition", "A (no flowchart, no workflow, no simulator)"),
                ("Ground Truth version", ground_truth_version),
                ("Ground Truth model", model),
                ("Ground Truth type", "llm_generated_gold (frozen)"),
                ("Evaluation judge model", model),
                ("Scoring prompt version", prompt_version),
                ("Evaluated agent run set", run_set),
                ("Evaluated agent model", agent_model),
                ("Evaluated tasks", metrics.get("executed_tasks")),
            ]
        ),
        "",
        "## Primary results",
        "",
        _table(
            [
                ("LLM-Gold Decision Success Rate (DSR)", metrics.get("llm_gold_decision_success_rate")),
                ("Decision Acceptance Rate (DAR)", metrics.get("decision_acceptance_rate")),
                ("Partial Decision Rate (PDR)", metrics.get("partial_decision_rate")),
                ("Incorrect Decision Rate (IDR)", metrics.get("incorrect_decision_rate")),
                ("Scored Task Coverage", metrics.get("scored_task_coverage")),
                ("Scored tasks", metrics.get("scored_tasks")),
                ("Unscorable tasks", metrics.get("unscorable_tasks")),
            ]
        ),
        "",
        "## Secondary metrics",
        "",
        _table(
            [
                ("Required Step Precision", metrics.get("required_step_precision")),
                ("Required Step Precision reason", metrics.get("required_step_precision_reason")),
                ("Critical Error Rate", metrics.get("critical_error_rate")),
                ("Response Quality", None),
                ("policy_verified_decision_success_rate", None),
            ]
        ),
        "",
        "## Lexical proxy metrics (diagnostic only, lower bound)",
        "",
        _table(
            [
                ("Appropriate Clarification Rate", metrics.get("appropriate_clarification_rate")),
                ("Required Step Recall", metrics.get("required_step_recall")),
                (
                    "Required Step Precision (lexical surrogate)",
                    metrics.get("required_step_precision_lexical_diagnostic"),
                ),
                ("Branch Decision Accuracy", metrics.get("branch_decision_accuracy")),
            ]
        ),
        "",
        f"- {metrics.get('secondary_metric_note', '')}",
        "",
        f"Outcome distribution: `{json.dumps(metrics.get('outcome_counts', {}), ensure_ascii=False)}`",
        "",
        "## Confidence intervals (95%, conversation-unit bootstrap)",
        "",
        _table(
            [
                (
                    "DSR 95% CI",
                    _ci(statistics.get("confidence_intervals", {}).get("dsr")),
                ),
                (
                    "DAR 95% CI",
                    _ci(statistics.get("confidence_intervals", {}).get("dar")),
                ),
                (
                    "IDR 95% CI",
                    _ci(statistics.get("confidence_intervals", {}).get("idr")),
                ),
            ]
        ),
        "",
        f"- Executed tasks: {statistics.get('executed_tasks')}; scored: "
        f"{statistics.get('scored_tasks')}; excluded: {statistics.get('excluded_tasks')}",
        f"- Bootstrap: {statistics.get('bootstrap_procedure')}",
        f"- Replications: {statistics.get('bootstrap_replications')} "
        f"(seed {statistics.get('bootstrap_seed')})",
        f"- Independent sampling unit: {statistics.get('independent_sampling_unit')}",
        "",
        "## Interpretation limits",
        "",
        "- The Ground Truth is the frozen DeepSeek V4 Pro-adjudicated annotation, "
        "not an independently verified China Mobile policy set: "
        "`policy_verified = false` for every task.",
        "- " + str(statistics.get("limitation")),
        "",
    ]
    return "\n".join(lines)


def render_quality_report(
    *,
    gold_summaries: Mapping[str, Any],
    scorer_stats: Mapping[str, Any],
    model: str,
    prompt_version: str,
) -> str:
    lines = [
        "# LLM-Gold Annotation Quality Report",
        "",
        _table(
            [
                ("Ground Truth model", model),
                ("Annotation prompt version", prompt_version),
                ("Adjudication prompt version", prompt_version),
            ]
        ),
        "",
        "## Annotation consistency (intra-model, two V4 Pro passes)",
        "",
    ]
    for key, summary in gold_summaries.items():
        comparison = summary.get("pass_comparison_distribution", {})
        adjudication = summary.get("adjudication_outcome_distribution", {})
        lines.extend(
            [
                f"### {key}",
                "",
                _table(
                    [
                        ("Tasks finalized", summary.get("tasks_finalized")),
                        ("Scored / unscored", f"{summary.get('scored')} / {summary.get('unscored')}"),
                        ("Pass comparison distribution", json.dumps(comparison, ensure_ascii=False)),
                        ("Adjudication rate", summary.get("adjudication_rate")),
                        ("Adjudication outcomes", json.dumps(adjudication, ensure_ascii=False)),
                        ("Answerability distribution", json.dumps(summary.get("answerability_distribution", {}), ensure_ascii=False)),
                        ("Primary category distribution", json.dumps(summary.get("primary_category_distribution", {}), ensure_ascii=False)),
                        ("Confidence distribution", json.dumps(summary.get("confidence_distribution", {}), ensure_ascii=False)),
                        ("External policy dependency tasks", summary.get("external_policy_dependency_tasks")),
                        ("policy_verified tasks", summary.get("policy_verified_tasks")),
                        ("Total tokens", summary.get("total_tokens")),
                    ]
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Output evaluation consistency (two V4 Pro scoring passes)",
            "",
            _table(
                [
                    ("Scorer A/B agreement", scorer_stats.get("scorer_agreement_rate")),
                    ("Scoring adjudication rate", scorer_stats.get("scoring_adjudication_rate")),
                    ("Final outcome distribution", json.dumps(scorer_stats.get("final_outcome_distribution", {}), ensure_ascii=False)),
                    ("Critical error distribution", json.dumps(scorer_stats.get("critical_error_distribution", {}), ensure_ascii=False)),
                ]
            ),
            "",
            "These are two evaluations by the same model. They are reported as "
            "LLM Pass Agreement / LLM Scorer Agreement (intra-model consistency), "
            "never as independent human annotation reliability.",
            "",
        ]
    )
    return "\n".join(lines)


def render_error_analysis_report(
    *,
    finals: Mapping[str, FinalScore],
    limit: int = 100,
) -> str:
    lines = [
        "# Group A Error Analysis Report",
        "",
        "Critical errors are flagged independently by the evaluation judge. No "
        "critical error is reported when the evidence is insufficient.",
        "",
    ]
    kind_counts: Counter = Counter()
    examples: list[tuple[str, str, str, str]] = []
    for task_id, final in sorted(finals.items()):
        for error in final.critical_errors:
            kind_counts[error.kind] += 1
        if final.outcome in {"incorrect", "partially_correct"}:
            rationale = (final.rationale or "").replace("\n", " ")[:200]
            examples.append(
                (
                    task_id,
                    final.outcome,
                    ",".join(sorted(item.kind for item in final.critical_errors)),
                    rationale,
                )
            )
    lines.append(
        _table([(kind, count) for kind, count in sorted(kind_counts.items())])
    )
    lines.extend(["", "## Incorrect / partially correct tasks (first 100)", ""])
    lines.append("| Task | Outcome | Critical errors | Judge rationale (excerpt) |")
    lines.append("|---|---|---|---|")
    for task_id, outcome, errors, rationale in examples[:limit]:
        lines.append(f"| `{task_id}` | {outcome} | {errors or '—'} | {rationale} |")
    lines.append("")
    return "\n".join(lines)


def _ci(entry: Mapping[str, Any] | None) -> str:
    if not entry:
        return "not available"
    return f"[{entry.get('ci_lower')}, {entry.get('ci_upper')}]"


def write_report(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


__all__ = [
    "render_baseline_report",
    "render_error_analysis_report",
    "render_quality_report",
    "write_report",
]
