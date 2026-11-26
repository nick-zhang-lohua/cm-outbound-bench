"""Group A business baseline report assembly.

The report separates three things that are easy to conflate:

* **technical performance** — did the call to the model work;
* **business performance** — how often the decision was appropriate, reported per
  evaluation tier and never averaged across tiers;
* **error analysis** — which specific behaviour failed, classified structurally so
  the classification is reproducible rather than impressionistic.

Statistics use a **cluster bootstrap over conversation units**. Two tasks cut from
the same phone conversation are not independent, so resampling individual tasks
would understate the confidence interval. Every plan in this milestone selects one
task per conversation, but the code still clusters by conversation so the property
holds even for a future multi-task sample.

Nothing here reads conversation text. It consumes traces, references and optional
human output reviews.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter, defaultdict
from typing import Mapping, Sequence

from pydantic import Field

from cm_outbound_bench.decisions.evaluator import (
    AGGREGATE_METRIC_NAMES,
    METRIC_BRANCH,
    METRIC_CLARIFICATION,
    METRIC_DECISION_SUCCESS,
    METRIC_EXPERT_SUCCESS,
    METRIC_POLICY_COMPLIANCE,
    METRIC_POLICY_SUCCESS,
    METRIC_RESPONSE_QUALITY,
    METRIC_STEP_PRECISION,
    METRIC_STEP_RECALL,
    STRUCTURALLY_UNAVAILABLE_METRICS,
    AggregateMetrics,
    TaskEvaluation,
)
from cm_outbound_bench.decisions.schema import PrivateTaskReference
from cm_outbound_bench.domain.data_model import DomainModel

BUSINESS_REPORT_VERSION = "1.0"

#: Default number of bootstrap resamples.
BOOTSTRAP_RESAMPLES = 2000

#: Fixed bootstrap seed so a reported interval is reproducible.
BOOTSTRAP_SEED = 20261126

#: A response-quality review below this value is treated as a poor response.
RESPONSE_QUALITY_FLOOR = 0.5

#: Failure classes. Fixed names so a later run can be compared.
FAILURE_WRONG_DECISION = "wrong_business_decision"
FAILURE_WRONG_BRANCH = "wrong_branch"
FAILURE_MISSING_REQUIRED_STEP = "missing_required_step"
FAILURE_UNNECESSARY_STEP = "unnecessary_step"
FAILURE_MISSED_CLARIFICATION = "failure_to_request_necessary_information"
FAILURE_UNSUPPORTED_ASSUMPTION = "unsupported_assumption"
FAILURE_POLICY_VIOLATION = "policy_violation"
FAILURE_POOR_RESPONSE = "poor_customer_response"
FAILURE_FORMATTING = "output_formatting_failure"
FAILURE_UNSCORABLE = "unscorable_or_ambiguous_decision"

FAILURE_CLASSES: tuple[str, ...] = (
    FAILURE_WRONG_DECISION,
    FAILURE_WRONG_BRANCH,
    FAILURE_MISSING_REQUIRED_STEP,
    FAILURE_UNNECESSARY_STEP,
    FAILURE_MISSED_CLARIFICATION,
    FAILURE_UNSUPPORTED_ASSUMPTION,
    FAILURE_POLICY_VIOLATION,
    FAILURE_POOR_RESPONSE,
    FAILURE_FORMATTING,
    FAILURE_UNSCORABLE,
)


class ConfidenceInterval(DomainModel):
    """A point estimate with a percentile bootstrap interval."""

    metric: str
    point_estimate: float | None = None
    lower: float | None = None
    upper: float | None = None
    level: float = 0.95
    units: int = 0
    clusters: int = 0
    resamples: int = 0
    seed: int = 0
    method: str = "percentile_cluster_bootstrap"
    note: str = ""


class FailureAnalysis(DomainModel):
    """Counts of each failure class among the evaluated tasks."""

    failed_tasks: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    by_visible_context_bucket: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_task_complexity_bucket: dict[str, dict[str, int]] = Field(
        default_factory=dict
    )
    notes: list[str] = Field(default_factory=list)


class StratifiedResult(DomainModel):
    """Business success rate within one observable stratum."""

    dimension: str
    bucket: str
    tasks: int = 0
    scored: int = 0
    successes: int = 0
    success_rate: float | None = None


class BaselineReportData(DomainModel):
    """Everything the markdown report needs. Aggregates only."""

    report_version: str = BUSINESS_REPORT_VERSION
    run_set: str = ""
    condition: str = "A"
    benchmark_version: str = ""
    dataset_version: str = ""
    split: str = ""
    model_provider: str = ""
    model_name: str = ""
    model_parameters: dict[str, object] = Field(default_factory=dict)
    prompt_version: str = ""
    evaluator_version: str = ""
    agent_version: str = ""
    config_hash: str = ""
    aggregate: AggregateMetrics = Field(default_factory=AggregateMetrics)
    confidence_intervals: list[ConfidenceInterval] = Field(default_factory=list)
    failures: FailureAnalysis = Field(default_factory=FailureAnalysis)
    stratified: list[StratifiedResult] = Field(default_factory=list)
    behavioural_profile: dict[str, object] = Field(default_factory=dict)
    annotation_status: dict[str, object] = Field(default_factory=dict)
    independent_sampling_unit: str = "conversation"
    limitations: list[str] = Field(default_factory=list)
    pending_dependencies: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def behavioural_profile(
    outputs: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Describe what the agent actually produced. Never a correctness measure.

    This exists so the baseline run is informative while annotation is pending: how
    long the answers are, how often the agent asks for information, whether it
    expresses uncertainty, and how repetitive its stated actions are. None of these
    numbers says whether a decision was *right*.
    """
    if not outputs:
        return {}
    responses: list[int] = []
    actions: list[str] = []
    summaries: list[int] = []
    item_counts: Counter[int] = Counter()
    with_information = 0
    with_uncertainty = 0
    for output in outputs.values():
        response = str(output.get("customer_response") or "")
        action = str(output.get("next_action") or "")
        summary = str(output.get("evidence_summary") or "")
        information = output.get("required_information") or []
        responses.append(len(response))
        actions.append(action)
        summaries.append(len(summary))
        item_counts[len(information)] += 1
        if information:
            with_information += 1
        if output.get("uncertainty"):
            with_uncertainty += 1

    def _q(values: list[int], fraction: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        index = min(
            len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1))))
        )
        return ordered[index]

    repeats = Counter(actions).most_common(5)
    return {
        "tasks": len(actions),
        "customer_response_chars": {
            "min": min(responses) if responses else 0,
            "p50": _q(responses, 0.5),
            "max": max(responses) if responses else 0,
        },
        "next_action_chars": {
            "min": min(len(action) for action in actions) if actions else 0,
            "p50": _q([len(action) for action in actions], 0.5),
            "max": max((len(action) for action in actions), default=0),
        },
        "evidence_summary_chars_p50": _q(summaries, 0.5),
        "outputs_with_required_information": with_information,
        "required_information_fraction": (
            round(with_information / len(actions), 4) if actions else None
        ),
        "required_information_item_counts": dict(sorted(item_counts.items())),
        "outputs_with_uncertainty": with_uncertainty,
        "distinct_next_action_values": len(set(actions)),
        "most_common_next_action": [
            {"value": value, "count": count} for value, count in repeats
        ],
    }


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(
        len(sorted_values) - 1,
        max(0, int(round(fraction * (len(sorted_values) - 1)))),
    )
    return sorted_values[index]


def cluster_bootstrap_ci(
    values_by_cluster: Mapping[str, Sequence[float]],
    *,
    metric: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    level: float = 0.95,
) -> ConfidenceInterval:
    """Percentile bootstrap interval over conversation-unit clusters.

    ``values_by_cluster`` maps a conversation identifier to the metric values
    observed for tasks cut from that conversation. Resampling clusters (not tasks)
    keeps the dependence between tasks from the same call.
    """
    clusters = [
        (cluster, list(values))
        for cluster, values in values_by_cluster.items()
        if values
    ]
    observations = sum(len(values) for _, values in clusters)
    interval = ConfidenceInterval(
        metric=metric,
        level=level,
        units=observations,
        clusters=len(clusters),
        resamples=resamples,
        seed=seed,
    )
    if observations == 0:
        interval.note = "no scored observation for this metric"
        return interval

    flat = [value for _, values in clusters for value in values]
    interval.point_estimate = round(sum(flat) / len(flat), 4)
    if len(clusters) < 2:
        interval.note = (
            "fewer than two independent clusters, so no interval is reported; the "
            "point estimate comes from a single conversation and has no measurable "
            "sampling variability"
        )
        return interval

    generator = random.Random(seed)
    estimates: list[float] = []
    cluster_ids = list(range(len(clusters)))
    for _ in range(resamples):
        sample: list[float] = []
        for _ in cluster_ids:
            _, values = clusters[generator.randrange(len(clusters))]
            sample.extend(values)
        if sample:
            estimates.append(sum(sample) / len(sample))
    if not estimates:
        interval.note = "bootstrap produced no usable resample"
        return interval
    estimates.sort()
    alpha = (1.0 - level) / 2.0
    interval.lower = round(_percentile(estimates, alpha), 4)
    interval.upper = round(_percentile(estimates, 1.0 - alpha), 4)
    interval.note = (
        "percentile interval over conversation-unit resampling; tasks from the "
        "same conversation are kept together"
    )
    return interval


def cluster_metric_values(
    evaluations: Sequence[TaskEvaluation],
    references: Mapping[str, PrivateTaskReference],
    metric: str,
) -> dict[str, list[float]]:
    """Group per-task metric values by conversation unit."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for evaluation in evaluations:
        if evaluation.business.scoring_status != "scored":
            continue
        value = evaluation.business.metrics.get(metric)
        if value is None:
            continue
        reference = references.get(evaluation.task_id)
        cluster = reference.conversation_id if reference is not None else evaluation.task_id
        grouped[cluster].append(float(value))
    return dict(grouped)


def classify_failures(
    evaluations: Sequence[TaskEvaluation],
    references: Mapping[str, PrivateTaskReference],
    *,
    output_reviews: Mapping[str, float] | None = None,
) -> FailureAnalysis:
    """Classify every non-successful task into one or more failure classes.

    A task can appear in several classes (for example a wrong branch that also
    misses a required step), because the classes describe behaviour, not a
    partition.
    """
    analysis = FailureAnalysis()
    counts: Counter[str] = Counter()
    reviews = output_reviews or {}
    any_failure = 0

    for evaluation in evaluations:
        classes: list[str] = []
        technical = evaluation.technical
        business = evaluation.business
        if not technical.output_valid:
            classes.append(FAILURE_FORMATTING)
        if business.scoring_status != "scored":
            if business.reason in {
                "task_marked_ambiguous_or_unscorable",
                "candidate_pending_review",
            }:
                classes.append(FAILURE_UNSCORABLE)
        else:
            metrics = business.metrics
            success = metrics.get(METRIC_EXPERT_SUCCESS)
            if success is None:
                success = metrics.get(METRIC_DECISION_SUCCESS)
            if metrics.get(METRIC_CLARIFICATION) is not None:
                if metrics[METRIC_CLARIFICATION] < 1.0:
                    classes.append(FAILURE_MISSED_CLARIFICATION)
            elif success is not None and success < 1.0:
                branch = metrics.get(METRIC_BRANCH)
                if branch is not None and branch < 1.0:
                    classes.append(FAILURE_WRONG_BRANCH)
                elif metrics.get(METRIC_STEP_RECALL, 1.0) < 1.0:
                    classes.append(FAILURE_MISSING_REQUIRED_STEP)
                elif metrics.get(METRIC_STEP_PRECISION, 1.0) < 1.0:
                    classes.append(FAILURE_UNNECESSARY_STEP)
                else:
                    classes.append(FAILURE_WRONG_DECISION)
            if metrics.get(METRIC_POLICY_COMPLIANCE) == 0.0:
                classes.append(FAILURE_POLICY_VIOLATION)
        quality = reviews.get(evaluation.task_id)
        if quality is not None and quality < RESPONSE_QUALITY_FLOOR:
            classes.append(FAILURE_POOR_RESPONSE)
        if classes:
            any_failure += 1
            for item in dict.fromkeys(classes):
                counts[item] += 1

    analysis.failed_tasks = any_failure
    analysis.counts = {name: counts.get(name, 0) for name in FAILURE_CLASSES}
    analysis.notes.append(
        "A task may appear in more than one class: the classes describe observable "
        "behaviour, not a partition of the failures."
    )
    if not any(reviews):
        analysis.notes.append(
            "response quality was not reviewed for this run, so "
            f"{FAILURE_POOR_RESPONSE} is reported as zero rather than estimated"
        )
    return analysis


def _context_bucket(turns: int) -> str:
    if turns <= 4:
        return "2-4 turns"
    if turns <= 8:
        return "5-8 turns"
    if turns <= 14:
        return "9-14 turns"
    return "15+ turns"


def _complexity_bucket(customer_turns: int) -> str:
    if customer_turns <= 1:
        return "1 customer turn"
    if customer_turns == 2:
        return "2 customer turns"
    return "3+ customer turns"


def stratify_results(
    evaluations: Sequence[TaskEvaluation],
    references: Mapping[str, PrivateTaskReference],
    visible_turn_counts: Mapping[str, int],
    customer_turn_counts: Mapping[str, int],
) -> list[StratifiedResult]:
    """Compute the success rate within observable task strata.

    Only observable dimensions are used. The corpus has no verified business-domain
    label, so no domain-stratified result is produced or claimed.
    """
    buckets: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for evaluation in evaluations:
        if evaluation.business.scoring_status != "scored":
            continue
        metrics = evaluation.business.metrics
        success = metrics.get(METRIC_EXPERT_SUCCESS)
        if success is None:
            success = metrics.get(METRIC_DECISION_SUCCESS)
        if success is None:
            continue
        turns = visible_turn_counts.get(evaluation.task_id, 0)
        customers = customer_turn_counts.get(evaluation.task_id, 0)
        buckets[("visible_context_length", _context_bucket(turns))].append(
            success == 1.0
        )
        buckets[("customer_turn_count", _complexity_bucket(customers))].append(
            success == 1.0
        )

    rows: list[StratifiedResult] = []
    for (dimension, bucket), outcomes in sorted(buckets.items()):
        successes = sum(1 for outcome in outcomes if outcome)
        rows.append(
            StratifiedResult(
                dimension=dimension,
                bucket=bucket,
                tasks=len(outcomes),
                scored=len(outcomes),
                successes=successes,
                success_rate=round(successes / len(outcomes), 4) if outcomes else None,
            )
        )
    return rows


def build_report_data(
    *,
    run_set: str,
    condition: str,
    benchmark_version: str,
    dataset_version: str,
    split: str,
    model_provider: str,
    model_name: str,
    model_parameters: Mapping[str, object],
    prompt_version: str,
    evaluator_version: str,
    agent_version: str,
    config_hash: str,
    evaluations: Sequence[TaskEvaluation],
    references: Mapping[str, PrivateTaskReference],
    aggregate: AggregateMetrics,
    output_reviews: Mapping[str, float] | None = None,
    visible_turn_counts: Mapping[str, int] | None = None,
    customer_turn_counts: Mapping[str, int] | None = None,
    agent_outputs: Mapping[str, Mapping[str, object]] | None = None,
) -> BaselineReportData:
    """Assemble every number the baseline report needs."""
    reviews = output_reviews or {}
    intervals = [
        cluster_bootstrap_ci(
            cluster_metric_values(evaluations, references, metric),
            metric=AGGREGATE_METRIC_NAMES[metric],
        )
        for metric in (
            METRIC_EXPERT_SUCCESS,
            METRIC_POLICY_SUCCESS,
            METRIC_BRANCH,
            METRIC_STEP_RECALL,
            METRIC_STEP_PRECISION,
            METRIC_CLARIFICATION,
            METRIC_POLICY_COMPLIANCE,
        )
    ]
    if reviews:
        review_values: dict[str, list[float]] = defaultdict(list)
        for evaluation in evaluations:
            if evaluation.task_id in reviews:
                reference = references.get(evaluation.task_id)
                cluster = (
                    reference.conversation_id
                    if reference is not None
                    else evaluation.task_id
                )
                review_values[cluster].append(float(reviews[evaluation.task_id]))
        intervals.append(
            cluster_bootstrap_ci(
                dict(review_values), metric=AGGREGATE_METRIC_NAMES[METRIC_RESPONSE_QUALITY]
            )
        )

    report = BaselineReportData(
        run_set=run_set,
        condition=condition,
        benchmark_version=benchmark_version,
        dataset_version=dataset_version,
        split=split,
        model_provider=model_provider,
        model_name=model_name,
        model_parameters=dict(model_parameters),
        prompt_version=prompt_version,
        evaluator_version=evaluator_version,
        agent_version=agent_version,
        config_hash=config_hash,
        aggregate=aggregate,
        confidence_intervals=intervals,
        failures=classify_failures(evaluations, references, output_reviews=reviews),
        independent_sampling_unit="conversation unit",
        annotation_status={
            "scored_tasks": aggregate.scored_tasks,
            "unscored_tasks": aggregate.unscored_tasks,
            "scored_fraction": aggregate.scored_fraction,
            "unscored_reasons": aggregate.unscored_reason_counts,
            "expert_tier_tasks": aggregate.expert_tier_tasks,
            "policy_tier_tasks": aggregate.policy_tier_tasks,
        },
        limitations=[
            "The corpus carries no verified business-domain label, so no "
            "domain-stratified result exists. Only observable strata are reported.",
            "Speaker roles come from the research protocol; the corpus stores "
            "role=UNKNOWN for every record.",
            "No business backend or tool contract exists, so tool-intent accuracy "
            "and business task completion remain unavailable.",
            "Channels 3/4/5 are unresolved; units containing their speech are "
            "conservatively excluded from task cutoffs.",
            "Conversation identity is inferred from one delivered transcript unit, "
            "not confirmed by an external call record.",
        ],
    )
    if visible_turn_counts and customer_turn_counts:
        report.stratified = stratify_results(
            evaluations, references, visible_turn_counts, customer_turn_counts
        )
    if agent_outputs:
        report.behavioural_profile = behavioural_profile(agent_outputs)
    if aggregate.scored_tasks == 0:
        report.pending_dependencies = [
            "Human annotation of the evaluation sample has not been completed, so "
            "no business performance number can be reported.",
            "The expert-adjudicated tier needs two independent human annotations "
            "per task plus adjudication of disagreements.",
            "The policy-verified tier needs an authoritative China Mobile business "
            "rule set, which does not exist in this workspace.",
            "Response quality needs a blind human review of the agent outputs.",
        ]
        report.notes.append(
            "Technical performance above is real and measured. Business performance "
            "is explicitly NOT measured: reporting a rate from zero scored tasks "
            "would be a fabricated result."
        )
    if report.behavioural_profile:
        report.notes.append(
            "The behavioural profile describes what the agent produced. It is not a "
            "correctness measure and must not be reported as one."
        )
    return report


def render_report_markdown(report: BaselineReportData) -> str:
    """Render the Group A business baseline report."""
    aggregate = report.aggregate
    technical = aggregate.technical
    lines: list[str] = [
        "# Group A Business Baseline Report",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Run set | `{report.run_set}` |",
        f"| Condition | {report.condition} (no flowchart, no workflow) |",
        f"| Benchmark version | `{report.benchmark_version}` |",
        f"| Dataset | `{report.dataset_version}` |",
        f"| Split | `{report.split}` |",
        f"| Provider label | `{report.model_provider}` |",
        f"| Model | `{report.model_name}` |",
        f"| Model parameters | `{_json(report.model_parameters)}` |",
        f"| Prompt version | `{report.prompt_version}` |",
        f"| Evaluator version | `{report.evaluator_version}` |",
        f"| Agent version | `{report.agent_version}` |",
        f"| Config hash | `{report.config_hash}` |",
        f"| Independent sampling unit | {report.independent_sampling_unit} |",
        "",
        "## A. Technical performance",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tasks executed | {aggregate.tasks_total} |",
        f"| Request success rate | {technical.get('request_success_rate')} |",
        f"| Valid output rate | {technical.get('valid_output_rate')} |",
        f"| Timeout rate | {technical.get('timeout_rate')} |",
        f"| Latency p50 (ms) | {technical.get('latency_ms_p50')} |",
        f"| Latency p90 (ms) | {technical.get('latency_ms_p90')} |",
        f"| Tasks with token usage | {technical.get('tasks_with_token_usage')} |",
        f"| Total tokens | {technical.get('total_tokens')} |",
        f"| Failed executions | {aggregate.execution_status_counts} |",
        "",
        "## B. Business performance",
        "",
        "Two evaluation claims are reported separately and are never averaged:",
        "",
        "| Metric | Value | Denominator | Note |",
        "|---|---:|---:|---|",
    ]
    for metric in AGGREGATE_METRIC_NAMES.values():
        value = aggregate.business_metrics.get(metric)
        denominator = aggregate.business_metric_denominators.get(metric, 0)
        shown = "not available" if value is None else str(value)
        reason = aggregate.unavailable_metrics.get(metric, "")
        lines.append(
            f"| `{metric}` | {shown} | {denominator} | {reason or 'measured'} |"
        )
    lines.extend(
        [
            "",
            "Structurally unavailable in this milestone:",
            "",
        ]
    )
    for name, reason in STRUCTURALLY_UNAVAILABLE_METRICS.items():
        lines.append(f"- `{name}` — {reason}")
    lines.extend(
        [
            "",
            "### Scoring status",
            "",
            "| Fact | Value |",
            "|---|---|",
            f"| Total executed | {aggregate.tasks_total} |",
            f"| Scored | {aggregate.scored_tasks} |",
            f"| Unscored | {aggregate.unscored_tasks} |",
            f"| Candidate (pending review) | {aggregate.candidate_tasks} |",
            f"| Invalid reference | {aggregate.invalid_tasks} |",
            f"| Scored fraction | {aggregate.scored_fraction} |",
            f"| Unscored reasons | `{_json(aggregate.unscored_reason_counts)}` |",
            f"| Expert-tier scored tasks | {aggregate.expert_tier_tasks} |",
            f"| Policy-tier scored tasks | {aggregate.policy_tier_tasks} |",
            f"| Both tiers | {aggregate.both_tier_tasks} |",
            "",
        ]
    )

    lines.extend(
        [
            "## C. Error analysis",
            "",
            f"Tasks with at least one classified failure: {report.failures.failed_tasks}",
            "",
            "| Failure class | Count |",
            "|---|---:|",
        ]
    )
    for name, count in report.failures.counts.items():
        lines.append(f"| `{name}` | {count} |")
    lines.append("")

    if report.stratified:
        lines.extend(
            [
                "### Results by observable task characteristics",
                "",
                "| Dimension | Bucket | Tasks | Successes | Success rate |",
                "|---|---|---:|---:|---:|",
            ]
        )
        for row in report.stratified:
            lines.append(
                f"| {row.dimension} | {row.bucket} | {row.tasks} | {row.successes} | "
                f"{row.success_rate} |"
            )
        lines.append("")

    if report.confidence_intervals:
        lines.extend(
            [
                "## D. Statistical reporting",
                "",
                "95% percentile intervals from a cluster bootstrap that resamples "
                "conversation units, not individual tasks. A metric with fewer than "
                "two independent clusters gets a point estimate and no interval.",
                "",
                "| Metric | Point estimate | 95% CI | Units | Clusters |",
                "|---|---:|---|---:|---:|",
            ]
        )
        for interval in report.confidence_intervals:
            ci = (
                "not available"
                if interval.lower is None
                else f"[{interval.lower}, {interval.upper}]"
            )
            point = (
                "not available"
                if interval.point_estimate is None
                else str(interval.point_estimate)
            )
            lines.append(
                f"| `{interval.metric}` | {point} | {ci} | {interval.units} | "
                f"{interval.clusters} |"
            )
        lines.append("")
        single = [
            interval
            for interval in report.confidence_intervals
            if interval.note and "fewer than two independent clusters" in interval.note
        ]
        if single:
            lines.append(
                "At least one metric has fewer than two independent clusters, so no "
                "interval is quoted for it."
            )
            lines.append("")

    if report.behavioural_profile:
        lines.extend(
            [
                "## E. Descriptive behavioural profile",
                "",
                "**This section is not a correctness measure.** It describes the shape "
                "of the agent's answers so the run is informative while annotation is "
                "pending. A long answer is not a correct answer and a repeated action "
                "is not necessarily a wrong one.",
                "",
                "| Observation | Value |",
                "|---|---|",
            ]
        )
        profile = report.behavioural_profile
        lines.append(f"| Answers profiled | {profile.get('tasks')} |")
        responses = profile.get("customer_response_chars", {})
        if isinstance(responses, dict):
            lines.append(
                f"| `customer_response` length, chars (min / p50 / max) | "
                f"{responses.get('min')} / {responses.get('p50')} / "
                f"{responses.get('max')} |"
            )
        actions = profile.get("next_action_chars", {})
        if isinstance(actions, dict):
            lines.append(
                f"| `next_action` length, chars (min / p50 / max) | "
                f"{actions.get('min')} / {actions.get('p50')} / {actions.get('max')} |"
            )
        lines.extend(
            [
                f"| `evidence_summary` length, chars (p50) | "
                f"{profile.get('evidence_summary_chars_p50')} |",
                f"| Answers with non-empty `required_information` | "
                f"{profile.get('outputs_with_required_information')} "
                f"({profile.get('required_information_fraction')}) |",
                f"| `required_information` item counts | "
                f"`{_json(profile.get('required_information_item_counts', {}))}` |",
                f"| Answers with a non-null `uncertainty` | "
                f"{profile.get('outputs_with_uncertainty')} |",
                f"| Distinct `next_action` values | "
                f"{profile.get('distinct_next_action_values')} of "
                f"{profile.get('tasks')} |",
                "",
            ]
        )
        repeats = profile.get("most_common_next_action") or []
        if repeats:
            lines.extend(
                [
                    "Most frequent stated actions (verbatim, aggregate only):",
                    "",
                    "| Action | Count |",
                    "|---|---:|",
                ]
            )
            for item in repeats:
                if isinstance(item, dict):
                    value = str(item.get("value", "")).replace("|", "\\|")
                    lines.append(f"| {value} | {item.get('count')} |")
            lines.append("")

    if report.pending_dependencies:
        lines.extend(
            [
                "## Pending dependencies",
                "",
                "The business metrics above are `not available` for a concrete, "
                "unfinished reason:",
                "",
            ]
        )
        for item in report.pending_dependencies:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(
        [
            "## Limitations",
            "",
        ]
    )
    for item in report.limitations:
        lines.append(f"- {item}")
    lines.append("")
    if report.notes:
        lines.append("## Notes")
        lines.append("")
        for item in report.notes:
            lines.append(f"- {item}")
        lines.append("")
    lines.extend(
        [
            "## Integrity",
            "",
            "This report is generated from the run-set traces, the private "
            "references and optional human output reviews. It contains aggregate "
            "measurements only: no conversation text, no prompt text and no "
            "credential.",
            "",
        ]
    )
    return "\n".join(lines)


def report_digest(report: BaselineReportData) -> str:
    """Return a stable digest of the report payload, for reproducibility checks."""
    payload = report.model_dump(mode="json")
    return hashlib.sha256(
        _json(payload).encode("utf-8")
    ).hexdigest()


def _json(payload: object) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "BUSINESS_REPORT_VERSION",
    "BaselineReportData",
    "ConfidenceInterval",
    "FAILURE_CLASSES",
    "FailureAnalysis",
    "RESPONSE_QUALITY_FLOOR",
    "StratifiedResult",
    "build_report_data",
    "classify_failures",
    "cluster_bootstrap_ci",
    "cluster_metric_values",
    "render_report_markdown",
    "report_digest",
    "stratify_results",
]
