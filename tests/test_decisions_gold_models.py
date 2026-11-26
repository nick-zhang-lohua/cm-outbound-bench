"""Tests for gold annotation parsing, comparison and schema gates."""

from __future__ import annotations

from cm_outbound_bench.decisions.annotation import (
    ANNOTATION_ACCEPTED_AS_GOLD,
    AUTHORITY_LLM_GENERATED_GOLD,
    AnnotationProvenance,
    DecisionReference,
)
from cm_outbound_bench.decisions.gold.agreement import action_similarity, compare_passes
from cm_outbound_bench.decisions.gold.models import (
    COMPARISON_ACCEPTABLE_ALTERNATIVES,
    COMPARISON_AGREEMENT,
    COMPARISON_BOTH_UNSCORED,
    COMPARISON_MAJOR,
    GoldDecision,
    PassAnnotation,
    extract_json_object,
    normalize_answerability,
    parse_annotation_json,
)
from cm_outbound_bench.decisions.schema import PrivateTaskReference


def _pass(
    *,
    task_id: str = "T",
    pass_id: str = "A",
    answerability: str = "sufficient_for_action",
    primary: GoldDecision | None = None,
    alternatives: list[GoldDecision] | None = None,
    required_information: list[str] | None = None,
    required_steps: list[str] | None = None,
    prohibited: list[str] | None = None,
    scoreable: bool = True,
    confidence: str = "high",
) -> PassAnnotation:
    return PassAnnotation(
        task_id=task_id,
        pass_id=pass_id,  # type: ignore[arg-type]
        task_answerability=answerability,
        primary_expected_decision=primary,
        acceptable_alternative_decisions=alternatives or [],
        required_information=required_information or [],
        required_steps=required_steps or [],
        prohibited_steps=prohibited or [],
        scoreable=scoreable,
        confidence=confidence,  # type: ignore[arg-type]
        evidence_refs=[],
    )


class TestParsing:
    def test_extract_json_object_handles_fences(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert extract_json_object(text) == {"a": 1}

    def test_extract_json_object_handles_surrounding_prose(self) -> None:
        text = 'Here is the result:\n{"a": 1}\nDone.'
        assert extract_json_object(text) == {"a": 1}

    def test_extract_json_object_returns_none_for_garbage(self) -> None:
        assert extract_json_object("no json here") is None

    def test_parse_keeps_evidence_and_decisions(self) -> None:
        payload = {
            "task_answerability": "clarification_required",
            "customer_intent": "ask about the plan",
            "primary_expected_decision": {
                "category": "clarify",
                "action": "ask who is the line owner",
                "reason": "ownership is unverified",
            },
            "required_information": ["line ownership"],
            "evidence_refs": [{"visible_turn": 2, "quote": "..."}],
            "confidence": "high",
            "scoreable": True,
            "external_policy_dependency": False,
            "policy_verified": False,
        }
        annotation = parse_annotation_json(payload, task_id="T", pass_id="A")
        assert annotation.task_answerability == "clarification_required"
        assert annotation.scoreable is True
        assert annotation.primary_expected_decision is not None
        assert annotation.primary_expected_decision.category == "clarify"
        assert annotation.evidence_refs[0].visible_turn == 2

    def test_ambiguous_task_is_forced_unscored(self) -> None:
        payload = {
            "task_answerability": "ambiguous",
            "primary_expected_decision": {
                "category": "answer",
                "action": "give the price",
                "reason": "x",
            },
            "scoreable": True,
        }
        annotation = parse_annotation_json(payload, task_id="T", pass_id="A")
        assert annotation.scoreable is False

    def test_clarification_without_information_is_unscored(self) -> None:
        payload = {
            "task_answerability": "clarification_required",
            "scoreable": True,
            "primary_expected_decision": {
                "category": "clarify",
                "action": "ask",
                "reason": "x",
            },
        }
        annotation = parse_annotation_json(payload, task_id="T", pass_id="A")
        assert annotation.scoreable is False
        assert annotation.unscorable_reason

    def test_old_answerability_vocabulary_is_normalized(self) -> None:
        assert normalize_answerability("sufficient_information") == "sufficient_for_action"
        assert normalize_answerability("more_information_required") == "clarification_required"
        assert normalize_answerability("ambiguous") == "ambiguous"
        assert normalize_answerability("garbage") == "insufficient_context"

    def test_unknown_category_falls_back_to_other(self) -> None:
        payload = {
            "task_answerability": "sufficient_for_action",
            "primary_expected_decision": {
                "category": "sell_stuff",
                "action": "offer the plan",
                "reason": "x",
            },
            "scoreable": True,
        }
        annotation = parse_annotation_json(payload, task_id="T", pass_id="A")
        assert annotation.primary_expected_decision is not None
        assert annotation.primary_expected_decision.category == "other"


class TestComparison:
    def test_identical_passes_agree(self) -> None:
        primary = GoldDecision(
            decision_id="d", category="clarify", action="确认机主身份", reason="x"
        )
        left = _pass(task_id="T", primary=primary, required_steps=["确认机主身份"])
        right = left.model_copy(deep=True)
        right.pass_id = "B"
        comparison = compare_passes(left, right)
        assert comparison.classification == COMPARISON_AGREEMENT

    def test_mutual_alternatives_are_acceptable(self) -> None:
        left = _pass(
            task_id="T",
            primary=GoldDecision(
                decision_id="d1", category="clarify", action="询问机主身份", reason="x"
            ),
            alternatives=[
                GoldDecision(
                    decision_id="d2",
                    category="verify_information",
                    action="核实账户信息",
                    reason="x",
                )
            ],
        )
        right = _pass(
            task_id="T",
            pass_id="B",
            primary=GoldDecision(
                decision_id="d2",
                category="verify_information",
                action="核实账户信息",
                reason="x",
            ),
            alternatives=[
                GoldDecision(
                    decision_id="d1", category="clarify", action="询问机主身份", reason="x"
                )
            ],
        )
        comparison = compare_passes(left, right)
        assert comparison.classification == COMPARISON_ACCEPTABLE_ALTERNATIVES

    def test_different_categories_disagree_majorly(self) -> None:
        left = _pass(
            task_id="T",
            primary=GoldDecision(
                decision_id="d1", category="close_conversation", action="结束通话", reason="x"
            ),
        )
        right = _pass(
            task_id="T",
            pass_id="B",
            primary=GoldDecision(
                decision_id="d2", category="recommend", action="推荐套餐", reason="x"
            ),
        )
        comparison = compare_passes(left, right)
        assert comparison.classification == COMPARISON_MAJOR

    def test_both_unscored_is_its_own_class(self) -> None:
        left = _pass(task_id="T", answerability="ambiguous", scoreable=False)
        right = _pass(
            task_id="T", pass_id="B", answerability="ambiguous", scoreable=False
        )
        comparison = compare_passes(left, right)
        assert comparison.classification == COMPARISON_BOTH_UNSCORED

    def test_scoreability_split_is_major(self) -> None:
        left = _pass(task_id="T", scoreable=True)
        right = _pass(task_id="T", pass_id="B", scoreable=False)
        comparison = compare_passes(left, right)
        assert comparison.classification == COMPARISON_MAJOR

    def test_action_similarity_is_symmetric_and_bounded(self) -> None:
        assert action_similarity("确认机主身份", "确认机主身份") == 1.0
        value = action_similarity("询问机主", "确认机主")
        assert 0.0 <= value <= 1.0
        assert action_similarity("", "anything") == 0.0


class TestGoldSchemaGates:
    def test_llm_gold_reference_scores_only_through_the_gold_gate(self) -> None:
        base = PrivateTaskReference(
            task_id="T",
            call_id="CALL_X",
            split="dev",
            cutoff_record_index=1,
            cutoff_seq=1,
            cutoff_segment_id="S",
            cutoff_start_ms=1000,
            cutoff_visible_turn_count=2,
            hidden_speech_turn_count=0,
        )
        gold = base.model_copy(
            update={
                "scoring_status": "scored",
                "annotation_status": ANNOTATION_ACCEPTED_AS_GOLD,
                "ground_truth_type": "llm_generated_gold",
                "ground_truth_source": "deepseek_v4_pro",
                "ground_truth_status": "frozen",
                "annotation_method": "llm_based",
                "task_answerability": "sufficient_for_action",
                "primary_expected_decision": DecisionReference(
                    decision_id="d",
                    label="确认机主身份",
                    decision_category="clarify",
                    match_keywords=["确认机主身份"],
                ),
                "annotation_provenance": AnnotationProvenance(
                    authority=AUTHORITY_LLM_GENERATED_GOLD,
                    model_name="deepseek-v4-pro",
                ),
            }
        )
        assert gold.is_llm_gold is True
        assert gold.is_scored is True

    def test_gold_gate_requires_accepted_as_gold_status(self) -> None:
        base = PrivateTaskReference(
            task_id="T",
            call_id="CALL_X",
            split="dev",
            cutoff_record_index=1,
            cutoff_seq=1,
            cutoff_segment_id="S",
            cutoff_start_ms=1000,
            cutoff_visible_turn_count=2,
            hidden_speech_turn_count=0,
        )
        gold = base.model_copy(
            update={
                "scoring_status": "scored",
                "annotation_status": "reviewed",
                "ground_truth_type": "llm_generated_gold",
                "task_answerability": "sufficient_for_action",
                "primary_expected_decision": DecisionReference(
                    decision_id="d",
                    label="确认机主身份",
                    decision_category="clarify",
                ),
                "annotation_provenance": AnnotationProvenance(
                    authority=AUTHORITY_LLM_GENERATED_GOLD,
                    model_name="deepseek-v4-pro",
                ),
            }
        )
        assert gold.is_scored is False

    def test_plain_llm_proposal_is_still_never_scored(self) -> None:
        base = PrivateTaskReference(
            task_id="T",
            call_id="CALL_X",
            split="dev",
            cutoff_record_index=1,
            cutoff_seq=1,
            cutoff_segment_id="S",
            cutoff_start_ms=1000,
            cutoff_visible_turn_count=2,
            hidden_speech_turn_count=0,
        )
        proposed = base.model_copy(
            update={
                "scoring_status": "scored",
                "annotation_status": "reviewed",
                "task_answerability": "sufficient_for_action",
                "primary_expected_decision": DecisionReference(
                    decision_id="d",
                    label="确认机主身份",
                    decision_category="clarify",
                ),
                "annotation_provenance": AnnotationProvenance(
                    authority="llm_proposed", model_name="deepseek-v4-pro"
                ),
            }
        )
        assert proposed.is_scored is False

    def test_external_policy_task_scores_only_with_clarification(self) -> None:
        base = PrivateTaskReference(
            task_id="T",
            call_id="CALL_X",
            split="dev",
            cutoff_record_index=1,
            cutoff_seq=1,
            cutoff_segment_id="S",
            cutoff_start_ms=1000,
            cutoff_visible_turn_count=2,
            hidden_speech_turn_count=0,
        )

        def _with_category(category: str) -> PrivateTaskReference:
            return base.model_copy(
                update={
                    "scoring_status": "scored",
                    "annotation_status": ANNOTATION_ACCEPTED_AS_GOLD,
                    "ground_truth_type": "llm_generated_gold",
                    "task_answerability": "requires_external_business_policy",
                    "primary_expected_decision": DecisionReference(
                        decision_id="d",
                        label="verify",
                        decision_category=category,
                    ),
                    "annotation_provenance": AnnotationProvenance(
                        authority=AUTHORITY_LLM_GENERATED_GOLD,
                        model_name="deepseek-v4-pro",
                    ),
                }
            )

        assert _with_category("verify_information").is_scored is True
        assert _with_category("answer").is_scored is False
