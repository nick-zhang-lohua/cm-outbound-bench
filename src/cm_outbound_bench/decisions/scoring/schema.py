"""Scoring result models for the blind Group A output evaluation.

Outcomes are the four primary rubric classes; critical errors are flagged
independently and never invented when evidence is insufficient.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from cm_outbound_bench.domain.data_model import DomainModel

OUTCOME_FULLY_CORRECT = "fully_correct"
OUTCOME_PARTIALLY_CORRECT = "partially_correct"
OUTCOME_INCORRECT = "incorrect"
OUTCOME_UNSCORABLE = "unscorable"

OUTCOME_VALUES: tuple[str, ...] = (
    OUTCOME_FULLY_CORRECT,
    OUTCOME_PARTIALLY_CORRECT,
    OUTCOME_INCORRECT,
    OUTCOME_UNSCORABLE,
)

CRITICAL_ERROR_KINDS: frozenset[str] = frozenset(
    {
        "fabricated_business_policy",
        "fabricated_package_eligibility",
        "unsupported_pricing_claim",
        "contradicts_explicit_customer_information",
        "unjustified_completion_claim",
    }
)

SCORER_A = "A"
SCORER_B = "B"

SCORE_AGREEMENT = "agreement"
SCORE_DISAGREEMENT = "disagreement"


class ScoringError(RuntimeError):
    """Raised when a scoring record is invalid."""


class CriticalError(DomainModel):
    """One independently flagged critical error."""

    kind: str
    description: str = ""

    @model_validator(mode="after")
    def validate_kind(self) -> "CriticalError":
        if self.kind not in CRITICAL_ERROR_KINDS:
            raise ValueError(f"unknown critical error kind {self.kind!r}")
        return self


class ScoreOutcome(DomainModel):
    """The structured decision of one scoring pass."""

    task_id: str = Field(min_length=1)
    scorer_id: Literal["A", "B"]
    outcome: str
    rationale: str = ""
    critical_errors: list[CriticalError] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"

    @model_validator(mode="after")
    def validate_outcome(self) -> "ScoreOutcome":
        if self.outcome not in OUTCOME_VALUES:
            raise ValueError(f"unknown scoring outcome {self.outcome!r}")
        return self


class ScoreRecord(DomainModel):
    """Reproducibility metadata plus the parsed result of one scoring call."""

    record_version: str = "1.0"
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    scorer_id: str = Field(min_length=1)
    model: str = ""
    provider: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    config_hash: str = ""
    ground_truth_version: str = ""
    reference_hash: str = ""
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
    outcome: ScoreOutcome | None = None
    raw_response_text: str | None = None
    error: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.parsing_status == "ok" and self.outcome is not None


class ScoreComparison(DomainModel):
    """Agreement classification of scorer A vs scorer B."""

    task_id: str = Field(min_length=1)
    classification: str = SCORE_DISAGREEMENT
    same_outcome: bool = False
    same_critical_errors: bool = False
    notes: list[str] = Field(default_factory=list)


class ScoreAdjudication(DomainModel):
    """One scorer-adjudication call and its structured outcome."""

    task_id: str = Field(min_length=1)
    outcome: str = OUTCOME_UNSCORABLE
    rationale: str = ""
    critical_errors: list[CriticalError] = Field(default_factory=list)
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
    def validate_outcome(self) -> "ScoreAdjudication":
        if self.outcome not in OUTCOME_VALUES:
            raise ValueError(f"unknown adjudicated scoring outcome {self.outcome!r}")
        return self


class FinalScore(DomainModel):
    """The adopted final score of one task. Never modifies the gold reference."""

    task_id: str = Field(min_length=1)
    outcome: str = OUTCOME_UNSCORABLE
    critical_errors: list[CriticalError] = Field(default_factory=list)
    comparison: ScoreComparison | None = None
    adjudication: ScoreAdjudication | None = None
    source: str = "scorer_agreement"
    rationale: str = ""


__all__ = [
    "CRITICAL_ERROR_KINDS",
    "OUTCOME_FULLY_CORRECT",
    "OUTCOME_INCORRECT",
    "OUTCOME_PARTIALLY_CORRECT",
    "OUTCOME_UNSCORABLE",
    "OUTCOME_VALUES",
    "SCORER_A",
    "SCORER_B",
    "SCORE_AGREEMENT",
    "SCORE_DISAGREEMENT",
    "CriticalError",
    "FinalScore",
    "ScoreAdjudication",
    "ScoreComparison",
    "ScoreOutcome",
    "ScoreRecord",
    "ScoringError",
]
