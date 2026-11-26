"""Blind two-pass scoring of Group A outputs against frozen gold references.

Both scoring passes are stateless and receive exactly: the agent-visible task
prefix, the frozen gold label and the agent's structured output. The rubric is
fixed in the prompt files. No pass receives the other pass's decision, the agent
model identity, the experimental condition, the historical continuation or Test
references. The frozen gold label is never modified by scoring.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Mapping

from cm_outbound_bench.decisions.gold.prompts import (
    SCORING_PASS_A,
    SCORING_PASS_B,
    load_prompt,
    prompt_sha256,
    render_task_prefix,
)
from cm_outbound_bench.decisions.gold.provider import JudgeClient
from cm_outbound_bench.decisions.gold.storage import utc_now
from cm_outbound_bench.decisions.schema import PrivateTaskReference, PublicDecisionTask
from cm_outbound_bench.decisions.scoring.schema import (
    CRITICAL_ERROR_KINDS,
    OUTCOME_UNSCORABLE,
    SCORE_AGREEMENT,
    SCORE_DISAGREEMENT,
    SCORER_A,
    SCORER_B,
    CriticalError,
    FinalScore,
    ScoreAdjudication,
    ScoreComparison,
    ScoreOutcome,
    ScoreRecord,
)


@dataclass(frozen=True)
class ScoringContext:
    """Everything the scoring pipeline needs. Never holds a credential."""

    prompt_version: str
    model: str
    provider_label: str
    config_hash: str
    temperature: float
    max_output_tokens: int
    ground_truth_version: str
    client_factory: Callable[[], JudgeClient]
    dry_run: bool = False


class ScoringError(RuntimeError):
    """Raised when the scoring pipeline cannot proceed."""


def render_gold_label(reference: PrivateTaskReference) -> str:
    """Render the frozen gold label for the scorer, without any agent identity."""
    payload = {
        "task_answerability": reference.task_answerability,
        "primary_expected_decision": (
            {
                "category": reference.primary_expected_decision.decision_category,
                "action": reference.primary_expected_decision.label,
                "reason": reference.primary_expected_decision.rationale,
            }
            if reference.primary_expected_decision is not None
            else None
        ),
        "acceptable_alternative_decisions": [
            {
                "category": decision.decision_category,
                "action": decision.label,
                "reason": decision.rationale,
            }
            for decision in reference.acceptable_alternative_decisions
        ],
        "required_information": list(reference.required_information),
        "expected_clarification": list(reference.expected_clarification),
        "required_steps": list(reference.required_steps),
        "optional_steps": list(reference.optional_steps),
        "prohibited_steps": list(reference.prohibited_steps),
        "unsupported_claims_to_avoid": list(reference.unsupported_claims_to_avoid),
        "external_policy_dependency": reference.external_policy_dependency,
        "unscorable_reason": reference.unscorable_reason,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_agent_output(agent_output: Mapping[str, object] | None) -> str:
    if not agent_output:
        return "(无有效输出)"
    return json.dumps(agent_output, ensure_ascii=False, indent=2)


def _build_messages(
    task: PublicDecisionTask,
    gold: PrivateTaskReference,
    agent_output: Mapping[str, object] | None,
    *,
    prompt_name: str,
    prompt_version: str,
) -> tuple[str, str]:
    system = load_prompt(prompt_name, prompt_version)
    user = "\n\n".join(
        [
            render_task_prefix(task),
            "【冻结的基准金标签】",
            render_gold_label(gold),
            "【客服代表的实际输出】",
            render_agent_output(agent_output),
        ]
    )
    return system, user


def scoring_cache_key(
    *,
    task_id: str,
    gold_version: str,
    reference_hash: str,
    model: str,
    prompt_hash: str,
    config_hash: str,
    scorer_id: str,
) -> str:
    payload = "\n".join(
        [
            task_id,
            gold_version,
            reference_hash,
            model,
            prompt_hash,
            config_hash,
            scorer_id,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_outcome(payload: dict, *, task_id: str, scorer_id: str) -> ScoreOutcome:
    outcome = str(payload.get("outcome", OUTCOME_UNSCORABLE)).strip().casefold()
    if outcome not in {"fully_correct", "partially_correct", "incorrect", "unscorable"}:
        outcome = OUTCOME_UNSCORABLE
    critical: list[CriticalError] = []
    for item in payload.get("critical_errors") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip().casefold()
        if kind not in CRITICAL_ERROR_KINDS:
            continue
        critical.append(
            CriticalError(kind=kind, description=str(item.get("description", "")))
        )
    confidence = str(payload.get("confidence", "medium")).strip().casefold()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    return ScoreOutcome(
        task_id=task_id,
        scorer_id=scorer_id,  # type: ignore[arg-type]
        outcome=outcome,
        rationale=str(payload.get("rationale", "")),
        critical_errors=critical,
        confidence=confidence,  # type: ignore[arg-type]
    )


def score_one(
    context: ScoringContext,
    task: PublicDecisionTask,
    gold: PrivateTaskReference,
    agent_output: Mapping[str, object] | None,
    scorer_id: str,
    *,
    cached: ScoreRecord | None = None,
) -> ScoreRecord:
    """Run (or reuse) one blind scoring pass for one task."""
    prompt_name = SCORING_PASS_A if scorer_id == SCORER_A else SCORING_PASS_B
    system, user = _build_messages(
        task, gold, agent_output, prompt_name=prompt_name, prompt_version=context.prompt_version
    )
    prompt_hash = prompt_sha256(system + "\n\u0000\n" + user)
    reference_hash = hashlib.sha256(
        json.dumps(gold.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    cache_key = scoring_cache_key(
        task_id=task.task_id,
        gold_version=context.ground_truth_version,
        reference_hash=reference_hash,
        model=context.model,
        prompt_hash=prompt_hash,
        config_hash=context.config_hash,
        scorer_id=scorer_id,
    )
    if cached is not None and cached.cache_key == cache_key:
        return cached

    if context.dry_run:
        return ScoreRecord(
            run_id="dry-run",
            task_id=task.task_id,
            scorer_id=scorer_id,
            model=context.model,
            provider=context.provider_label,
            prompt_version=context.prompt_version,
            prompt_hash=prompt_hash,
            config_hash=context.config_hash,
            ground_truth_version=context.ground_truth_version,
            reference_hash=reference_hash,
            cache_key=cache_key,
            temperature=context.temperature,
            max_output_tokens=context.max_output_tokens,
            requested_at=utc_now(),
            parsing_status="skipped",
            error=None,
        )

    client = context.client_factory()
    try:
        response = client.complete_json(
            system=system,
            user=user,
            temperature=context.temperature,
            max_output_tokens=context.max_output_tokens,
        )
    except Exception as error:
        raise ScoringError(
            f"task {task.task_id} scorer {scorer_id}: {error}"
        ) from error

    payload = _extract_json(response.content)
    parsing_status = "ok"
    if payload is None:
        payload = {}
        parsing_status = "failed"
    outcome = _parse_outcome(payload, task_id=task.task_id, scorer_id=scorer_id)
    return ScoreRecord(
        run_id="gold-score",
        task_id=task.task_id,
        scorer_id=scorer_id,
        model=context.model,
        provider=context.provider_label,
        prompt_version=context.prompt_version,
        prompt_hash=prompt_hash,
        config_hash=context.config_hash,
        ground_truth_version=context.ground_truth_version,
        reference_hash=reference_hash,
        cache_key=cache_key,
        temperature=context.temperature,
        max_output_tokens=context.max_output_tokens,
        requested_at=utc_now(),
        api_request_id=response.api_request_id,
        model_reported=response.model_reported,
        response_hash=response.response_hash,
        retry_count=response.retry_count,
        token_usage=response.token_usage,
        parsing_status=parsing_status,
        outcome=outcome if parsing_status == "ok" else None,
        raw_response_text=response.content,
    )


def _extract_json(text: str) -> dict | None:
    from cm_outbound_bench.decisions.gold.models import extract_json_object

    return extract_json_object(text)


def compare_scores(left: ScoreOutcome, right: ScoreOutcome) -> ScoreComparison:
    same_outcome = left.outcome == right.outcome
    same_critical = _critical_keys(left) == _critical_keys(right)
    return ScoreComparison(
        task_id=left.task_id,
        classification=SCORE_AGREEMENT if same_outcome else SCORE_DISAGREEMENT,
        same_outcome=same_outcome,
        same_critical_errors=same_critical,
        notes=[] if same_outcome else [f"{left.outcome} vs {right.outcome}"],
    )


def _critical_keys(outcome: ScoreOutcome) -> frozenset[str]:
    return frozenset(item.kind for item in outcome.critical_errors)


def adjudicate_scores(
    context: ScoringContext,
    task: PublicDecisionTask,
    gold: PrivateTaskReference,
    agent_output: Mapping[str, object] | None,
    score_a: ScoreOutcome,
    score_b: ScoreOutcome,
) -> ScoreAdjudication:
    """One stateless adjudication call for a scorer disagreement."""
    from cm_outbound_bench.decisions.gold.prompts import SCORING_ADJUDICATION

    system = load_prompt(SCORING_ADJUDICATION, context.prompt_version)
    user = "\n\n".join(
        [
            render_task_prefix(task),
            "【冻结的基准金标签】",
            render_gold_label(gold),
            "【客服代表的实际输出】",
            render_agent_output(agent_output),
            "【评分A的结论】",
            json.dumps(score_a.model_dump(mode="json"), ensure_ascii=False, indent=2),
            "【评分B的结论】",
            json.dumps(score_b.model_dump(mode="json"), ensure_ascii=False, indent=2),
        ]
    )
    prompt_hash = prompt_sha256(system + "\n\u0000\n" + user)
    if context.dry_run:
        return ScoreAdjudication(
            task_id=task.task_id,
            outcome=OUTCOME_UNSCORABLE,
            prompt_hash=prompt_hash,
            config_hash=context.config_hash,
            model=context.model,
            prompt_version=context.prompt_version,
            requested_at=utc_now(),
            parsing_status="skipped",
        )
    try:
        response = context.client_factory().complete_json(
            system=system,
            user=user,
            temperature=context.temperature,
            max_output_tokens=context.max_output_tokens,
        )
    except Exception as error:
        raise ScoringError(
            f"task {task.task_id} score adjudication: {error}"
        ) from error
    payload = _extract_json(response.content) or {}
    outcome = _parse_outcome(
        payload, task_id=task.task_id, scorer_id=SCORER_A
    )
    return ScoreAdjudication(
        task_id=task.task_id,
        outcome=outcome.outcome,
        rationale=outcome.rationale,
        critical_errors=outcome.critical_errors,
        model=context.model,
        prompt_version=context.prompt_version,
        prompt_hash=prompt_hash,
        config_hash=context.config_hash,
        requested_at=utc_now(),
        api_request_id=response.api_request_id,
        model_reported=response.model_reported,
        response_hash=response.response_hash,
        retry_count=response.retry_count,
        token_usage=response.token_usage,
        parsing_status="ok",
        raw_response_text=response.content,
    )


def finalize_score(
    comparison: ScoreComparison,
    score_a: ScoreOutcome,
    score_b: ScoreOutcome,
    adjudication: ScoreAdjudication | None,
) -> FinalScore:
    """Adopt the final score: common outcome on agreement, adjudication otherwise."""
    if comparison.classification == SCORE_AGREEMENT:
        return FinalScore(
            task_id=score_a.task_id,
            outcome=score_a.outcome,
            critical_errors=list(score_a.critical_errors),
            comparison=comparison,
            source="scorer_agreement",
            rationale=score_a.rationale,
        )
    if adjudication is not None:
        return FinalScore(
            task_id=score_a.task_id,
            outcome=adjudication.outcome,
            critical_errors=list(adjudication.critical_errors),
            comparison=comparison,
            adjudication=adjudication,
            source="scorer_adjudication",
            rationale=adjudication.rationale,
        )
    return FinalScore(
        task_id=score_a.task_id,
        outcome=OUTCOME_UNSCORABLE,
        critical_errors=[],
        comparison=comparison,
        source="adjudication_unavailable",
        rationale="scorers disagreed and no adjudication result exists",
    )


def aggregate_saved_scores(
    *,
    gold: Mapping[str, PrivateTaskReference],
    records_a: Mapping[str, ScoreRecord],
    records_b: Mapping[str, ScoreRecord],
    adjudications: Mapping[str, ScoreAdjudication] | None = None,
) -> tuple[dict[str, FinalScore], list[str]]:
    """Re-derive final scores from already-saved passes, with no API call.

    This is the sanctioned way to re-apply the aggregation rules (for example
    after the ``is_scored`` gold gate was tightened) without re-running the
    judge. It obeys exactly the same rule as :func:`score_dataset`: a task whose
    frozen Gold Label is not scoreable becomes ``unscorable`` with source
    ``gold_reference_unscored`` and never enters a business metric denominator.
    Saved adjudications are reused only for tasks that still disagree.
    """
    adjudications = adjudications or {}
    finals: dict[str, FinalScore] = {}
    problems: list[str] = []
    for task_id in sorted(gold):
        if not gold[task_id].is_scored:
            finals[task_id] = FinalScore(
                task_id=task_id,
                outcome=OUTCOME_UNSCORABLE,
                source="gold_reference_unscored",
            )
            continue
        left = records_a.get(task_id)
        right = records_b.get(task_id)
        if left is None or right is None:
            problems.append(f"{task_id}: saved scoring pass is missing")
            continue
        if left.outcome is None or right.outcome is None:
            finals[task_id] = FinalScore(
                task_id=task_id,
                outcome=OUTCOME_UNSCORABLE,
                source="scorer_parse_failure",
            )
            continue
        comparison = compare_scores(left.outcome, right.outcome)
        adjudication = (
            adjudications.get(task_id)
            if comparison.classification == SCORE_DISAGREEMENT
            else None
        )
        finals[task_id] = finalize_score(
            comparison, left.outcome, right.outcome, adjudication
        )
    return finals, problems


def score_dataset(
    context: ScoringContext,
    *,
    tasks: Mapping[str, PublicDecisionTask],
    gold: Mapping[str, PrivateTaskReference],
    agent_outputs: Mapping[str, Mapping[str, object] | None],
    cache: Mapping[str, Mapping[str, ScoreRecord]] | None = None,
    concurrency: int = 4,
    on_record: Callable[[str, ScoreRecord, ScoreRecord], None] | None = None,
) -> tuple[dict[str, FinalScore], dict[str, ScoreRecord], dict[str, ScoreRecord], list[str]]:
    """Run the full blind scoring pipeline over a dataset of tasks.

    ``on_record`` is invoked as soon as both passes of one task complete, so a
    later API failure cannot lose completed scoring work.
    """
    task_ids = sorted(tasks)
    cache = cache or {}
    records_a: dict[str, ScoreRecord] = {}
    records_b: dict[str, ScoreRecord] = {}
    finals: dict[str, FinalScore] = {}
    problems: list[str] = []
    failures: dict[str, str] = {}

    # Only tasks whose frozen Gold Label is actually scoreable may enter LLM
    # scoring. An unscored gold reference can never produce a scoreable verdict,
    # whatever the judge would say about the agent output.
    scorable_ids = [task_id for task_id in task_ids if gold[task_id].is_scored]
    unscored_gold_ids = [task_id for task_id in task_ids if task_id not in set(scorable_ids)]

    def _run(task_id: str) -> tuple[str, ScoreRecord, ScoreRecord]:
        task = tasks[task_id]
        reference = gold[task_id]
        output = agent_outputs.get(task_id)
        cached_a = cache.get("A", {}).get(task_id)
        cached_b = cache.get("B", {}).get(task_id)
        record_a = score_one(
            context, task, reference, output, SCORER_A, cached=cached_a
        )
        record_b = score_one(
            context, task, reference, output, SCORER_B, cached=cached_b
        )
        return task_id, record_a, record_b

    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_run, task_id): task_id for task_id in scorable_ids
            }
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    task_id, record_a, record_b = future.result()
                except ScoringError as error:
                    failures[task_id] = str(error)
                    continue
                records_a[task_id] = record_a
                records_b[task_id] = record_b
                if on_record is not None:
                    on_record(task_id, record_a, record_b)
    else:
        for task_id in scorable_ids:
            try:
                task_id, record_a, record_b = _run(task_id)
            except ScoringError as error:
                failures[task_id] = str(error)
                continue
            records_a[task_id] = record_a
            records_b[task_id] = record_b
            if on_record is not None:
                on_record(task_id, record_a, record_b)

    for task_id in unscored_gold_ids:
        finals[task_id] = FinalScore(
            task_id=task_id,
            outcome=OUTCOME_UNSCORABLE,
            source="gold_reference_unscored",
        )

    for task_id in task_ids:
        if task_id not in records_a:
            if task_id in finals:
                continue  # unscored gold reference: excluded, not a problem
            # The scoring calls failed after retries; report the task as remaining
            # rather than fabricating an unscorable verdict for it.
            problems.append(
                f"{task_id}: scoring call failed ({failures.get(task_id, 'unknown')})"
            )
            continue
        record_a = records_a[task_id]
        record_b = records_b[task_id]
        if record_a.outcome is None or record_b.outcome is None:
            problems.append(f"{task_id}: a scoring pass failed to parse")
            finals[task_id] = FinalScore(
                task_id=task_id,
                outcome=OUTCOME_UNSCORABLE,
                source="scorer_parse_failure",
            )
            continue
        comparison = compare_scores(record_a.outcome, record_b.outcome)
        adjudication: ScoreAdjudication | None = None
        if comparison.classification == SCORE_DISAGREEMENT:
            adjudication = adjudicate_scores(
                context,
                tasks[task_id],
                gold[task_id],
                agent_outputs.get(task_id),
                record_a.outcome,
                record_b.outcome,
            )
        finals[task_id] = finalize_score(
            comparison, record_a.outcome, record_b.outcome, adjudication
        )
    return finals, records_a, records_b, problems


__all__ = [
    "ScoringContext",
    "ScoringError",
    "adjudicate_scores",
    "aggregate_saved_scores",
    "compare_scores",
    "finalize_score",
    "render_agent_output",
    "render_gold_label",
    "score_dataset",
    "score_one",
    "scoring_cache_key",
]
