"""Public task schema and private evaluator reference schema.

The separation is structural, not a convention:

* :class:`PublicDecisionTask` is the only object the agent-facing loader can
  return. It has no field that could carry a call identifier, a cutoff index, a
  future turn, a historical next response or an evaluator label.
* :class:`PrivateTaskReference` carries everything the evaluator needs and is
  stored in a different artifact. The task loader never reads it.

The private reference was **extended** in Milestone 1.1 with the business
annotation fields (answerability, primary and alternative expected decisions,
branch conditions, step requirements, evidence provenance, adjudication state).
The extension is additive: a reference written by Milestone 1 still parses, and a
new reference defaults to ``unknown`` / ``pending`` so an unannotated task can
never look scored.

Milestone 1.2 extends it again, additively, with the LLM-generated Gold Label
fields (``ground_truth_type/source/status``, ``external_policy_dependency``,
``policy_verified``, ``annotation_confidence``, ``required_information``). A Gold
Label is scorable only through the dedicated
``accepted_as_benchmark_gold`` gate; the historical agent response and plain
``llm_proposed`` pre-annotations remain non-scorable.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, field_validator

from cm_outbound_bench.decisions.annotation import (
    ADJUDICATION_PENDING,
    ANNOTATION_ACCEPTED_AS_GOLD,
    ANSWERABILITY_REQUIRES_EXTERNAL_POLICY,
    ANSWERABILITY_UNKNOWN,
    LLM_GOLD_AUTHORITIES,
    LLM_GOLD_SCOREABLE_ANSWERABILITY,
    POLICY_DEPENDENT_SCOREABLE_CATEGORIES,
    SCOREABLE_ANSWERABILITY,
    TIER_EXPERT_ADJUDICATED,
    TIER_NONE,
    AnnotationProvenance,
    BranchCondition,
    BusinessRuleCitation,
    DecisionReference,
    tier_supports_expert,
    tier_supports_policy,
)
from cm_outbound_bench.domain.data_model import DomainModel

BENCHMARK_VERSION = "0.2.0"
TASK_SCHEMA_VERSION = "1.0"
REFERENCE_SCHEMA_VERSION = "2.0"
OUTPUT_SCHEMA_VERSION = "1.0"

TASK_TYPE_DECISION_POINT = "decision_point"

#: Fields an agent-visible task may contain. Used by the loader as a hard guard.
PUBLIC_TASK_FIELDS: frozenset[str] = frozenset(
    {
        "task_id",
        "task_type",
        "business_domain",
        "visible_context",
        "agent_task",
        "schema_version",
    }
)

#: The instruction is deliberately action-oriented and contains no answer hint.
TASK_INSTRUCTION = (
    "请阅读以上外呼通话记录，判断客服代表下一步应当执行的动作以及应当对客户说的话，"
    "并严格按照要求输出 JSON。"
)


def make_task_id(call_id: str, cutoff_seq: int) -> str:
    """Return an opaque task id derived from the call and the cutoff.

    The id is a truncated hash: it cannot be used to recover the call id, and the
    visible task never carries either value.
    """
    payload = f"{call_id}|{cutoff_seq}|{TASK_SCHEMA_VERSION}".encode("utf-8")
    return "TASK_" + hashlib.sha256(payload).hexdigest()[:20]


class VisibleTurn(DomainModel):
    """One conversation turn as shown to the evaluated agent."""

    role: Literal["customer", "historical_agent"]
    content: str = Field(min_length=1)


class AgentTaskSpec(DomainModel):
    """The task statement shown to the evaluated agent."""

    instruction: str = TASK_INSTRUCTION


class PublicDecisionTask(DomainModel):
    """Agent-visible decision-point task. Contains no private field."""

    task_id: str = Field(min_length=1)
    task_type: Literal["decision_point"] = TASK_TYPE_DECISION_POINT
    business_domain: str = "unknown"
    visible_context: list[VisibleTurn] = Field(min_length=1)
    agent_task: AgentTaskSpec = Field(default_factory=AgentTaskSpec)
    schema_version: str = TASK_SCHEMA_VERSION

    @field_validator("visible_context")
    @classmethod
    def require_customer_turn(cls, value: list[VisibleTurn]) -> list[VisibleTurn]:
        if not any(turn.role == "customer" for turn in value):
            raise ValueError("visible context must contain a customer turn")
        return value

    def public_payload(self) -> dict[str, object]:
        """Return exactly the agent-visible fields, as plain JSON data."""
        payload = self.model_dump(mode="json")
        return {key: value for key, value in payload.items() if key in PUBLIC_TASK_FIELDS}


class ExpectedDecision(DomainModel):
    """A verified expected decision, when one exists.

    No instance of this model exists in the current milestone: there is no
    verified business rule set or human annotation for this corpus. The schema is
    defined so a verified annotation can be attached later without changing the
    evaluator interface.
    """

    decision_id: str = Field(min_length=1)
    branch: str | None = None
    required_steps: list[str] = Field(default_factory=list)
    policy_ids: list[str] = Field(default_factory=list)
    response_keywords: list[str] = Field(default_factory=list)


class PrivateTaskReference(DomainModel):
    """Evaluator-only task reference. Never returned by the task loader.

    ``call_id`` keeps its historical field name for artifact compatibility. It is
    an **inferred conversation unit identifier**; :attr:`conversation_id` exposes
    the same value under the research term and
    :data:`cm_outbound_bench.transcripts.models.CONVERSATION_ID_SEMANTICS`
    documents why it must not be called a verified telephone call id.

    Annotation state is additive. The default is an unannotated, unscored task, so
    a reference can never be scored by accident. See
    :mod:`cm_outbound_bench.decisions.annotation` for the two evaluation tiers.
    """

    task_id: str = Field(min_length=1)
    reference_schema_version: str = REFERENCE_SCHEMA_VERSION
    call_id: str
    split: str
    cutoff_record_index: int = Field(ge=0)
    cutoff_seq: int = Field(ge=0)
    cutoff_segment_id: str
    cutoff_start_ms: int = Field(ge=0)
    cutoff_visible_turn_count: int = Field(ge=1)
    hidden_speech_turn_count: int = Field(ge=0)
    historical_next_action_reference: str | None = None
    historical_next_action_segment_id: str | None = None
    historical_next_action_authority: Literal["observed_reference_only"] = (
        "observed_reference_only"
    )
    scoring_status: Literal["scored", "candidate", "unscored", "invalid"] = "unscored"
    annotation_status: Literal[
        "pending",
        "in_review",
        "reviewed",
        "adjudicated",
        "rejected",
        "accepted_as_benchmark_gold",
    ] = "pending"
    verified_business_rules: list[str] = Field(default_factory=list)
    expected_decisions: list[ExpectedDecision] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    data_quality_status: str = "ok"
    builder_version: str = ""
    constructed_at: str = ""

    # -- Milestone 1.1 annotation extension -------------------------------
    task_answerability: Literal[
        "sufficient_information",
        "more_information_required",
        "ambiguous",
        "unscorable",
        "unknown",
        "sufficient_for_action",
        "clarification_required",
        "insufficient_context",
        "requires_external_business_policy",
        "not_a_valid_business_decision_point",
        "malformed_context",
    ] = ANSWERABILITY_UNKNOWN
    primary_expected_decision: DecisionReference | None = None
    acceptable_alternative_decisions: list[DecisionReference] = Field(
        default_factory=list
    )
    branch_conditions: list[BranchCondition] = Field(default_factory=list)
    expected_branch: str | None = None
    required_steps: list[str] = Field(default_factory=list)
    optional_steps: list[str] = Field(default_factory=list)
    prohibited_steps: list[str] = Field(default_factory=list)
    expected_clarification: list[str] = Field(default_factory=list)
    rule_citations: list[BusinessRuleCitation] = Field(default_factory=list)
    annotation_provenance: AnnotationProvenance | None = None
    reviewer_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    annotator_ids: list[str] = Field(default_factory=list)
    adjudication_status: str = ADJUDICATION_PENDING
    annotation_guideline_version: str | None = None
    verification_tier: Literal[
        "none",
        "expert_adjudicated",
        "policy_verified",
        "expert_adjudicated_and_policy_verified",
    ] = TIER_NONE

    # -- Milestone 1.2 LLM-gold ground truth extension --------------------
    # The final DeepSeek V4 Pro-generated and adjudicated annotation is the official
    # Benchmark Gold Label (provenance recorded, never presented as human review and
    # never presented as verified China Mobile policy unless policy_verified is
    # true).
    ground_truth_type: Literal["llm_generated_gold"] | None = None
    ground_truth_source: str | None = None
    ground_truth_status: Literal["frozen"] | None = None
    ground_truth_version: str | None = None
    annotation_method: str = ""
    external_policy_dependency: bool | None = None
    policy_verified: bool | None = None
    annotation_confidence: Literal["high", "medium", "low"] | None = None
    required_information: list[str] = Field(default_factory=list)
    unscorable_reason: str = ""
    customer_intent: str = ""
    conversation_state: str = ""
    unsupported_claims_to_avoid: list[str] = Field(default_factory=list)
    assumptions_not_allowed: list[str] = Field(default_factory=list)

    @property
    def conversation_id(self) -> str:
        """The research-facing identifier of the inferred conversation unit."""
        return self.call_id

    @property
    def transcript_unit_id(self) -> str:
        """The corpus identifier of the delivered transcript unit."""
        return self.call_id

    @property
    def scoring_references(self) -> list[DecisionReference]:
        """Every decision the evaluator may accept as a correct answer.

        The primary decision comes first, then each recorded alternative. A task
        with only a clarification expectation and no decision is still scoreable:
        the correct answer is to ask for the missing information.
        """
        references: list[DecisionReference] = []
        if self.primary_expected_decision is not None:
            references.append(self.primary_expected_decision)
        references.extend(self.acceptable_alternative_decisions)
        return references

    @property
    def effective_verification_tier(self) -> str:
        """Return the verification tier, applying the legacy migration rule.

        A reference written before the two-tier schema has legacy
        ``expected_decisions`` and no explicit tier. Those decisions were described
        as human-verified phrases, so they are treated as
        ``expert_adjudicated`` — never as ``policy_verified``, because no policy
        citation exists for them.
        """
        if self.verification_tier != TIER_NONE:
            return self.verification_tier
        if self.expected_decisions:
            return TIER_EXPERT_ADJUDICATED
        return TIER_NONE

    @property
    def has_expert_tier(self) -> bool:
        return tier_supports_expert(self.effective_verification_tier)

    @property
    def has_policy_tier(self) -> bool:
        return tier_supports_policy(self.effective_verification_tier)

    @property
    def answerability_is_scoreable(self) -> bool:
        # A legacy reference carries no answerability annotation; it is treated as
        # sufficient because the legacy contract required expected decisions.
        if self.task_answerability == ANSWERABILITY_UNKNOWN:
            return bool(self.expected_decisions) or bool(self.scoring_references)
        if self.task_answerability in SCOREABLE_ANSWERABILITY:
            return True
        if self.task_answerability in LLM_GOLD_SCOREABLE_ANSWERABILITY:
            return True
        # A task that depends on unavailable external business policy is scoreable
        # only when the approved decision is a clarification, verification or
        # deferral — never a fabricated answer.
        if self.task_answerability == ANSWERABILITY_REQUIRES_EXTERNAL_POLICY:
            primary = self.primary_expected_decision
            return (
                primary is not None
                and primary.decision_category in POLICY_DEPENDENT_SCOREABLE_CATEGORIES
            )
        return False

    @property
    def is_llm_gold(self) -> bool:
        """True when this reference is an official LLM-generated Gold Label."""
        return (
            self.ground_truth_type == "llm_generated_gold"
            and self.annotation_provenance is not None
            and self.annotation_provenance.authority in LLM_GOLD_AUTHORITIES
        )

    @property
    def is_scored(self) -> bool:
        """True only when the reference can legitimately score a decision.

        All of the following must hold:

        * ``scoring_status == "scored"``;
        * the annotation gate is satisfied:
          * Milestone 1.1: ``annotation_status`` is ``reviewed`` or ``adjudicated``
            with human (or non-model-only) provenance, or
          * Milestone 1.2: the reference is an official LLM-generated Gold Label
            with ``annotation_status == accepted_as_benchmark_gold``;
        * the task is answerable enough to score;
        * at least one usable decision or an explicit clarification expectation
          exists;
        * a plain ``llm_proposed`` pre-annotation can never certify its own ground
          truth.
        """
        if self.scoring_status != "scored":
            return False
        if self.is_llm_gold:
            gate_ok = self.annotation_status == ANNOTATION_ACCEPTED_AS_GOLD
        else:
            gate_ok = self.annotation_status in {"reviewed", "adjudicated"}
        if not gate_ok:
            return False
        if self.annotation_provenance is not None and self.annotation_provenance.is_llm_only:
            return False
        if not self.answerability_is_scoreable:
            return False
        return (
            bool(self.scoring_references)
            or bool(self.expected_decisions)
            or bool(self.expected_clarification)
        )


class AgentDecisionOutput(DomainModel):
    """The validated structured output the evaluator inspects."""

    customer_response: str = Field(min_length=1)
    next_action: str = Field(min_length=1)
    required_information: list[str] = Field(default_factory=list)
    tool_intent: None = None
    uncertainty: str | None = None
    evidence_summary: str = Field(min_length=1)


class AgentRequest(DomainModel):
    """Everything one agent session is allowed to see."""

    task_id: str
    system_prompt: str
    user_prompt: str


class AgentInvocation(DomainModel):
    """One agent attempt, normalized independently of the provider."""

    status: Literal["success", "invalid_output", "provider_error", "timeout"]
    output: AgentDecisionOutput | None = None
    raw_text: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: int = Field(ge=0, default=0)
    token_usage: dict[str, int] = Field(default_factory=dict)
    attempts: int = Field(ge=0, default=1)
    http_status: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success" and self.output is not None


__all__ = [
    "BENCHMARK_VERSION",
    "OUTPUT_SCHEMA_VERSION",
    "PUBLIC_TASK_FIELDS",
    "REFERENCE_SCHEMA_VERSION",
    "TASK_INSTRUCTION",
    "TASK_SCHEMA_VERSION",
    "TASK_TYPE_DECISION_POINT",
    "AgentDecisionOutput",
    "AgentInvocation",
    "AgentRequest",
    "AgentTaskSpec",
    "AnnotationProvenance",
    "BranchCondition",
    "BusinessRuleCitation",
    "DecisionReference",
    "ExpectedDecision",
    "PrivateTaskReference",
    "PublicDecisionTask",
    "VisibleTurn",
    "make_task_id",
]
