"""Milestone 1.2 gold pipeline models: passes, comparisons, adjudications.

The models here are the *process* records. The final adopted state is always
written onto a :class:`cm_outbound_bench.decisions.schema.PrivateTaskReference`,
so the Gold Labels live in the same reference storage as every other task — there
is no disconnected annotation database.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from cm_outbound_bench.decisions.annotation import (
    ANSWERABILITY_AMBIGUOUS,
    CONFIDENCE_VALUES,
    DECISION_CATEGORY_OTHER,
    DECISION_CATEGORY_VALUES,
    LLM_GOLD_ANSWERABILITY_VALUES,
    TASK_ANSWERABILITY_VALUES,
)
from cm_outbound_bench.domain.data_model import DomainModel

PASS_A = "A"
PASS_B = "B"

COMPARISON_AGREEMENT = "agreement"
COMPARISON_ACCEPTABLE_ALTERNATIVES = "acceptable_alternatives"
COMPARISON_MINOR = "minor_disagreement"
COMPARISON_MAJOR = "major_disagreement"
COMPARISON_BOTH_UNSCORED = "both_unscored"

COMPARISON_CLASSES: tuple[str, ...] = (
    COMPARISON_AGREEMENT,
    COMPARISON_ACCEPTABLE_ALTERNATIVES,
    COMPARISON_MINOR,
    COMPARISON_MAJOR,
    COMPARISON_BOTH_UNSCORED,
)

ADJUDICATION_OUTCOMES: tuple[str, ...] = (
    "accept_pass_a",
    "accept_pass_b",
    "accept_both",
    "consensus",
    "ambiguous",
    "unscored",
)

_OLD_TO_NEW_ANSWERABILITY = {
    "sufficient_information": "sufficient_for_action",
    "more_information_required": "clarification_required",
    "unscorable": "not_a_valid_business_decision_point",
    "unknown": "insufficient_context",
}

#: Answerability values that can support a scored gold reference. A
#: policy-dependent task is scored only when its approved decision is a
#: clarification/verification/deferral (checked in validation).
GOLD_SCOREABLE_ANSWERABILITY: frozenset[str] = frozenset(
    {"sufficient_for_action", "clarification_required"}
)


class GoldError(RuntimeError):
    """Raised when a gold artifact is invalid."""


class EvidenceRef(DomainModel):
    """One evidence reference into the agent-visible prefix."""

    visible_turn: int = Field(ge=1)
    quote: str = ""


class GoldBranch(DomainModel):
    """One conditional branch the annotator recorded."""

    condition: str = Field(min_length=1)
    if_branch: str = ""
    else_branch: str = ""


class GoldDecision(DomainModel):
    """One acceptable expected decision in the gold annotation."""

    decision_id: str = Field(min_length=1)
    category: str = Field(default=DECISION_CATEGORY_OTHER)
    action: str = Field(min_length=1)
    reason: str = ""

    @model_validator(mode="after")
    def validate_category(self) -> "GoldDecision":
        if self.category not in DECISION_CATEGORY_VALUES:
            self.category = DECISION_CATEGORY_OTHER
        return self


class PassAnnotation(DomainModel):
    """The structured annotation produced by one V4 Pro pass.

    ``pass_id`` is ``A``/``B`` for the two independent passes, ``C`` for an
    adjudicator-produced consensus annotation and ``U`` for a union of two
    passes that accept each other's decisions.
    """

    task_id: str = Field(min_length=1)
    pass_id: Literal["A", "B", "C", "U"]
    task_answerability: str
    customer_intent: str = ""
    conversation_state: str = ""
    primary_expected_decision: GoldDecision | None = None
    acceptable_alternative_decisions: list[GoldDecision] = Field(
        default_factory=list
    )
    required_information: list[str] = Field(default_factory=list)
    required_steps: list[str] = Field(default_factory=list)
    optional_steps: list[str] = Field(default_factory=list)
    prohibited_steps: list[str] = Field(default_factory=list)
    branch_conditions: list[GoldBranch] = Field(default_factory=list)
    expected_branch: str | None = None
    external_policy_dependency: bool = False
    policy_verified: bool = False
    unsupported_claims_to_avoid: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    assumptions_not_allowed: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    scoreable: bool = False
    unscorable_reason: str = ""
    notes: str = ""

    @property
    def decision_categories(self) -> list[str]:
        return [
            decision.category
            for decision in (
                [self.primary_expected_decision]
                if self.primary_expected_decision is not None
                else []
            )
            + self.acceptable_alternative_decisions
        ]


class PassRecord(DomainModel):
    """Reproducibility metadata plus the parsed result of one API call."""

    record_version: str = "1.0"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    pass_id: str = Field(min_length=1)
    stage: str = "annotation"
    model: str = ""
    provider: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    config_hash: str = ""
    cache_key: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 0
    requested_at: str = ""
    api_request_id: str = ""
    model_reported: str = ""
    response_hash: str = ""
    retry_count: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    parsing_status: str = "pending"
    annotation: PassAnnotation | None = None
    raw_response_text: str | None = None
    error: dict[str, Any] | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.parsing_status == "ok" and self.annotation is not None


class PassComparison(DomainModel):
    """Semantic comparison of pass A and pass B for one task."""

    task_id: str = Field(min_length=1)
    classification: str = COMPARISON_MAJOR
    answerability_match: bool = False
    scoreability_match: bool = False
    category_match: bool = False
    decision_similarity: float = 0.0
    required_information_jaccard: float | None = None
    required_steps_jaccard: float | None = None
    prohibited_steps_jaccard: float | None = None
    external_policy_match: bool = False
    confidence_match: bool = False
    evidence_overlap: float | None = None
    notes: list[str] = Field(default_factory=list)


class AdjudicationResult(DomainModel):
    """One V4 Pro adjudication call and its structured outcome."""

    task_id: str = Field(min_length=1)
    outcome: str = "unscored"
    accepted_pass: str | None = None
    resolution_note: str = ""
    consensus: PassAnnotation | None = None
    cache_key: str = ""
    model: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    config_hash: str = ""
    requested_at: str = ""
    api_request_id: str = ""
    model_reported: str = ""
    response_hash: str = ""
    retry_count: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    parsing_status: str = "pending"
    raw_response_text: str | None = None
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "AdjudicationResult":
        if self.outcome not in ADJUDICATION_OUTCOMES:
            raise ValueError(f"unknown adjudication outcome {self.outcome!r}")
        if self.outcome == "consensus" and self.consensus is None:
            raise ValueError("a consensus outcome must carry the consensus annotation")
        if self.outcome in {"accept_pass_a", "accept_pass_b", "accept_both"} and self.accepted_pass not in {"A", "B", "both"}:
            raise ValueError(
                f"outcome {self.outcome} requires accepted_pass, got "
                f"{self.accepted_pass!r}"
            )
        return self


class FinalGoldState(DomainModel):
    """The adopted final state of one task after comparison and adjudication."""

    task_id: str = Field(min_length=1)
    comparison: PassComparison
    adjudication: AdjudicationResult | None = None
    final: PassAnnotation | None = None
    scoring_status: Literal["scored", "unscored"] = "unscored"
    source: str = "pass_a"  # pass_a | pass_b | union | adjudication_consensus
    notes: list[str] = Field(default_factory=list)


def normalize_answerability(value: str) -> str:
    """Map old and new answerability vocabulary onto the LLM-gold vocabulary."""
    candidate = (value or "").strip().casefold()
    if candidate in LLM_GOLD_ANSWERABILITY_VALUES:
        return candidate
    if candidate in _OLD_TO_NEW_ANSWERABILITY:
        return _OLD_TO_NEW_ANSWERABILITY[candidate]
    # Remaining old values that are shared between vocabularies.
    if candidate == ANSWERABILITY_AMBIGUOUS:
        return ANSWERABILITY_AMBIGUOUS
    if candidate in TASK_ANSWERABILITY_VALUES:
        return "insufficient_context"
    return "insufficient_context"


def is_scoreable_answerability(value: str) -> bool:
    return normalize_answerability(value) in GOLD_SCOREABLE_ANSWERABILITY


def parse_annotation_json(
    payload: dict[str, Any],
    *,
    task_id: str,
    pass_id: str,
) -> PassAnnotation:
    """Parse one model JSON object into a PassAnnotation, with defined repairs.

    The parser is deliberately forgiving about casing and synonyms, but strict
    about scoreability consistency: a task cannot be both ambiguous and scored.
    Unknown categories fall back to ``other`` and unknown answerability values to
    ``insufficient_context``.
    """
    answerability = normalize_answerability(str(payload.get("task_answerability", "")))
    primary = _parse_decision(
        payload.get("primary_expected_decision"), decision_id="gold-primary"
    )
    alternatives = [
        _parse_decision(item, decision_id=f"gold-alt-{index}")
        for index, item in enumerate(
            payload.get("acceptable_alternative_decisions") or [], start=1
        )
        if isinstance(item, dict)
    ]
    alternatives = [item for item in alternatives if item is not None]
    evidence = [
        EvidenceRef(
            visible_turn=int(item.get("visible_turn", 0)),
            quote=str(item.get("quote", "")),
        )
        for item in (payload.get("evidence_refs") or [])
        if isinstance(item, dict) and int(item.get("visible_turn", 0)) >= 1
    ]
    confidence = str(payload.get("confidence", "medium")).strip().casefold()
    if confidence not in CONFIDENCE_VALUES:
        confidence = "medium"
    scoreable = bool(payload.get("scoreable", False))
    branches = [
        GoldBranch(
            condition=str(item.get("condition", "")),
            if_branch=str(item.get("if_branch", "")),
            else_branch=str(item.get("else_branch", "")),
        )
        for item in (payload.get("branch_conditions") or [])
        if isinstance(item, dict) and str(item.get("condition", "")).strip()
    ]
    expected_branch_value = payload.get("expected_branch")
    expected_branch = (
        str(expected_branch_value) if expected_branch_value not in (None, "") else None
    )
    annotation = PassAnnotation(
        task_id=task_id,
        pass_id=pass_id,
        task_answerability=answerability,
        customer_intent=str(payload.get("customer_intent", "")),
        conversation_state=str(payload.get("conversation_state", "")),
        primary_expected_decision=primary,
        acceptable_alternative_decisions=alternatives,
        required_information=_string_list(payload.get("required_information")),
        required_steps=_string_list(payload.get("required_steps")),
        optional_steps=_string_list(payload.get("optional_steps")),
        prohibited_steps=_string_list(payload.get("prohibited_steps")),
        branch_conditions=branches,
        expected_branch=expected_branch,
        external_policy_dependency=bool(
            payload.get("external_policy_dependency", False)
        ),
        policy_verified=bool(payload.get("policy_verified", False)),
        unsupported_claims_to_avoid=_string_list(
            payload.get("unsupported_claims_to_avoid")
        ),
        evidence_refs=evidence,
        assumptions_not_allowed=_string_list(payload.get("assumptions_not_allowed")),
        confidence=confidence,  # type: ignore[arg-type]
        scoreable=scoreable,
        unscorable_reason=str(payload.get("unscorable_reason") or ""),
        notes=str(payload.get("notes", "")),
    )
    # Deterministic consistency repairs.
    if not is_scoreable_answerability(answerability):
        annotation.scoreable = False
    if annotation.scoreable and primary is None and not annotation.required_information:
        annotation.scoreable = False
        if not annotation.unscorable_reason:
            annotation.unscorable_reason = (
                "no primary decision and no required information were established"
            )
    if annotation.task_answerability == "clarification_required" and not (
        annotation.required_information
    ):
        annotation.scoreable = False
        if not annotation.unscorable_reason:
            annotation.unscorable_reason = (
                "clarification_required must name the information to request"
            )
    return annotation


def _parse_decision(payload: Any, *, decision_id: str) -> GoldDecision | None:
    if not isinstance(payload, dict):
        return None
    action = str(payload.get("action", "")).strip()
    if not action:
        return None
    return GoldDecision(
        decision_id=decision_id,
        category=str(payload.get("category", DECISION_CATEGORY_OTHER))
        .strip()
        .casefold(),
        action=action,
        reason=str(payload.get("reason", "")),
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model response, fence or not."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = cleaned.find("{", start + 1)
    return None


__all__ = [
    "ADJUDICATION_OUTCOMES",
    "COMPARISON_ACCEPTABLE_ALTERNATIVES",
    "COMPARISON_AGREEMENT",
    "COMPARISON_BOTH_UNSCORED",
    "COMPARISON_CLASSES",
    "COMPARISON_MAJOR",
    "COMPARISON_MINOR",
    "GOLD_SCOREABLE_ANSWERABILITY",
    "PASS_A",
    "PASS_B",
    "AdjudicationResult",
    "EvidenceRef",
    "FinalGoldState",
    "GoldBranch",
    "GoldDecision",
    "GoldError",
    "PassAnnotation",
    "PassComparison",
    "PassRecord",
    "extract_json_object",
    "is_scoreable_answerability",
    "normalize_answerability",
    "parse_annotation_json",
]
