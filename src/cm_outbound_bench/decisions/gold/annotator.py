"""Two-pass DeepSeek V4 Pro annotation pipeline.

For every selected task the pipeline runs two independent, stateless annotation
passes on exactly the agent-visible prefix, compares them semantically, and
adjudicates disagreements with a separate V4 Pro call. The adopted final state is
the official Benchmark Gold Label candidate; freezing and reference application
happen in :mod:`cm_outbound_bench.decisions.gold.validation`.

Independence is structural: pass B is built without pass A's output, the
adjudicator sees only the two pass records and the fixed guidelines, and no code
path ever renders the historical continuation, Group A outputs or Test data.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Sequence

from cm_outbound_bench.decisions.gold.adjudicator import adjudicate
from cm_outbound_bench.decisions.gold.agreement import compare_passes
from cm_outbound_bench.decisions.gold.config import JudgeConfig
from cm_outbound_bench.decisions.gold.models import (
    COMPARISON_ACCEPTABLE_ALTERNATIVES,
    COMPARISON_AGREEMENT,
    COMPARISON_BOTH_UNSCORED,
    COMPARISON_MAJOR,
    COMPARISON_MINOR,
    PASS_A,
    PASS_B,
    FinalGoldState,
    PassAnnotation,
    PassComparison,
    PassRecord,
    extract_json_object,
    parse_annotation_json,
)
from cm_outbound_bench.decisions.gold.prompts import (
    ANNOTATION_PASS_A,
    ANNOTATION_PASS_B,
    build_annotation_messages,
    prompt_sha256,
)
from cm_outbound_bench.decisions.gold.provider import JudgeClient
from cm_outbound_bench.decisions.gold.storage import GoldStorage, sha256_text, utc_now
from cm_outbound_bench.decisions.schema import PublicDecisionTask

_CONFIDENCE_LEVEL = {"low": 0, "medium": 1, "high": 2}
_ADJUDICATION_CLASSES = frozenset({COMPARISON_MINOR, COMPARISON_MAJOR})


class AnnotationError(RuntimeError):
    """Raised when the annotation pipeline cannot proceed."""


@dataclass(frozen=True)
class AnnotationContext:
    """Everything one pipeline run needs. Never holds a credential."""

    config: JudgeConfig
    storage: GoldStorage
    client_factory: Callable[[], JudgeClient]
    model: str
    provider_label: str
    annotation_config_hash: str
    prompt_version: str
    dry_run: bool = False


def run_pass(
    context: AnnotationContext,
    split: str,
    stage: str,
    task: PublicDecisionTask,
    pass_id: str,
) -> PassRecord:
    """Run (or reuse) one annotation pass for one task.

    Resume rule: a stored record whose cache key matches the current task,
    dataset, model, prompt hash and configuration hash is reused verbatim. A
    mismatched key means the inputs changed, so the record is regenerated.
    """
    prompt_name = ANNOTATION_PASS_A if pass_id == PASS_A else ANNOTATION_PASS_B
    system, user = build_annotation_messages(
        task,
        prompt_name=prompt_name,
        prompt_version=context.prompt_version,
    )
    prompt_hash = prompt_sha256(system + "\n\u0000\n" + user)
    cache_key = context.storage.cache_key(
        task.task_id,
        pass_id=pass_id,
        model=context.model,
        prompt_hash=prompt_hash,
        config_hash=context.annotation_config_hash,
    )
    cached = context.storage.load_pass(
        split,
        stage,
        task.task_id,
        pass_id,
        expected_cache_key=cache_key,
    )
    if cached is not None and cached.ok:
        return cached
    # A failed or parse-failed record is not a usable annotation: retry it.

    if context.dry_run:
        return PassRecord(
            run_id="dry-run",
            task_id=task.task_id,
            pass_id=pass_id,
            stage="annotation",
            model=context.model,
            provider=context.provider_label,
            prompt_version=context.prompt_version,
            prompt_hash=prompt_hash,
            config_hash=context.annotation_config_hash,
            cache_key=cache_key,
            temperature=context.config.temperature,
            max_output_tokens=context.config.max_output_tokens,
            requested_at=utc_now(),
            parsing_status="skipped",
            notes=["dry run: no API call was made"],
        )

    client = context.client_factory()
    requested = utc_now()
    try:
        response = client.complete_json(
            system=system,
            user=user,
            temperature=context.config.temperature,
            max_output_tokens=context.config.max_output_tokens,
            seed=context.config.seed,
        )
    except Exception as error:
        record = PassRecord(
            run_id="api-failed",
            task_id=task.task_id,
            pass_id=pass_id,
            stage="annotation",
            model=context.model,
            provider=context.provider_label,
            prompt_version=context.prompt_version,
            prompt_hash=prompt_hash,
            config_hash=context.annotation_config_hash,
            cache_key=cache_key,
            temperature=context.config.temperature,
            max_output_tokens=context.config.max_output_tokens,
            requested_at=requested,
            parsing_status="failed",
            error={"code": type(error).__name__, "message": str(error)[:300]},
            notes=[f"{pass_id}-pass API call failed"],
        )
        context.storage.save_pass(split, stage, record)
        raise AnnotationError(
            f"task {task.task_id} pass {pass_id}: {error}"
        ) from error

    payload = extract_json_object(response.content)
    if payload is None:
        record = PassRecord(
            run_id="parse-failed",
            task_id=task.task_id,
            pass_id=pass_id,
            stage="annotation",
            model=context.model,
            provider=context.provider_label,
            prompt_version=context.prompt_version,
            prompt_hash=prompt_hash,
            config_hash=context.annotation_config_hash,
            cache_key=cache_key,
            temperature=context.config.temperature,
            max_output_tokens=context.config.max_output_tokens,
            requested_at=requested,
            api_request_id=response.api_request_id,
            model_reported=response.model_reported,
            response_hash=response.response_hash,
            retry_count=response.retry_count,
            token_usage=response.token_usage,
            parsing_status="failed",
            raw_response_text=response.content,
            notes=["no JSON object found in the model response"],
        )
        context.storage.save_pass(split, stage, record)
        raise AnnotationError(
            f"task {task.task_id} pass {pass_id}: no JSON object in response"
        )

    annotation = parse_annotation_json(
        payload, task_id=task.task_id, pass_id=pass_id
    )
    record = PassRecord(
        run_id="gold-pass",
        task_id=task.task_id,
        pass_id=pass_id,
        stage="annotation",
        model=context.model,
        provider=context.provider_label,
        prompt_version=context.prompt_version,
        prompt_hash=prompt_hash,
        config_hash=context.annotation_config_hash,
        cache_key=cache_key,
        temperature=context.config.temperature,
        max_output_tokens=context.config.max_output_tokens,
        requested_at=requested,
        api_request_id=response.api_request_id,
        model_reported=response.model_reported,
        response_hash=response.response_hash,
        retry_count=response.retry_count,
        token_usage=response.token_usage,
        parsing_status="ok",
        annotation=annotation,
        raw_response_text=response.content,
    )
    context.storage.save_pass(split, stage, record)
    return record


def _union(left: PassAnnotation, right: PassAnnotation) -> PassAnnotation:
    """Merge two annotations that accept each other's decisions."""
    alternatives: list = list(left.acceptable_alternative_decisions)
    seen = {_key(left.primary_expected_decision.action)}
    if right.primary_expected_decision is not None:
        alternatives.append(right.primary_expected_decision)
        seen.add(_key(right.primary_expected_decision.action))
    for decision in right.acceptable_alternative_decisions:
        if _key(decision.action) not in seen:
            alternatives.append(decision)
            seen.add(_key(decision.action))
    return PassAnnotation(
        task_id=left.task_id,
        pass_id="U",
        task_answerability=left.task_answerability,
        customer_intent=left.customer_intent,
        conversation_state=left.conversation_state,
        primary_expected_decision=left.primary_expected_decision,
        acceptable_alternative_decisions=alternatives,
        required_information=_dedupe(
            [*left.required_information, *right.required_information]
        ),
        required_steps=_dedupe([*left.required_steps, *right.required_steps]),
        optional_steps=_dedupe([*left.optional_steps, *right.optional_steps]),
        prohibited_steps=_dedupe(
            [*left.prohibited_steps, *right.prohibited_steps]
        ),
        branch_conditions=_dedupe(
            [*left.branch_conditions, *right.branch_conditions],
            key=lambda item: item.condition,
        ),
        expected_branch=left.expected_branch or right.expected_branch,
        external_policy_dependency=(
            left.external_policy_dependency or right.external_policy_dependency
        ),
        policy_verified=left.policy_verified and right.policy_verified,
        unsupported_claims_to_avoid=_dedupe(
            [*left.unsupported_claims_to_avoid, *right.unsupported_claims_to_avoid]
        ),
        evidence_refs=_dedupe(
            [*left.evidence_refs, *right.evidence_refs],
            key=lambda item: item.visible_turn,
        ),
        assumptions_not_allowed=_dedupe(
            [*left.assumptions_not_allowed, *right.assumptions_not_allowed]
        ),
        confidence=_min_confidence(left.confidence, right.confidence),
        scoreable=left.scoreable or right.scoreable,
        unscorable_reason=left.unscorable_reason or right.unscorable_reason,
        notes=(
            f"union of passes A and B ({left.notes} / {right.notes})".strip(" /")
        ),
    )


def _key(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _dedupe(items: Sequence, key=None) -> list:
    result: list = []
    seen: set = set()
    for item in items:
        marker = key(item) if key is not None else str(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def _min_confidence(left: str, right: str) -> str:
    if _CONFIDENCE_LEVEL[left] <= _CONFIDENCE_LEVEL[right]:
        return left
    return right


def adopt_final(
    task_id: str,
    pass_a: PassAnnotation,
    pass_b: PassAnnotation,
    comparison: PassComparison,
    adjudication,
) -> FinalGoldState:
    """Pure adoption logic: comparison + adjudication -> final state."""
    if comparison.classification in {COMPARISON_AGREEMENT, COMPARISON_BOTH_UNSCORED}:
        final = (
            pass_a
            if comparison.classification == COMPARISON_AGREEMENT
            else _unscored(pass_a)
        )
        return FinalGoldState(
            task_id=task_id,
            comparison=comparison,
            final=final,
            scoring_status="scored" if final.scoreable else "unscored",
            source="pass_a_common",
        )

    if comparison.classification == COMPARISON_ACCEPTABLE_ALTERNATIVES:
        final = _union(pass_a, pass_b)
        return FinalGoldState(
            task_id=task_id,
            comparison=comparison,
            final=final,
            scoring_status="scored" if final.scoreable else "unscored",
            source="pass_union",
            notes=["both passes accept each other's decisions; union adopted"],
        )

    if comparison.classification in _ADJUDICATION_CLASSES:
        final, scoring, source = _apply_adjudication(
            adjudication, pass_a, pass_b
        )
        return FinalGoldState(
            task_id=task_id,
            comparison=comparison,
            adjudication=adjudication,
            final=final,
            scoring_status=scoring,
            source=source,
            notes=list(comparison.notes),
        )

    raise AnnotationError(
        f"task {task_id}: unknown comparison class {comparison.classification!r}"
    )


def finalize_task(
    context: AnnotationContext,
    split: str,
    stage: str,
    task: PublicDecisionTask,
    pass_a: PassAnnotation,
    pass_b: PassAnnotation,
    comparison: PassComparison,
) -> FinalGoldState:
    """Adjudicate when required, adopt the final state, and persist it.

    A failed adjudication API call (429 / provider error / unparseable response)
    is not a decision: it leaves the task without a final state so a later resume
    retries it, instead of silently excluding a scorable task.
    """
    adjudication = None
    if comparison.classification in _ADJUDICATION_CLASSES:
        adjudication = _adjudicate(context, split, stage, task, pass_a, pass_b)
        if adjudication is not None and adjudication.parsing_status == "failed":
            raise AnnotationError(
                f"task {task.task_id}: adjudication failed "
                f"({(adjudication.error or {}).get('code', 'unknown')}); task left "
                "unfinalized for retry"
            )
        if not context.dry_run and adjudication is not None:
            context.storage.save_adjudication(split, stage, adjudication)
    state = adopt_final(task.task_id, pass_a, pass_b, comparison, adjudication)
    if not context.dry_run:
        context.storage.save_final(split, stage, state)
    return state


def recompare_stage(
    context: AnnotationContext,
    split: str,
    stage: str,
    tasks: Sequence[PublicDecisionTask],
) -> dict[str, FinalGoldState]:
    """Re-derive comparisons and finals from cached pass records.

    No pass API call is made: the cached pass annotations stay as they are. This
    is the sanctioned way to apply a refined comparison rubric after a pilot
    analysis. Adjudications that are still required are reused from cache;
    adjudication rows for tasks that no longer need one are dropped when the
    JSONL artifacts are rewritten wholesale.
    """
    comparisons: list[PassComparison] = []
    adjudications = []
    finals: dict[str, FinalGoldState] = {}
    for task in tasks:
        record_a = context.storage.load_pass(
            split,
            stage,
            task.task_id,
            PASS_A,
            expected_cache_key=_cached_key_of(context, split, stage, task, PASS_A),
        )
        record_b = context.storage.load_pass(
            split,
            stage,
            task.task_id,
            PASS_B,
            expected_cache_key=_cached_key_of(context, split, stage, task, PASS_B),
        )
        if (
            record_a is None
            or record_b is None
            or record_a.annotation is None
            or record_b.annotation is None
        ):
            raise AnnotationError(
                f"task {task.task_id}: cached pass records are missing; run the "
                "annotation passes first"
            )
        comparison = compare_passes(record_a.annotation, record_b.annotation)
        comparisons.append(comparison)
        adjudication = None
        if comparison.classification in _ADJUDICATION_CLASSES:
            adjudication = _adjudicate(
                context,
                split,
                stage,
                task,
                record_a.annotation,
                record_b.annotation,
            )
            if adjudication is not None and adjudication.parsing_status == "failed":
                raise AnnotationError(
                    f"task {task.task_id}: adjudication failed during "
                    "re-comparison; rerun when the endpoint is available"
                )
            if adjudication is not None:
                adjudications.append(adjudication)
        finals[task.task_id] = adopt_final(
            task.task_id,
            record_a.annotation,
            record_b.annotation,
            comparison,
            adjudication,
        )
    if not context.dry_run:
        context.storage.rewrite_jsonl(
            context.storage.comparisons_path(split, stage), comparisons
        )
        context.storage.rewrite_jsonl(
            context.storage.adjudications_path(split, stage), adjudications
        )
        context.storage.rewrite_jsonl(
            context.storage.finals_path(split, stage), list(finals.values())
        )
    return finals


def _cached_key_of(
    context: AnnotationContext,
    split: str,
    stage: str,
    task: PublicDecisionTask,
    pass_id: str,
) -> str:
    prompt_name = ANNOTATION_PASS_A if pass_id == PASS_A else ANNOTATION_PASS_B
    system, user = build_annotation_messages(
        task,
        prompt_name=prompt_name,
        prompt_version=context.prompt_version,
    )
    prompt_hash = prompt_sha256(system + "\n\u0000\n" + user)
    return context.storage.cache_key(
        task.task_id,
        pass_id=pass_id,
        model=context.model,
        prompt_hash=prompt_hash,
        config_hash=context.annotation_config_hash,
    )


def _unscored(reference: PassAnnotation) -> PassAnnotation:
    return PassAnnotation(
        task_id=reference.task_id,
        pass_id=reference.pass_id,
        task_answerability=reference.task_answerability,
        customer_intent=reference.customer_intent,
        conversation_state=reference.conversation_state,
        external_policy_dependency=reference.external_policy_dependency,
        policy_verified=reference.policy_verified,
        evidence_refs=reference.evidence_refs,
        confidence=reference.confidence,
        scoreable=False,
        unscorable_reason=reference.unscorable_reason,
        notes=reference.notes,
    )


def _adjudicate(
    context: AnnotationContext,
    split: str,
    stage: str,
    task: PublicDecisionTask,
    pass_a: PassAnnotation,
    pass_b: PassAnnotation,
):
    from cm_outbound_bench.decisions.gold.prompts import (
        ANNOTATION_ADJUDICATION,
        load_prompt,
    )

    guidelines = ""
    adjudication_prompt_hash = ""
    try:
        guidelines = load_prompt(ANNOTATION_ADJUDICATION, context.prompt_version)
        adjudication_prompt_hash = prompt_sha256(guidelines)
    except Exception:
        guidelines = ""
    if context.dry_run:
        return None
    cache_key = sha256_text(
        "\n".join(
            [
                task.task_id,
                json.dumps(pass_a.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                json.dumps(pass_b.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                context.model,
                context.annotation_config_hash,
                adjudication_prompt_hash,
            ]
        )
    )
    cached = context.storage.load_adjudication_record(
        split, stage, task.task_id, expected_cache_key=cache_key
    )
    if cached is not None:
        return cached
    result = adjudicate(
        task,
        pass_a,
        pass_b,
        client=context.client_factory(),
        prompt_version=context.prompt_version,
        config_hash=context.annotation_config_hash,
        model=context.model,
        provider_label=context.provider_label,
        temperature=context.config.temperature,
        max_output_tokens=context.config.max_output_tokens,
        guidelines_text=guidelines,
    )
    result.cache_key = cache_key
    context.storage.save_adjudication_record(split, stage, result)
    return result


def _apply_adjudication(
    result,
    pass_a: PassAnnotation,
    pass_b: PassAnnotation,
) -> tuple[PassAnnotation | None, str, str]:
    """Return (final annotation, scoring_status, source)."""
    if result is None:
        return _unscored(pass_a), "unscored", "adjudication_failed"
    outcome = result.outcome
    if outcome == "accept_pass_a":
        return pass_a, _scored(pass_a), "adjudication:accept_pass_a"
    if outcome == "accept_pass_b":
        return pass_b, _scored(pass_b), "adjudication:accept_pass_b"
    if outcome == "accept_both":
        merged = _union(pass_a, pass_b)
        return merged, _scored(merged), "adjudication:accept_both"
    if outcome == "consensus":
        consensus = result.consensus
        if consensus is None:
            return _unscored(pass_a), "unscored", "adjudication:consensus_failed"
        return consensus, _scored(consensus), "adjudication:consensus"
    if outcome == "ambiguous":
        ambiguous = _unscored(pass_a)
        ambiguous.task_answerability = "ambiguous"
        if not ambiguous.unscorable_reason:
            ambiguous.unscorable_reason = result.resolution_note or "ambiguous"
        return ambiguous, "unscored", "adjudication:ambiguous"
    return _unscored(pass_a), "unscored", f"adjudication:{outcome}"


def _scored(annotation: PassAnnotation) -> str:
    return "scored" if annotation.scoreable else "unscored"


def run_annotation_stage(
    context: AnnotationContext,
    split: str,
    stage: str,
    tasks: Sequence[PublicDecisionTask],
    *,
    concurrency: int | None = None,
) -> dict[str, FinalGoldState]:
    """Run both passes, comparison and adjudication for a list of tasks."""
    workers = concurrency or context.config.annotation.concurrency
    finals: dict[str, FinalGoldState] = {}

    def _run_one(task: PublicDecisionTask) -> tuple[str, PassRecord, PassRecord]:
        record_a = run_pass(context, split, stage, task, PASS_A)
        record_b = run_pass(context, split, stage, task, PASS_B)
        return task.task_id, record_a, record_b

    records: dict[str, tuple[PassRecord, PassRecord]] = {}
    failures: list[str] = []
    if workers > 1 and tasks:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_one, task): task for task in tasks}
            for future in as_completed(futures):
                try:
                    task_id, record_a, record_b = future.result()
                    records[task_id] = (record_a, record_b)
                except AnnotationError as error:
                    failures.append(str(error))
    else:
        for task in tasks:
            try:
                task_id, record_a, record_b = _run_one(task)
                records[task_id] = (record_a, record_b)
            except AnnotationError as error:
                failures.append(str(error))

    for task in tasks:
        if task.task_id not in records:
            # The passes failed after retries; leave the task without a final
            # state so the run report can list it as remaining. No fabricated
            # annotation and no fabricated API success is recorded.
            continue
        record_a, record_b = records[task.task_id]
        if record_a.annotation is None or record_b.annotation is None:
            # A pass failed to parse. Never adjudicate over a missing annotation:
            # the task stays unscored with an explicit reason and can be repaired
            # by rerunning (the failed record has no usable cache entry).
            comparison = PassComparison(
                task_id=task.task_id,
                classification=COMPARISON_MAJOR,
                notes=["a pass record could not be parsed; task left unscored"],
            )
            if not context.dry_run:
                context.storage.save_comparison(split, stage, comparison)
            state = FinalGoldState(
                task_id=task.task_id,
                comparison=comparison,
                final=None,
                scoring_status="unscored",
                source="pass_failed",
                notes=["annotation pass failed"],
            )
            if not context.dry_run:
                context.storage.save_final(split, stage, state)
            finals[task.task_id] = state
            continue
        comparison = compare_passes(record_a.annotation, record_b.annotation)
        if not context.dry_run:
            context.storage.save_comparison(split, stage, comparison)
        finals[task.task_id] = finalize_task(
            context,
            split,
            stage,
            task,
            record_a.annotation,
            record_b.annotation,
            comparison,
        )
    return finals


__all__ = [
    "AnnotationContext",
    "AnnotationError",
    "FinalGoldState",
    "PassAnnotation",
    "PassComparison",
    "PassRecord",
    "adopt_final",
    "finalize_task",
    "recompare_stage",
    "run_annotation_stage",
    "run_pass",
]
