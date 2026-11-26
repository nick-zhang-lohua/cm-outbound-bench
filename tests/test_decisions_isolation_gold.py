"""Isolation guarantees for the Milestone 1.2 gold and scoring pipeline."""

from __future__ import annotations

from pathlib import Path

from cm_outbound_bench.decisions import channels
from cm_outbound_bench.decisions.gold.cli import STAGE_PLAN
from cm_outbound_bench.decisions.gold.prompts import (
    ANNOTATION_PASS_A,
    ANNOTATION_PASS_B,
    SCORING_PASS_A,
    build_annotation_messages,
    load_prompt,
    render_task_prefix,
)
from cm_outbound_bench.decisions.schema import PublicDecisionTask, VisibleTurn


def _task() -> PublicDecisionTask:
    return PublicDecisionTask(
        task_id="TASK_iso",
        visible_context=[
            VisibleTurn(role="historical_agent", content="您好，这里是移动客服。"),
            VisibleTurn(role="customer", content="我想咨询套餐。"),
        ],
    )


class TestSplitIsolation:
    def test_annotation_stages_cover_only_train_r_and_dev(self) -> None:
        splits = {split for split, _stage, _plan in STAGE_PLAN.values()}
        assert splits == {"train_r", "dev"}
        assert "test" not in splits

    def test_pipeline_never_loads_a_test_plan(self) -> None:
        for _split, _stage, plan_filename in STAGE_PLAN.values():
            assert "test" not in plan_filename


class TestPromptIsolation:
    def test_prompt_files_contain_no_simulator_reference(self) -> None:
        for name in (ANNOTATION_PASS_A, ANNOTATION_PASS_B, SCORING_PASS_A):
            text = load_prompt(name, "v1")
            assert "simulator" not in text.casefold()

    def test_annotation_prompt_receives_only_the_visible_prefix(self) -> None:
        task = _task()
        system, user = build_annotation_messages(
            task,
            prompt_name=ANNOTATION_PASS_A,
            prompt_version="v1",
        )
        rendered = render_task_prefix(task)
        assert rendered in user
        assert "TASK_iso" not in user  # opaque ids are not needed by the annotator
        assert "CALL_" not in user  # no conversation identifiers

    def test_pass_a_and_pass_b_are_different_files(self) -> None:
        assert load_prompt(ANNOTATION_PASS_A, "v1") != load_prompt(ANNOTATION_PASS_B, "v1")


class TestChannelMapping:
    def test_production_channel_mapping_is_unchanged(self) -> None:
        channels.assert_production_mapping_unchanged()
        assert channels.CONFIRMED_CHANNEL_ROLES == {1: "historical_agent", 2: "customer"}

    def test_unknown_channels_stay_unknown(self) -> None:
        assert set(channels.UNKNOWN_CHANNEL_IDS) == {3, 4, 5}


class TestHistoricalDataNeverGold:
    def test_gold_prompts_never_mention_historical_continuation(self) -> None:
        text = load_prompt(ANNOTATION_PASS_A, "v1") + load_prompt(ANNOTATION_PASS_B, "v1")
        # The annotator is told the continuation is withheld, not given one.
        assert "不会提供" in text or "不提供" in text

    def test_scorer_prompt_reads_gold_only(self) -> None:
        text = load_prompt(SCORING_PASS_A, "v1")
        assert "金标签" in text
        assert "deepseek-flash" not in text
        assert "baseline-a-v1" not in text


class TestFreezeGate:
    def test_scoring_cli_requires_frozen_manifest(self, tmp_path, monkeypatch) -> None:
        import argparse

        import pytest

        from cm_outbound_bench.decisions.scoring import cli as scoring_cli

        storage = scoring_cli.GoldStorage(
            tmp_path,
            dataset_version="cm_outbound_v2",
            tasks_artifact_sha256="hash",
        )
        monkeypatch.setattr(scoring_cli.GoldStorage, "default", classmethod(lambda cls, **kw: storage))

        args = argparse.Namespace(
            config=Path("configs/decision_annotation.toml"),
            model=None,
            prompt_version=None,
            dry_run=True,
            limit=None,
            run_set=Path("outputs/decision/traces/20261125T164504Z-group-a-dev-eval"),
            bootstrap_seed=None,
            json=False,
            resume=False,
            seed=None,
        )
        # Without a frozen manifest the evaluation must refuse, even in dry-run.
        with pytest.raises(scoring_cli.ScoringCLIError, match="freeze"):
            scoring_cli.run_group_a_evaluation(args)
