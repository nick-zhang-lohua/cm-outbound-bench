"""Business decision annotation schema for the real-transcript benchmark.

This module defines the *annotation* vocabulary that extends the existing private
evaluator reference. It is not a second data system: the final annotation state is
stored on :class:`cm_outbound_bench.decisions.schema.PrivateTaskReference`, and
this module supplies the models it embeds.

Two evaluation claims are kept apart on purpose:

``expert_adjudicated``
    An independent business-domain reviewer judged the decision reasonable and
    appropriate from the visible conversation prefix and approved business
    knowledge, with acceptable alternatives recorded. This is a judgement about
    appropriateness. It is **not** a formal policy-compliance claim.

``policy_verified``
    The decision is supported by an official policy, a verified operation record
    or authoritative business knowledge, with an explicit rule reference. Only
    this tier supports a policy-compliance claim.

A reference may carry both, in which case the two metrics are still reported
separately. There is no combined accuracy number, because the two claims have
different evidence requirements.

Nothing here can create verified ground truth from model output: an annotation
whose provenance authority is ``llm_proposed`` is never scorable, and
:func:`cm_outbound_bench.decisions.schema.PrivateTaskReference.is_scored`
enforces that.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from cm_outbound_bench.domain.data_model import DomainModel

#: Version of the annotation guideline this schema implements.
ANNOTATION_GUIDELINE_VERSION = "annotation-guidelines-v1"

#: Version of the annotation schema itself.
ANNOTATION_SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Task answerability
# ---------------------------------------------------------------------------

ANSWERABILITY_SUFFICIENT = "sufficient_information"
ANSWERABILITY_MORE_INFORMATION = "more_information_required"
ANSWERABILITY_AMBIGUOUS = "ambiguous"
ANSWERABILITY_UNSCORABLE = "unscorable"
ANSWERABILITY_UNKNOWN = "unknown"

TASK_ANSWERABILITY_VALUES: tuple[str, ...] = (
    ANSWERABILITY_SUFFICIENT,
    ANSWERABILITY_MORE_INFORMATION,
    ANSWERABILITY_AMBIGUOUS,
    ANSWERABILITY_UNSCORABLE,
    ANSWERABILITY_UNKNOWN,
)

#: Answerability values that may still be scored. Ambiguity and unscorability are
#: excluded from every business denominator.
SCOREABLE_ANSWERABILITY: frozenset[str] = frozenset(
    {ANSWERABILITY_SUFFICIENT, ANSWERABILITY_MORE_INFORMATION}
)

# ---------------------------------------------------------------------------
# Milestone 1.2 LLM-gold answerability vocabulary
# ---------------------------------------------------------------------------
#
# The DeepSeek V4 Pro annotation passes use this finer vocabulary. The old values
# above remain valid for Milestone 1.1 artifacts; the two vocabularies are never
# mixed in one final metrics table without an explicit version separation.

ANSWERABILITY_SUFFICIENT_FOR_ACTION = "sufficient_for_action"
ANSWERABILITY_CLARIFICATION_REQUIRED = "clarification_required"
ANSWERABILITY_INSUFFICIENT_CONTEXT = "insufficient_context"
ANSWERABILITY_REQUIRES_EXTERNAL_POLICY = "requires_external_business_policy"
ANSWERABILITY_NOT_A_DECISION_POINT = "not_a_valid_business_decision_point"
ANSWERABILITY_MALFORMED_CONTEXT = "malformed_context"

LLM_GOLD_ANSWERABILITY_VALUES: tuple[str, ...] = (
    ANSWERABILITY_SUFFICIENT_FOR_ACTION,
    ANSWERABILITY_CLARIFICATION_REQUIRED,
    ANSWERABILITY_AMBIGUOUS,
    ANSWERABILITY_INSUFFICIENT_CONTEXT,
    ANSWERABILITY_REQUIRES_EXTERNAL_POLICY,
    ANSWERABILITY_NOT_A_DECISION_POINT,
    ANSWERABILITY_MALFORMED_CONTEXT,
)

#: All answerability values a PrivateTaskReference may carry after the additive
#: extension. Old and new vocabularies are both valid; scoreability is decided by
#: :data:`SCOREABLE_ANSWERABILITY` plus the LLM-gold scoring rules.
ALL_ANSWERABILITY_VALUES: tuple[str, ...] = tuple(
    dict.fromkeys((*TASK_ANSWERABILITY_VALUES, *LLM_GOLD_ANSWERABILITY_VALUES))
)

#: LLM-gold answerability values that can support a scored reference.
LLM_GOLD_SCOREABLE_ANSWERABILITY: frozenset[str] = frozenset(
    {ANSWERABILITY_SUFFICIENT_FOR_ACTION, ANSWERABILITY_CLARIFICATION_REQUIRED}
)

#: An external-policy dependency can still be scored when the approved next move is
#: a clarification, a verification or a deferral — never a fabricated answer.
POLICY_DEPENDENT_SCOREABLE_CATEGORIES: frozenset[str] = frozenset(
    {"clarify", "verify_information", "defer_pending_information"}
)

# ---------------------------------------------------------------------------
# Annotation provenance authority
# ---------------------------------------------------------------------------

AUTHORITY_LLM_PROPOSED = "llm_proposed"
AUTHORITY_HUMAN_PRIMARY = "human_primary_annotation"
AUTHORITY_HUMAN_SECONDARY = "human_secondary_review"
AUTHORITY_HUMAN_ADJUDICATED = "human_adjudicated"

#: Milestone 1.2: the final DeepSeek V4 Pro-generated and adjudicated annotation is
#: the official Benchmark Gold Label. It is deliberately distinct from
#: ``llm_proposed``, which remains a non-scorable pre-annotation.
AUTHORITY_LLM_GENERATED_GOLD = "llm_generated_gold"

AUTHORITY_VALUES: tuple[str, ...] = (
    AUTHORITY_LLM_PROPOSED,
    AUTHORITY_HUMAN_PRIMARY,
    AUTHORITY_HUMAN_SECONDARY,
    AUTHORITY_HUMAN_ADJUDICATED,
    AUTHORITY_LLM_GENERATED_GOLD,
)

#: Authorities that are human review.
HUMAN_AUTHORITIES: frozenset[str] = frozenset(
    {AUTHORITY_HUMAN_PRIMARY, AUTHORITY_HUMAN_SECONDARY, AUTHORITY_HUMAN_ADJUDICATED}
)

#: Authorities that certify the official LLM-generated Benchmark Gold Labels.
LLM_GOLD_AUTHORITIES: frozenset[str] = frozenset({AUTHORITY_LLM_GENERATED_GOLD})

# ---------------------------------------------------------------------------
# Evidence provenance
# ---------------------------------------------------------------------------

EVIDENCE_SOURCE_OFFICIAL_POLICY = "official_policy"
EVIDENCE_SOURCE_VERIFIED_BUSINESS_KNOWLEDGE = "verified_business_knowledge"
EVIDENCE_SOURCE_EXPERT_CONSENSUS = "expert_consensus"
EVIDENCE_SOURCE_HISTORICAL_OBSERVATION = "historical_observation_only"

EVIDENCE_SOURCE_VALUES: tuple[str, ...] = (
    EVIDENCE_SOURCE_OFFICIAL_POLICY,
    EVIDENCE_SOURCE_VERIFIED_BUSINESS_KNOWLEDGE,
    EVIDENCE_SOURCE_EXPERT_CONSENSUS,
    EVIDENCE_SOURCE_HISTORICAL_OBSERVATION,
)

#: Evidence sources that can support a ``policy_verified`` claim.
POLICY_TIER_EVIDENCE: frozenset[str] = frozenset(
    {
        EVIDENCE_SOURCE_OFFICIAL_POLICY,
        EVIDENCE_SOURCE_VERIFIED_BUSINESS_KNOWLEDGE,
    }
)

# ---------------------------------------------------------------------------
# Verification tiers
# ---------------------------------------------------------------------------

TIER_NONE = "none"
TIER_EXPERT_ADJUDICATED = "expert_adjudicated"
TIER_POLICY_VERIFIED = "policy_verified"
TIER_BOTH = "expert_adjudicated_and_policy_verified"

VERIFICATION_TIER_VALUES: tuple[str, ...] = (
    TIER_NONE,
    TIER_EXPERT_ADJUDICATED,
    TIER_POLICY_VERIFIED,
    TIER_BOTH,
)

# ---------------------------------------------------------------------------
# Annotation and adjudication lifecycle
# ---------------------------------------------------------------------------

ANNOTATION_PENDING = "pending"
ANNOTATION_IN_REVIEW = "in_review"
ANNOTATION_REVIEWED = "reviewed"
ANNOTATION_ADJUDICATED = "adjudicated"
ANNOTATION_REJECTED = "rejected"

#: Milestone 1.2: a final DeepSeek V4 Pro annotation/adjudication adopted as the
#: official Benchmark Gold Label. No human review is required by project decision.
ANNOTATION_ACCEPTED_AS_GOLD = "accepted_as_benchmark_gold"

ANNOTATION_STATUS_VALUES: tuple[str, ...] = (
    ANNOTATION_PENDING,
    ANNOTATION_IN_REVIEW,
    ANNOTATION_REVIEWED,
    ANNOTATION_ADJUDICATED,
    ANNOTATION_REJECTED,
    ANNOTATION_ACCEPTED_AS_GOLD,
)

ADJUDICATION_PENDING = "pending"
ADJUDICATION_IN_REVIEW = "in_review"
ADJUDICATION_RESOLVED = "adjudicated"
ADJUDICATION_REJECTED = "rejected"
ADJUDICATION_NOT_REQUIRED = "not_required"

ADJUDICATION_STATUS_VALUES: tuple[str, ...] = (
    ADJUDICATION_PENDING,
    ADJUDICATION_IN_REVIEW,
    ADJUDICATION_RESOLVED,
    ADJUDICATION_REJECTED,
    ADJUDICATION_NOT_REQUIRED,
)

#: Disagreement categories used by adjudication statistics.
DISAGREEMENT_CATEGORIES: tuple[str, ...] = (
    "none",
    "decision_family",
    "branch_condition",
    "required_step_missing",
    "required_step_extra",
    "answerability",
    "prohibited_action",
    "clarification_expected",
    "evidence_insufficient",
    "guideline_ambiguity",
)

# ---------------------------------------------------------------------------
# Milestone 1.2 decision category taxonomy
# ---------------------------------------------------------------------------
#
# Generic conversational decision categories, not authorized China Mobile business
# actions. A task is never forced into a category that does not fit.

DECISION_CATEGORY_ACKNOWLEDGE = "acknowledge"
DECISION_CATEGORY_CLARIFY = "clarify"
DECISION_CATEGORY_CONFIRM = "confirm"
DECISION_CATEGORY_ANSWER = "answer"
DECISION_CATEGORY_EXPLAIN = "explain"
DECISION_CATEGORY_RECOMMEND = "recommend"
DECISION_CATEGORY_VERIFY_INFORMATION = "verify_information"
DECISION_CATEGORY_HANDLE_OBJECTION = "handle_objection"
DECISION_CATEGORY_ESCALATE = "escalate"
DECISION_CATEGORY_DEFER = "defer_pending_information"
DECISION_CATEGORY_CLOSE = "close_conversation"
DECISION_CATEGORY_OTHER = "other"

DECISION_CATEGORY_VALUES: tuple[str, ...] = (
    DECISION_CATEGORY_ACKNOWLEDGE,
    DECISION_CATEGORY_CLARIFY,
    DECISION_CATEGORY_CONFIRM,
    DECISION_CATEGORY_ANSWER,
    DECISION_CATEGORY_EXPLAIN,
    DECISION_CATEGORY_RECOMMEND,
    DECISION_CATEGORY_VERIFY_INFORMATION,
    DECISION_CATEGORY_HANDLE_OBJECTION,
    DECISION_CATEGORY_ESCALATE,
    DECISION_CATEGORY_DEFER,
    DECISION_CATEGORY_CLOSE,
    DECISION_CATEGORY_OTHER,
)

#: Qualitative annotation confidence. Not a calibrated probability.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

CONFIDENCE_VALUES: tuple[str, ...] = (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_LOW,
)

#: Provenance values mandated by the Milestone 1.2 research decision.
GROUND_TRUTH_TYPE_LLM_GENERATED_GOLD = "llm_generated_gold"
GROUND_TRUTH_SOURCE_DEEPSEEK_V4_PRO = "deepseek_v4_pro"
GROUND_TRUTH_STATUS_FROZEN = "frozen"
ANNOTATION_METHOD_LLM_BASED = "llm_based"
GROUND_TRUTH_VERSION_V1 = "cm-outbound-llm-gold-v1"


class AnnotationError(RuntimeError):
    """Raised when an annotation cannot be accepted."""


class BusinessSourceReference(DomainModel):
    """One business-reference source discovered in the workspace.

    ``authority_level`` is deliberately blunt. A synthetic document, a recovered
    conversation record and an official policy are not interchangeable, and only
    an ``authoritative`` source may support a ``policy_verified`` claim.
    """

    source_id: str = Field(min_length=1)
    source_type: str
    source_path: str
    version: str = ""
    authority_level: Literal[
        "authoritative", "unverified", "synthetic", "recovered_partial"
    ]
    applicable_topics: list[str] = Field(default_factory=list)
    content_hash: str = ""
    notes: str = ""

    @property
    def supports_policy_verification(self) -> bool:
        return self.authority_level == "authoritative"


class BusinessRuleCitation(DomainModel):
    """A rule statement taken from one business source."""

    rule_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    evidence_source: str = EVIDENCE_SOURCE_OFFICIAL_POLICY
    applicable_topics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_source(self) -> "BusinessRuleCitation":
        if self.evidence_source not in EVIDENCE_SOURCE_VALUES:
            raise ValueError(
                f"unknown evidence source {self.evidence_source!r}; expected one of "
                f"{EVIDENCE_SOURCE_VALUES}"
            )
        return self

    @property
    def supports_policy_tier(self) -> bool:
        return self.evidence_source in POLICY_TIER_EVIDENCE


class BranchCondition(DomainModel):
    """One conditional path a decision can take."""

    condition_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_branch: str = Field(min_length=1)
    required_information: list[str] = Field(default_factory=list)


class DecisionReference(DomainModel):
    """One acceptable expected decision.

    Alternatives are first-class, not a fallback: a decision task frequently has
    more than one professionally appropriate answer, and forcing a single "correct"
    string would measure wording recall instead of decision quality.

    ``match_keywords`` is the auditable matching anchor. An annotator writes the
    minimal phrases that identify this decision in an agent's answer (for example
    ``确认机主`` or ``说明费用``). The evaluator accepts the decision when **any**
    keyword appears in the agent's stated action, spoken response or requested
    information. When no keyword is supplied the ``label`` is used as the single
    keyword, which is stricter; recording keywords is therefore recommended.
    """

    decision_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    decision_family: str = Field(
        default="unspecified",
        description=(
            "Coarse action family used for agreement statistics. Milestone 1.2 "
            "LLM-gold annotations store the DECISION_CATEGORY_VALUES taxonomy here "
            "instead of the human guideline families."
        ),
    )
    decision_category: str = Field(
        default="",
        description=(
            "Milestone 1.2 decision category (e.g. clarify, verify_information). "
            "Empty for legacy human annotations."
        ),
    )
    rationale: str = Field(
        default="",
        description="Why this decision is appropriate given the visible prefix.",
    )
    description: str = ""
    match_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Minimal phrases that identify this decision in an agent answer. Any "
            "one match accepts the decision. Never a full-sentence requirement."
        ),
    )
    expected_customer_response_intent: str = ""
    branch_conditions: list[BranchCondition] = Field(default_factory=list)
    required_steps: list[str] = Field(default_factory=list)
    optional_steps: list[str] = Field(default_factory=list)
    prohibited_steps: list[str] = Field(default_factory=list)
    rule_citations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @property
    def effective_keywords(self) -> list[str]:
        """Keywords the evaluator matches, falling back to the label."""
        keywords = [item for item in self.match_keywords if item.strip()]
        return keywords or [self.label]


class AnnotationProvenance(DomainModel):
    """Where an annotation came from and how strong its authority is."""

    authority: str
    method: str = ""
    annotator_id: str = ""
    reviewer_id: str = ""
    model_name: str = ""
    model_version: str = ""
    prompt_version: str = ""
    guideline_version: str = ANNOTATION_GUIDELINE_VERSION
    created_at: str = ""
    source_refs: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_authority(self) -> "AnnotationProvenance":
        if self.authority not in AUTHORITY_VALUES:
            raise ValueError(
                f"unknown annotation authority {self.authority!r}; expected one of "
                f"{AUTHORITY_VALUES}"
            )
        if self.authority in {AUTHORITY_LLM_PROPOSED, AUTHORITY_LLM_GENERATED_GOLD} and (
            not (self.model_name or self.model_version)
        ):
            raise ValueError(
                "an LLM-produced annotation must record the model identity so it "
                "can never be mistaken for human review"
            )
        return self

    @property
    def is_llm_only(self) -> bool:
        """True only for a pre-annotation proposal, which can never be scored."""
        return self.authority == AUTHORITY_LLM_PROPOSED

    @property
    def is_llm_gold(self) -> bool:
        """True for the official LLM-generated Benchmark Gold Label authority."""
        return self.authority == AUTHORITY_LLM_GENERATED_GOLD

    @property
    def is_human(self) -> bool:
        return self.authority in HUMAN_AUTHORITIES


class AnnotationSubmission(DomainModel):
    """One reviewer's independent annotation of one task.

    Submissions are the input to the process; a
    :class:`cm_outbound_bench.decisions.schema.PrivateTaskReference` is the
    resulting state. Keeping them separate is what makes independent double
    annotation and pre-adjudication agreement measurable.
    """

    task_id: str = Field(min_length=1)
    annotator_id: str = Field(min_length=1)
    guideline_version: str = ANNOTATION_GUIDELINE_VERSION
    annotation_status: str = ANNOTATION_PENDING
    task_answerability: str = ANSWERABILITY_UNKNOWN
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
    verified_business_rules: list[str] = Field(default_factory=list)
    rule_citations: list[BusinessRuleCitation] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: AnnotationProvenance | None = None
    reviewer_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    submitted_at: str = ""
    notes: str = ""

    @model_validator(mode="after")
    def validate_submission(self) -> "AnnotationSubmission":
        if self.task_answerability not in TASK_ANSWERABILITY_VALUES:
            raise ValueError(
                f"unknown task_answerability {self.task_answerability!r}"
            )
        if self.prohibited_steps and not (
            self.verified_business_rules or self.rule_citations
        ):
            raise ValueError(
                "prohibited_steps require at least one verified business rule: a "
                "prohibited action can only be asserted from an actual rule"
            )
        if (
            self.task_answerability == ANSWERABILITY_MORE_INFORMATION
            and not self.expected_clarification
        ):
            raise ValueError(
                "task_answerability=more_information_required must name the "
                "information that has to be requested in expected_clarification"
            )
        return self

    @property
    def is_human(self) -> bool:
        return self.provenance is not None and self.provenance.is_human

    def decision_identities(self) -> set[str]:
        """Return the full set of decisions this submission accepts.

        Set semantics are what agreement statistics compare: the primary decision
        and every acceptable alternative are equally acceptable answers.
        """
        identities: set[str] = set()
        if self.primary_expected_decision is not None:
            identities.add(self.primary_expected_decision.label)
        for alternative in self.acceptable_alternative_decisions:
            identities.add(alternative.label)
        return identities


class AdjudicationRecord(DomainModel):
    """The final, adjudicated annotation state of one task."""

    task_id: str = Field(min_length=1)
    status: str = ADJUDICATION_PENDING
    adjudicator_id: str = ""
    annotator_ids: list[str] = Field(default_factory=list)
    guideline_version: str = ANNOTATION_GUIDELINE_VERSION
    disagreement_category: str = "none"
    pre_adjudication_decision_agreement: bool | None = None
    final_annotation: AnnotationSubmission | None = None
    resolution_note: str = ""
    adjudicated_at: str = ""
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_adjudication(self) -> "AdjudicationRecord":
        if self.status not in ADJUDICATION_STATUS_VALUES:
            raise ValueError(f"unknown adjudication status {self.status!r}")
        if self.status == ADJUDICATION_RESOLVED and self.final_annotation is None:
            raise ValueError(
                "a resolved adjudication must carry the final adjudicated "
                "annotation"
            )
        return self


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

#: Reasons an annotation set is not usable for scoring. Used by the import
#: validator and by the tests so the rule set has one definition.
def validate_annotation_consistency(
    submission: AnnotationSubmission,
) -> list[str]:
    """Return structural problems with one submission. Empty list means valid."""
    issues: list[str] = []
    if submission.task_answerability in {
        ANSWERABILITY_AMBIGUOUS,
        ANSWERABILITY_UNSCORABLE,
    } and submission.primary_expected_decision is not None:
        issues.append(
            "ambiguous_or_unscorable_task_must_not_define_a_primary_decision"
        )
    if (
        submission.task_answerability == ANSWERABILITY_SUFFICIENT
        and submission.expected_clarification
    ):
        issues.append(
            "sufficient_information_task_must_not_expect_a_clarification_request"
        )
    identities = submission.decision_identities()
    if submission.primary_expected_decision is not None and len(identities) == 0:
        issues.append("primary_decision_has_no_label")
    if submission.expected_branch is not None and not any(
        condition.expected_branch == submission.expected_branch
        for condition in submission.branch_conditions
    ):
        issues.append("expected_branch_not_defined_by_any_branch_condition")
    for citation in submission.rule_citations:
        if not citation.supports_policy_tier:
            issues.append(
                f"rule_citation_{citation.rule_id}_is_not_policy_grade_evidence"
            )
    if submission.reviewer_confidence is None:
        issues.append("reviewer_confidence_missing")
    if not submission.evidence_refs:
        issues.append("evidence_refs_missing")
    if submission.provenance is None:
        issues.append("annotation_provenance_missing")
    return issues


def tier_for(
    *,
    expert_adjudicated: bool,
    policy_verified: bool,
) -> str:
    """Return the verification tier implied by two independent claims."""
    if expert_adjudicated and policy_verified:
        return TIER_BOTH
    if policy_verified:
        return TIER_POLICY_VERIFIED
    if expert_adjudicated:
        return TIER_EXPERT_ADJUDICATED
    return TIER_NONE


def tier_supports_expert(tier: str) -> bool:
    return tier in {TIER_EXPERT_ADJUDICATED, TIER_BOTH}


def tier_supports_policy(tier: str) -> bool:
    return tier in {TIER_POLICY_VERIFIED, TIER_BOTH}


#: Human-readable protocol summary used by the CLI and the reports so the
#: ordering rules are stated exactly once.
ANNOTATION_ORDER = (
    "1. The annotator receives only the agent-visible task prefix and the "
    "approved business reference material.",
    "2. The annotator records the expected decision independently, with evidence "
    "references and a confidence value.",
    "3. A second annotator annotates the same task independently and without "
    "seeing the first annotation.",
    "4. Disagreements go to a designated adjudicator who is not one of the two "
    "annotators.",
    "5. Only a human-adjudicated record with a guideline version can become a "
    "scored reference.",
)


__all__ = [
    "ADJUDICATION_IN_REVIEW",
    "ADJUDICATION_NOT_REQUIRED",
    "ADJUDICATION_PENDING",
    "ADJUDICATION_REJECTED",
    "ADJUDICATION_RESOLVED",
    "ADJUDICATION_STATUS_VALUES",
    "ALL_ANSWERABILITY_VALUES",
    "ANNOTATION_ACCEPTED_AS_GOLD",
    "ANNOTATION_ADJUDICATED",
    "ANNOTATION_GUIDELINE_VERSION",
    "ANNOTATION_IN_REVIEW",
    "ANNOTATION_METHOD_LLM_BASED",
    "ANNOTATION_ORDER",
    "ANNOTATION_PENDING",
    "ANNOTATION_REJECTED",
    "ANNOTATION_REVIEWED",
    "ANNOTATION_SCHEMA_VERSION",
    "ANNOTATION_STATUS_VALUES",
    "ANSWERABILITY_AMBIGUOUS",
    "ANSWERABILITY_CLARIFICATION_REQUIRED",
    "ANSWERABILITY_INSUFFICIENT_CONTEXT",
    "ANSWERABILITY_MALFORMED_CONTEXT",
    "ANSWERABILITY_MORE_INFORMATION",
    "ANSWERABILITY_NOT_A_DECISION_POINT",
    "ANSWERABILITY_REQUIRES_EXTERNAL_POLICY",
    "ANSWERABILITY_SUFFICIENT",
    "ANSWERABILITY_SUFFICIENT_FOR_ACTION",
    "ANSWERABILITY_UNKNOWN",
    "ANSWERABILITY_UNSCORABLE",
    "AUTHORITY_HUMAN_ADJUDICATED",
    "AUTHORITY_HUMAN_PRIMARY",
    "AUTHORITY_HUMAN_SECONDARY",
    "AUTHORITY_LLM_GENERATED_GOLD",
    "AUTHORITY_LLM_PROPOSED",
    "AUTHORITY_VALUES",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_VALUES",
    "DECISION_CATEGORY_ACKNOWLEDGE",
    "DECISION_CATEGORY_ANSWER",
    "DECISION_CATEGORY_CLARIFY",
    "DECISION_CATEGORY_CLOSE",
    "DECISION_CATEGORY_CONFIRM",
    "DECISION_CATEGORY_DEFER",
    "DECISION_CATEGORY_ESCALATE",
    "DECISION_CATEGORY_EXPLAIN",
    "DECISION_CATEGORY_HANDLE_OBJECTION",
    "DECISION_CATEGORY_OTHER",
    "DECISION_CATEGORY_RECOMMEND",
    "DECISION_CATEGORY_VALUES",
    "DECISION_CATEGORY_VERIFY_INFORMATION",
    "DISAGREEMENT_CATEGORIES",
    "EVIDENCE_SOURCE_EXPERT_CONSENSUS",
    "EVIDENCE_SOURCE_HISTORICAL_OBSERVATION",
    "EVIDENCE_SOURCE_OFFICIAL_POLICY",
    "EVIDENCE_SOURCE_VALUES",
    "EVIDENCE_SOURCE_VERIFIED_BUSINESS_KNOWLEDGE",
    "GROUND_TRUTH_SOURCE_DEEPSEEK_V4_PRO",
    "GROUND_TRUTH_STATUS_FROZEN",
    "GROUND_TRUTH_TYPE_LLM_GENERATED_GOLD",
    "GROUND_TRUTH_VERSION_V1",
    "HUMAN_AUTHORITIES",
    "LLM_GOLD_ANSWERABILITY_VALUES",
    "LLM_GOLD_AUTHORITIES",
    "LLM_GOLD_SCOREABLE_ANSWERABILITY",
    "POLICY_DEPENDENT_SCOREABLE_CATEGORIES",
    "POLICY_TIER_EVIDENCE",
    "SCOREABLE_ANSWERABILITY",
    "TASK_ANSWERABILITY_VALUES",
    "TIER_BOTH",
    "TIER_EXPERT_ADJUDICATED",
    "TIER_NONE",
    "TIER_POLICY_VERIFIED",
    "VERIFICATION_TIER_VALUES",
    "AdjudicationRecord",
    "AnnotationError",
    "AnnotationProvenance",
    "AnnotationSubmission",
    "BranchCondition",
    "BusinessRuleCitation",
    "BusinessSourceReference",
    "DecisionReference",
    "tier_for",
    "tier_supports_expert",
    "tier_supports_policy",
    "validate_annotation_consistency",
]
