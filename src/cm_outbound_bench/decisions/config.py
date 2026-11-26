"""Configuration for the decision-point benchmark.

Three TOML files keep the concerns separate and reviewable:

* ``configs/dataset.toml`` - where the corpus and artifacts live, plus the
  task-construction policy;
* ``configs/split.toml`` - seed and split targets;
* ``configs/baseline_agent.toml`` - endpoint, model and generation parameters.
  It names the environment variable that holds the credential; it never holds a
  credential.

TOML is used instead of YAML so the project keeps a single stdlib-only
configuration path and no extra runtime dependency.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Mapping

from pydantic import Field, ValidationError

from cm_outbound_bench.decisions.builder import BuilderConfig
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.splitting import SplitTargets

DATASET_CONFIG_ENV = "CM_DECISION_DATASET_CONFIG"
SPLIT_CONFIG_ENV = "CM_DECISION_SPLIT_CONFIG"

DEFAULT_DATASET_CONFIG = Path("configs/dataset.toml")
DEFAULT_SPLIT_CONFIG = Path("configs/split.toml")
DEFAULT_AGENT_CONFIG = Path("configs/baseline_agent.toml")

#: Reproducible default seed for every split, recorded in the manifest.
DEFAULT_SEED = 20261125


class ConfigError(RuntimeError):
    """Raised when a configuration file is present but invalid."""


class DatasetConfig(DomainModel):
    """Corpus location, artifact locations and task construction policy."""

    corpus_dir: str | None = Field(
        default=None,
        description="Explicit de-identified corpus directory; env var wins.",
    )
    artifact_root: str = "data/decision"
    outputs_root: str = "outputs/decision"
    near_duplicate_max_hamming: int = Field(default=3, ge=0, le=8)
    task_construction: BuilderConfig = Field(default_factory=BuilderConfig)


class SplitConfig(DomainModel):
    """Seed and allocation targets for the call-level split."""

    seed: int = DEFAULT_SEED
    targets: SplitTargets = Field(default_factory=SplitTargets)


def load_dataset_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> DatasetConfig:
    """Load the dataset configuration, falling back to defaults when absent."""
    environment = os.environ if environ is None else environ
    explicit = path if path is not None else environment.get(DATASET_CONFIG_ENV)
    target = Path(explicit).expanduser() if explicit else DEFAULT_DATASET_CONFIG
    if not target.is_file():
        if explicit:
            raise ConfigError(f"dataset config not found: {target}")
        return DatasetConfig()
    return _load(target, DatasetConfig)


def load_split_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> SplitConfig:
    """Load the split configuration, falling back to defaults when absent."""
    environment = os.environ if environ is None else environ
    explicit = path if path is not None else environment.get(SPLIT_CONFIG_ENV)
    target = Path(explicit).expanduser() if explicit else DEFAULT_SPLIT_CONFIG
    if not target.is_file():
        if explicit:
            raise ConfigError(f"split config not found: {target}")
        return SplitConfig()
    return _load(target, SplitConfig)


def _load(path: Path, model: type[DatasetConfig] | type[SplitConfig]):  # noqa: ANN202
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"cannot read config {path}: {error}") from error
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise ConfigError(
            f"invalid config {path}: {error.error_count()} validation error(s)"
        ) from error


__all__ = [
    "DATASET_CONFIG_ENV",
    "DEFAULT_AGENT_CONFIG",
    "DEFAULT_DATASET_CONFIG",
    "DEFAULT_SEED",
    "DEFAULT_SPLIT_CONFIG",
    "SPLIT_CONFIG_ENV",
    "ConfigError",
    "DatasetConfig",
    "SplitConfig",
    "load_dataset_config",
    "load_split_config",
]
