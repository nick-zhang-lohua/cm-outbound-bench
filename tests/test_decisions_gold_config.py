"""Tests for the dedicated DeepSeek V4 Pro judge configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cm_outbound_bench.decisions.gold.config import (
    DEFAULT_JUDGE_CONFIG,
    JudgeConfigError,
    JudgeStageConfig,
    load_judge_config,
    resolve_api_key,
)


def test_default_config_uses_the_verified_v4_pro_identifier() -> None:
    config = load_judge_config(DEFAULT_JUDGE_CONFIG, environ={})
    assert config.model == "deepseek-v4-pro"
    assert config.model_checked() == "deepseek-v4-pro"
    assert config.annotation.prompt_version == "v1"
    assert config.evaluation.prompt_version == "v1"


def test_unverified_model_is_rejected_without_substitution() -> None:
    config = load_judge_config(DEFAULT_JUDGE_CONFIG, environ={})
    replaced = config.model_copy(update={"model": "deepseek-flash"})
    with pytest.raises(JudgeConfigError):
        replaced.model_checked()


def test_env_model_override_is_still_verified() -> None:
    config = load_judge_config(
        DEFAULT_JUDGE_CONFIG, environ={"CM_JUDGE_MODEL": "deepseek-v4-pro"}
    )
    assert config.model == "deepseek-v4-pro"


def test_config_hash_is_deterministic_and_stage_specific() -> None:
    config = load_judge_config(DEFAULT_JUDGE_CONFIG, environ={})
    assert config.config_hash(stage="annotation") == config.config_hash(
        stage="annotation"
    )
    annotation = config.annotation.model_copy(update={"prompt_version": "v2"})
    changed = config.model_copy(update={"annotation": annotation})
    assert changed.config_hash(stage="annotation") != config.config_hash(
        stage="annotation"
    )
    # The annotation prompt version must not leak into the evaluation hash.
    assert changed.config_hash(stage="evaluation") == config.config_hash(
        stage="evaluation"
    )


def test_missing_api_key_is_a_hard_error_with_no_fallback() -> None:
    config = load_judge_config(DEFAULT_JUDGE_CONFIG, environ={})
    with pytest.raises(JudgeConfigError, match="DEEPSEEK_API_KEY"):
        resolve_api_key(config, environ={})


def test_missing_config_file_raises() -> None:
    with pytest.raises(JudgeConfigError):
        load_judge_config(Path("configs/does_not_exist.toml"), environ={})


def test_stage_config_defaults() -> None:
    stage = JudgeStageConfig()
    assert stage.prompt_version == "v1"
    assert stage.guideline_version == "annotation-guidelines-v1"
