"""Tests for gold validation, reference application and freezing."""

from __future__ import annotations

import json

import pytest

from cm_outbound_bench.decisions.annotation import (
    ANNOTATION_ACCEPTED_AS_GOLD,
    AUTHORITY_LLM_GENERATED_GOLD,
)
from cm_outbound_bench.decisions.gold.models import (
    COMPARISON_AGREEMENT,
    EvidenceRef,
    FinalGoldState,
    GoldDecision,
    PassAnnotation,
    PassComparison,
)
from cm_outbound_bench.decisions.gold.validation import (
    FreezeError,
    apply_finals,
    build_gold_reference,
    validate_final,
    write_frozen_manifest,
    write_references,
)
from cm_outbound_bench.decisions.schema import (
    PrivateTaskReference,
    PublicDecisionTask,
    VisibleTurn,
)


def _task(task_id: str = "TASK_gold") -> PublicDecisionTask:
    return PublicDecisionTask(
        task_id=task_id,
        visible_context=[
            VisibleTurn(role="historical_agent", content="您好，这里是移动客服。"),
            VisibleTurn(role="customer", content="我想咨询套餐。"),
            VisibleTurn(role="historical_agent", content="请问您想了解哪方面？"),
        ],
    )


def _base(task_id: str = "TASK_gold") -> PrivateTaskReference:
    return PrivateTaskReference(
        task_id=task_id,
        call_id="CALL_gold",
        split="dev",
        cutoff_record_index=2,
        cutoff_seq=1,
        cutoff_segment_id="SEG_gold",
        cutoff_start_ms=5000,
        cutoff_visible_turn_count=3,
        hidden_speech_turn_count=0,
    )


def _scored_final(task_id: str = "TASK_gold") -> FinalGoldState:
    annotation = PassAnnotation(
        task_id=task_id,
        pass_id="A",
        task_answerability="sufficient_for_action",
        primary_expected_decision=GoldDecision(
            decision_id="d", category="explain", action="介绍套餐内容", reason="客户想了解"
        ),
        required_steps=["介绍套餐内容"],
        evidence_refs=[EvidenceRef(visible_turn=2, quote="我想咨询套餐")],
        confidence="high",
        scoreable=True,
    )
    return FinalGoldState(
        task_id=task_id,
        comparison=PassComparison(
            task_id=task_id, classification=COMPARISON_AGREEMENT
        ),
        final=annotation,
        scoring_status="scored",
        source="pass_a_common",
    )


class TestValidateFinal:
    def test_valid_scored_final_passes(self) -> None:
        assert validate_final(_scored_final(), _task()) == []

    def test_evidence_turn_out_of_range_fails(self) -> None:
        final = _scored_final()
        final.final.evidence_refs.append(EvidenceRef(visible_turn=99, quote="x"))
        issues = validate_final(final, _task())
        assert any("evidence_ref_turn_out_of_range" in issue for issue in issues)

    def test_scored_without_decision_fails(self) -> None:
        final = _scored_final()
        final.final.primary_expected_decision = None
        final.final.required_information = []
        issues = validate_final(final, _task())
        assert any(
            "no_decision_and_no_required_information" in issue for issue in issues
        )

    def test_policy_verified_true_is_rejected(self) -> None:
        final = _scored_final()
        final.final.policy_verified = True
        issues = validate_final(final, _task())
        assert any("policy_verified" in issue for issue in issues)

    def test_external_policy_with_answer_category_fails(self) -> None:
        final = _scored_final()
        final.final.task_answerability = "requires_external_business_policy"
        final.final.primary_expected_decision.category = "answer"
        issues = validate_final(final, _task())
        assert any("external_policy_task" in issue for issue in issues)

    def test_clarification_requires_information(self) -> None:
        final = _scored_final()
        final.final.task_answerability = "clarification_required"
        final.final.required_information = []
        issues = validate_final(final, _task())
        assert any("clarification_required_must_name" in issue for issue in issues)


class TestApplyAndFreeze:
    def test_apply_finals_builds_scored_gold_reference(self) -> None:
        gold, problems = apply_finals(
            finals=[_scored_final()],
            references={"TASK_gold": _base()},
            tasks={"TASK_gold": _task()},
            model="deepseek-v4-pro",
            prompt_version="v1",
            config_hash="hash",
            provider_label="deepseek-v4-pro-judge",
        )
        assert problems == []
        reference = gold["TASK_gold"]
        assert reference.is_llm_gold is True
        assert reference.is_scored is True
        assert reference.ground_truth_type == "llm_generated_gold"
        assert reference.ground_truth_source == "deepseek_v4_pro"
        assert reference.ground_truth_status == "frozen"
        assert reference.annotation_method == "llm_based"
        assert reference.annotation_status == ANNOTATION_ACCEPTED_AS_GOLD
        assert reference.policy_verified is False
        assert reference.primary_expected_decision is not None
        assert reference.annotation_provenance is not None
        assert reference.annotation_provenance.authority == AUTHORITY_LLM_GENERATED_GOLD

    def test_invalid_final_is_demoted_not_rejected(self) -> None:
        final = _scored_final()
        final.final.evidence_refs.append(EvidenceRef(visible_turn=99, quote="x"))
        gold, problems = apply_finals(
            finals=[final],
            references={"TASK_gold": _base()},
            tasks={"TASK_gold": _task()},
            model="deepseek-v4-pro",
            prompt_version="v1",
            config_hash="hash",
            provider_label="judge",
        )
        assert problems == []
        assert gold["TASK_gold"].scoring_status == "unscored"
        assert gold["TASK_gold"].is_scored is False

    def test_duplicate_finals_are_rejected_not_counted_twice(self) -> None:
        gold, problems = apply_finals(
            finals=[_scored_final(), _scored_final()],
            references={"TASK_gold": _base()},
            tasks={"TASK_gold": _task()},
            model="deepseek-v4-pro",
            prompt_version="v1",
            config_hash="hash",
            provider_label="judge",
        )
        assert any("duplicate final state" in problem for problem in problems)
        assert len(gold) == 1

    def test_wrong_split_is_rejected(self) -> None:
        base = _base().model_copy(update={"split": "test"})
        gold, problems = apply_finals(
            finals=[_scored_final()],
            references={"TASK_gold": base},
            tasks={"TASK_gold": _task()},
            model="deepseek-v4-pro",
            prompt_version="v1",
            config_hash="hash",
            provider_label="judge",
        )
        assert any("unexpected split" in problem for problem in problems)
        assert gold == {}

    def test_freeze_refuses_silent_overwrite(self, tmp_path) -> None:
        manifests = tmp_path / "manifests"
        write_frozen_manifest(
            manifests,
            train_r={"tasks": 400, "scored": 300, "unscored": 100, "sha256": "x"},
            dev={"tasks": 400, "scored": 350, "unscored": 50, "sha256": "y"},
            prompt_version="v1",
            dataset_version="cm_outbound_v2",
            model="deepseek-v4-pro",
        )
        with pytest.raises(FreezeError):
            write_frozen_manifest(
                manifests,
                train_r={"tasks": 400, "scored": 0, "unscored": 400, "sha256": "z"},
                dev={"tasks": 400, "scored": 0, "unscored": 400, "sha256": "w"},
                prompt_version="v1",
                dataset_version="cm_outbound_v2",
                model="deepseek-v4-pro",
            )

    def test_write_references_returns_stable_hash(self, tmp_path) -> None:
        reference = build_gold_reference(
            final=_scored_final(),
            base=_base(),
            task=_task(),
            model="deepseek-v4-pro",
            prompt_version="v1",
            config_hash="hash",
            provider_label="judge",
        )
        info = write_references(tmp_path, [reference])
        assert info["tasks"] == 1
        assert info["scored"] == 1
        assert len(info["sha256"]) == 64
        again = write_references(tmp_path, [reference])
        assert info["sha256"] == again["sha256"]  # deterministic serialization

    def test_frozen_manifest_declares_test_untouched(self, tmp_path) -> None:
        manifests = tmp_path / "manifests"
        path = write_frozen_manifest(
            manifests,
            train_r={"tasks": 400, "scored": 300, "unscored": 100, "sha256": "x"},
            dev={"tasks": 400, "scored": 350, "unscored": 50, "sha256": "y"},
            prompt_version="v1",
            dataset_version="cm_outbound_v2",
            model="deepseek-v4-pro",
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["test_annotated"] is False
        assert payload["ground_truth_version"] == "cm-outbound-llm-gold-v1"
        assert payload["ground_truth_source"] == "deepseek_v4_pro"
        assert payload["ground_truth_type"] == "llm_generated_gold"
