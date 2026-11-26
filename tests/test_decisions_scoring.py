"""Tests for the blind scoring pipeline, metrics and bootstrap statistics."""

from __future__ import annotations

import pytest

from cm_outbound_bench.decisions.annotation import (
    ANNOTATION_ACCEPTED_AS_GOLD,
    AUTHORITY_LLM_GENERATED_GOLD,
    AnnotationProvenance,
    DecisionReference,
)
from cm_outbound_bench.decisions.schema import PrivateTaskReference
from cm_outbound_bench.decisions.scoring.metrics import compute_metrics
from cm_outbound_bench.decisions.scoring.schema import (
    OUTCOME_FULLY_CORRECT,
    OUTCOME_INCORRECT,
    OUTCOME_PARTIALLY_CORRECT,
    OUTCOME_UNSCORABLE,
    CriticalError,
    FinalScore,
    ScoreAdjudication,
    ScoreComparison,
    ScoreOutcome,
    ScoreRecord,
)
from cm_outbound_bench.decisions.scoring.scorer import (
    aggregate_saved_scores,
    compare_scores,
    finalize_score,
    render_agent_output,
    render_gold_label,
    scoring_cache_key,
)
from cm_outbound_bench.decisions.scoring.statistics import bootstrap_rate_ci


def _gold(task_id: str = "TASK_s") -> PrivateTaskReference:
    return PrivateTaskReference(
        task_id=task_id,
        call_id="CALL_s",
        split="dev",
        cutoff_record_index=1,
        cutoff_seq=1,
        cutoff_segment_id="SEG",
        cutoff_start_ms=1000,
        cutoff_visible_turn_count=2,
        hidden_speech_turn_count=0,
        scoring_status="scored",
        annotation_status=ANNOTATION_ACCEPTED_AS_GOLD,
        ground_truth_type="llm_generated_gold",
        ground_truth_source="deepseek_v4_pro",
        ground_truth_status="frozen",
        ground_truth_version="cm-outbound-llm-gold-v1",
        task_answerability="sufficient_for_action",
        primary_expected_decision=DecisionReference(
            decision_id="d",
            label="介绍套餐内容",
            decision_category="explain",
            rationale="客户想了解套餐",
            match_keywords=["介绍套餐内容"],
        ),
        required_steps=["介绍套餐内容"],
        annotation_provenance=AnnotationProvenance(
            authority=AUTHORITY_LLM_GENERATED_GOLD, model_name="deepseek-v4-pro"
        ),
    )


def _final(task_id: str, outcome: str, *, errors: list[str] | None = None) -> FinalScore:
    return FinalScore(
        task_id=task_id,
        outcome=outcome,
        critical_errors=[CriticalError(kind=kind, description="x") for kind in (errors or [])],
        comparison=ScoreComparison(
            task_id=task_id, classification="agreement", same_outcome=True
        ),
        source="scorer_agreement",
    )


class TestScoringSchema:
    def test_outcome_validation(self) -> None:
        outcome = ScoreOutcome(
            task_id="T", scorer_id="A", outcome=OUTCOME_FULLY_CORRECT
        )
        assert outcome.outcome == OUTCOME_FULLY_CORRECT
        import pytest

        with pytest.raises(Exception):
            ScoreOutcome(task_id="T", scorer_id="A", outcome="great")

    def test_critical_error_kinds_are_validated(self) -> None:
        import pytest

        with pytest.raises(Exception):
            CriticalError(kind="made_up_error")

    def test_compare_scores_agreement(self) -> None:
        left = ScoreOutcome(task_id="T", scorer_id="A", outcome="fully_correct")
        right = ScoreOutcome(task_id="T", scorer_id="B", outcome="fully_correct")
        comparison = compare_scores(left, right)
        assert comparison.classification == "agreement"
        assert comparison.same_outcome is True

    def test_compare_scores_disagreement(self) -> None:
        left = ScoreOutcome(task_id="T", scorer_id="A", outcome="fully_correct")
        right = ScoreOutcome(task_id="T", scorer_id="B", outcome="incorrect")
        comparison = compare_scores(left, right)
        assert comparison.classification == "disagreement"

    def test_finalize_uses_adjudication_on_disagreement(self) -> None:
        left = ScoreOutcome(task_id="T", scorer_id="A", outcome="fully_correct")
        right = ScoreOutcome(task_id="T", scorer_id="B", outcome="incorrect")
        comparison = compare_scores(left, right)
        adjudication = ScoreAdjudication(
            task_id="T",
            outcome="partially_correct",
            parsing_status="ok",
        )
        final = finalize_score(comparison, left, right, adjudication)
        assert final.outcome == "partially_correct"
        assert final.source == "scorer_adjudication"

    def test_finalize_without_adjudication_stays_unscorable(self) -> None:
        left = ScoreOutcome(task_id="T", scorer_id="A", outcome="fully_correct")
        right = ScoreOutcome(task_id="T", scorer_id="B", outcome="incorrect")
        comparison = compare_scores(left, right)
        final = finalize_score(comparison, left, right, None)
        assert final.outcome == OUTCOME_UNSCORABLE


class TestUnscoredGoldGate:
    """A task whose frozen Gold Label is not scoreable can never be scored.

    These tests pin the rule that only ``is_scored`` references enter the
    business denominators, including when finals are re-derived from saved
    records with no API call.
    """

    def _record(self, task_id: str, scorer_id: str, outcome: str) -> ScoreRecord:
        return ScoreRecord(
            run_id="gold-score",
            task_id=task_id,
            scorer_id=scorer_id,
            model="deepseek-v4-pro",
            prompt_version="v1",
            parsing_status="ok",
            outcome=ScoreOutcome(
                task_id=task_id, scorer_id=scorer_id, outcome=outcome
            ),
        )

    def test_unscored_gold_is_excluded_even_if_both_scorers_say_correct(self) -> None:
        gold = {
            "T": _gold("T").model_copy(update={"scoring_status": "unscored"}),
        }
        assert gold["T"].is_scored is False
        finals, problems = aggregate_saved_scores(
            gold=gold,
            records_a={"T": self._record("T", "A", OUTCOME_FULLY_CORRECT)},
            records_b={"T": self._record("T", "B", OUTCOME_FULLY_CORRECT)},
        )
        assert problems == []
        assert finals["T"].outcome == OUTCOME_UNSCORABLE
        assert finals["T"].source == "gold_reference_unscored"
        metrics = compute_metrics(
            finals=finals,
            references=gold,
            outputs={},
            executed_tasks=1,
        )
        assert metrics["scored_tasks"] == 0
        assert metrics["llm_gold_decision_success_rate"] is None
        assert metrics["scored_task_coverage"] == 0.0

    def test_scored_gold_agreement_is_rebuilt_from_saved_passes(self) -> None:
        gold = {"T": _gold("T")}
        finals, problems = aggregate_saved_scores(
            gold=gold,
            records_a={"T": self._record("T", "A", OUTCOME_PARTIALLY_CORRECT)},
            records_b={"T": self._record("T", "B", OUTCOME_PARTIALLY_CORRECT)},
        )
        assert problems == []
        assert finals["T"].outcome == OUTCOME_PARTIALLY_CORRECT
        assert finals["T"].source == "scorer_agreement"

    def test_disagreement_reuses_the_saved_adjudication(self) -> None:
        gold = {"T": _gold("T")}
        adjudication = ScoreAdjudication(
            task_id="T", outcome=OUTCOME_INCORRECT, parsing_status="ok"
        )
        finals, _ = aggregate_saved_scores(
            gold=gold,
            records_a={"T": self._record("T", "A", OUTCOME_FULLY_CORRECT)},
            records_b={"T": self._record("T", "B", OUTCOME_PARTIALLY_CORRECT)},
            adjudications={"T": adjudication},
        )
        assert finals["T"].outcome == OUTCOME_INCORRECT
        assert finals["T"].source == "scorer_adjudication"

    def test_disagreement_without_a_saved_adjudication_stays_unscorable(self) -> None:
        gold = {"T": _gold("T")}
        finals, _ = aggregate_saved_scores(
            gold=gold,
            records_a={"T": self._record("T", "A", OUTCOME_FULLY_CORRECT)},
            records_b={"T": self._record("T", "B", OUTCOME_INCORRECT)},
        )
        assert finals["T"].outcome == OUTCOME_UNSCORABLE
        assert finals["T"].source == "adjudication_unavailable"

    def test_missing_saved_pass_is_reported_not_invented(self) -> None:
        gold = {"T": _gold("T")}
        finals, problems = aggregate_saved_scores(
            gold=gold,
            records_a={"T": self._record("T", "A", OUTCOME_FULLY_CORRECT)},
            records_b={},
        )
        assert "T" not in finals
        assert problems and "T" in problems[0]


class TestGoldImmutability:
    def test_gold_label_render_has_no_agent_identity(self) -> None:
        rendered = render_gold_label(_gold())
        assert "deepseek-flash" not in rendered
        assert "baseline-a" not in rendered
        assert "group_a" not in rendered.casefold()

    def test_agent_output_render_is_structured_only(self) -> None:
        assert render_agent_output(None) != ""
        assert "model" not in render_agent_output(
            {"customer_response": "好的", "next_action": "确认"}
        ).casefold()

    def test_cache_key_covers_reference_hash(self) -> None:
        left = scoring_cache_key(
            task_id="T",
            gold_version="v1",
            reference_hash="hash-a",
            model="deepseek-v4-pro",
            prompt_hash="p",
            config_hash="c",
            scorer_id="A",
        )
        right = scoring_cache_key(
            task_id="T",
            gold_version="v1",
            reference_hash="hash-b",
            model="deepseek-v4-pro",
            prompt_hash="p",
            config_hash="c",
            scorer_id="A",
        )
        assert left != right


class TestMetrics:
    def test_dsr_dar_pdr_idr_denominators(self) -> None:
        finals = {
            "t1": _final("t1", OUTCOME_FULLY_CORRECT),
            "t2": _final("t2", OUTCOME_PARTIALLY_CORRECT),
            "t3": _final("t3", OUTCOME_INCORRECT),
            "t4": _final("t4", OUTCOME_UNSCORABLE),
        }
        metrics = compute_metrics(
            finals=finals,
            references={"t1": _gold("t1"), "t2": _gold("t2"), "t3": _gold("t3"), "t4": _gold("t4")},
            outputs={},
            executed_tasks=4,
        )
        assert metrics["scored_tasks"] == 3
        assert metrics["llm_gold_decision_success_rate"] == pytest.approx(1 / 3)
        assert metrics["decision_acceptance_rate"] == pytest.approx(2 / 3)
        assert metrics["partial_decision_rate"] == pytest.approx(1 / 3)
        assert metrics["incorrect_decision_rate"] == pytest.approx(1 / 3)
        assert metrics["scored_task_coverage"] == pytest.approx(3 / 4)
        assert metrics["policy_verified_decision_success_rate"] is None

    def test_no_scored_tasks_gives_none_rates(self) -> None:
        finals = {"t4": _final("t4", OUTCOME_UNSCORABLE)}
        metrics = compute_metrics(
            finals=finals,
            references={"t4": _gold("t4")},
            outputs={},
            executed_tasks=1,
        )
        assert metrics["llm_gold_decision_success_rate"] is None
        assert metrics["critical_error_rate"] is None

    def test_critical_errors_are_counted_separately(self) -> None:
        finals = {
            "t1": _final("t1", OUTCOME_FULLY_CORRECT, errors=["fabricated_business_policy"]),
            "t2": _final("t2", OUTCOME_FULLY_CORRECT),
        }
        metrics = compute_metrics(
            finals=finals,
            references={"t1": _gold("t1"), "t2": _gold("t2")},
            outputs={},
            executed_tasks=2,
        )
        assert metrics["critical_error_tasks"] == 1
        assert metrics["critical_error_rate"] == 0.5
        assert metrics["llm_gold_decision_success_rate"] == 1.0


class TestBootstrap:
    def test_bootstrap_is_deterministic(self) -> None:
        units = [f"u{i}" for i in range(50)]
        numerators = {"rate": [i % 2 for i in range(50)]}
        denominators = [1] * 50
        first = bootstrap_rate_ci(
            numerators=numerators,
            denominators=denominators,
            metric_names=["rate"],
            unit_ids=units,
            replications=200,
            seed=20261126,
        )
        second = bootstrap_rate_ci(
            numerators=numerators,
            denominators=denominators,
            metric_names=["rate"],
            unit_ids=units,
            replications=200,
            seed=20261126,
        )
        assert first == second

    def test_bootstrap_bounds_are_inside_unit_interval(self) -> None:
        units = [f"u{i}" for i in range(30)]
        numerators = {"rate": [1] * 30}
        denominators = [1] * 30
        result = bootstrap_rate_ci(
            numerators=numerators,
            denominators=denominators,
            metric_names=["rate"],
            unit_ids=units,
            replications=100,
            seed=1,
        )
        assert 0.0 <= result["rate"]["ci_lower"] <= result["rate"]["ci_upper"] <= 1.0
