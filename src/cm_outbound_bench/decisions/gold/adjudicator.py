"""DeepSeek V4 Pro adjudication of annotation pass disagreements.

The adjudicator receives the same agent-visible prefix, the two pass annotations
and the fixed annotation guidelines. It never receives the historical
continuation, Group A outputs, hidden evaluator references or Test data.

Outcomes: accept pass A, accept pass B, accept both, produce a consensus
annotation, mark the task ambiguous, or mark it unscored. Consensus is never
forced when the visible evidence does not support it.
"""

from __future__ import annotations

import json
from typing import Any

from cm_outbound_bench.decisions.gold.models import (
    AdjudicationResult,
    PassAnnotation,
    extract_json_object,
    parse_annotation_json,
)
from cm_outbound_bench.decisions.gold.prompts import (
    ANNOTATION_ADJUDICATION,
    build_annotation_messages,
    prompt_sha256,
)
from cm_outbound_bench.decisions.gold.provider import JudgeClient, JudgeResponse
from cm_outbound_bench.decisions.gold.storage import utc_now
from cm_outbound_bench.decisions.schema import PublicDecisionTask

_OUTCOME_MAP = {
    "accept_pass_a": ("accept_pass_a", "A"),
    "accept_pass_b": ("accept_pass_b", "B"),
    "accept_both": ("accept_both", "both"),
    "accept_both_as_valid_alternatives": ("accept_both", "both"),
    "consensus": ("consensus", None),
    "new_consensus": ("consensus", None),
    "ambiguous": ("ambiguous", None),
    "unscored": ("unscored", None),
    "mark_unscored": ("unscored", None),
}


class AdjudicationError(RuntimeError):
    """Raised when an adjudication call fails irrecoverably."""


def render_pass_annotation(annotation: PassAnnotation, label: str) -> str:
    """Render one pass annotation compactly for the adjudication prompt."""
    payload = {
        "task_answerability": annotation.task_answerability,
        "customer_intent": annotation.customer_intent,
        "conversation_state": annotation.conversation_state,
        "primary_expected_decision": (
            annotation.primary_expected_decision.model_dump(mode="json")
            if annotation.primary_expected_decision is not None
            else None
        ),
        "acceptable_alternative_decisions": [
            decision.model_dump(mode="json")
            for decision in annotation.acceptable_alternative_decisions
        ],
        "required_information": annotation.required_information,
        "required_steps": annotation.required_steps,
        "optional_steps": annotation.optional_steps,
        "prohibited_steps": annotation.prohibited_steps,
        "branch_conditions": [
            branch.model_dump(mode="json") for branch in annotation.branch_conditions
        ],
        "expected_branch": annotation.expected_branch,
        "external_policy_dependency": annotation.external_policy_dependency,
        "policy_verified": annotation.policy_verified,
        "unsupported_claims_to_avoid": annotation.unsupported_claims_to_avoid,
        "evidence_refs": [
            item.model_dump(mode="json") for item in annotation.evidence_refs
        ],
        "confidence": annotation.confidence,
        "scoreable": annotation.scoreable,
        "unscorable_reason": annotation.unscorable_reason,
    }
    return (
        f"标注{label}的完整结果：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )


def adjudicate(
    task: PublicDecisionTask,
    pass_a: PassAnnotation,
    pass_b: PassAnnotation,
    *,
    client: JudgeClient,
    prompt_version: str,
    config_hash: str,
    model: str,
    provider_label: str,
    temperature: float,
    max_output_tokens: int,
    guidelines_text: str = "",
    project_root: Any | None = None,
) -> AdjudicationResult:
    """Run one stateless adjudication call and parse the outcome."""
    prompt_text = build_annotation_messages(
        task,
        prompt_name=ANNOTATION_ADJUDICATION,
        prompt_version=prompt_version,
        extra={
            "固定标注指南": guidelines_text or "(随提示文件提供)",
            "标注A（第一遍独立标注）": render_pass_annotation(pass_a, "A"),
            "标注B（第二遍独立标注）": render_pass_annotation(pass_b, "B"),
        },
        project_root=project_root,
    )
    system, user = prompt_text
    prompt_hash = prompt_sha256(system + "\n\u0000\n" + user)
    try:
        response: JudgeResponse = client.complete_json(
            system=system,
            user=user,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
    except Exception as error:
        return AdjudicationResult(
            task_id=task.task_id,
            outcome="unscored",
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            config_hash=config_hash,
            requested_at=utc_now(),
            parsing_status="failed",
            raw_response_text=None,
            error={
                "code": type(error).__name__,
                "message": str(error)[:300],
            },
        )

    payload = extract_json_object(response.content)
    if payload is None:
        return AdjudicationResult(
            task_id=task.task_id,
            outcome="unscored",
            model=model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            config_hash=config_hash,
            requested_at=utc_now(),
            api_request_id=response.api_request_id,
            model_reported=response.model_reported,
            response_hash=response.response_hash,
            retry_count=response.retry_count,
            token_usage=response.token_usage,
            parsing_status="failed",
            raw_response_text=response.content,
            error={
                "code": "no_json_object",
                "message": "the adjudication response contained no JSON object",
            },
        )
    raw_outcome = str(payload.get("outcome", "")).strip().casefold()
    outcome, accepted_pass = _OUTCOME_MAP.get(raw_outcome, ("unscored", None))
    consensus: PassAnnotation | None = None
    parsing_status = "ok"
    if outcome == "consensus":
        consensus_payload = payload.get("consensus_annotation") if payload else None
        if isinstance(consensus_payload, dict):
            consensus = parse_annotation_json(
                consensus_payload, task_id=task.task_id, pass_id="A"
            )
            consensus.pass_id = "C"  # adjudicator-produced consensus
        else:
            outcome = "unscored"
            parsing_status = "repaired"
    if outcome == "unscored" and raw_outcome:
        parsing_status = "ok"

    return AdjudicationResult(
        task_id=task.task_id,
        outcome=outcome,
        accepted_pass=accepted_pass,
        resolution_note=str(payload.get("reason", "")) if payload else "",
        consensus=consensus,
        model=model,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        config_hash=config_hash,
        requested_at=utc_now(),
        api_request_id=response.api_request_id,
        model_reported=response.model_reported,
        response_hash=response.response_hash,
        retry_count=response.retry_count,
        token_usage=response.token_usage,
        parsing_status=parsing_status,
        raw_response_text=response.content,
    )


__all__ = [
    "AdjudicationError",
    "adjudicate",
    "render_pass_annotation",
]
