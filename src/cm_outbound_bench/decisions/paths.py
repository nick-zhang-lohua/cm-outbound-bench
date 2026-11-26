"""Artifact locations for the decision-point benchmark.

The corpus itself lives outside the project checkout (it is confidential research
material), so its location is resolved by the existing corpus path layer with
``CM_OUTBOUND_CORPUS_DIR`` as the documented override.

Everything this benchmark *generates* is project-local and git-ignored:

``data/decision/calls/``      reconstructed conversations
``data/decision/splits/``     split manifest
``data/decision/tasks/``      public tasks, private references, artifact manifest
``data/decision/samples/``    reproducible annotation sampling plans
``data/decision/annotations/`` review packets, submissions, adjudications
``outputs/decision/traces/``  execution traces and the run-set manifest
``outputs/decision/reports/`` experiment reports
``outputs/decision/channel_review/`` channel semantics human-review package
"""

from __future__ import annotations

from pathlib import Path

from cm_outbound_bench.corpus.paths import default_corpus_dir
from cm_outbound_bench.decisions.config import DatasetConfig

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

CALLS_DIRNAME = "calls"
SPLITS_DIRNAME = "splits"
TASKS_DIRNAME = "tasks"
SAMPLES_DIRNAME = "samples"
ANNOTATIONS_DIRNAME = "annotations"
TRACES_DIRNAME = "traces"
REPORTS_DIRNAME = "reports"
CHANNEL_REVIEW_DIRNAME = "channel_review"
ANNOTATION_DOCS_DIRNAME = "annotation"


def project_root() -> Path:
    """Return the project checkout root."""
    return _PROJECT_ROOT


def _resolve(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def artifact_root(config: DatasetConfig) -> Path:
    return _resolve(config.artifact_root)


def outputs_root(config: DatasetConfig) -> Path:
    return _resolve(config.outputs_root)


def calls_dir(config: DatasetConfig) -> Path:
    return artifact_root(config) / CALLS_DIRNAME


def calls_artifact_path(config: DatasetConfig) -> Path:
    return calls_dir(config) / "calls.jsonl"


def calls_manifest_path(config: DatasetConfig) -> Path:
    return calls_dir(config) / "calls.manifest.json"


def splits_dir(config: DatasetConfig) -> Path:
    return artifact_root(config) / SPLITS_DIRNAME


def split_manifest_path(config: DatasetConfig) -> Path:
    return splits_dir(config) / "split_manifest.json"


def tasks_dir(config: DatasetConfig) -> Path:
    return artifact_root(config) / TASKS_DIRNAME


def samples_dir(config: DatasetConfig) -> Path:
    """Annotation sampling plans. Git-ignored: ids only, but still operational."""
    return artifact_root(config) / SAMPLES_DIRNAME


def annotations_dir(config: DatasetConfig) -> Path:
    """Review packets, submissions and adjudications. Git-ignored: contains text."""
    return artifact_root(config) / ANNOTATIONS_DIRNAME


def channel_review_dir(config: DatasetConfig) -> Path:
    """Human-review package for the unknown channels. Git-ignored: contains text."""
    return outputs_root(config) / CHANNEL_REVIEW_DIRNAME


def traces_root(config: DatasetConfig) -> Path:
    return outputs_root(config) / TRACES_DIRNAME


def reports_dir(config: DatasetConfig) -> Path:
    return outputs_root(config) / REPORTS_DIRNAME


def annotation_docs_dir(config: DatasetConfig | None = None) -> Path:
    """Committed annotation guidance. Documentation only, no conversation text."""
    return _PROJECT_ROOT / "docs" / ANNOTATION_DOCS_DIRNAME


def resolve_corpus_dir(
    config: DatasetConfig,
    explicit: str | Path | None = None,
) -> Path:
    """Resolve the de-identified corpus directory.

    Order: explicit value, ``CM_OUTBOUND_CORPUS_DIR``, the dataset config's
    ``corpus_dir``, then the conventional shared data root.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    configured = default_corpus_dir()
    if configured.exists():
        return configured
    if config.corpus_dir:
        return _resolve(config.corpus_dir)
    return configured


__all__ = [
    "ANNOTATIONS_DIRNAME",
    "ANNOTATION_DOCS_DIRNAME",
    "CALLS_DIRNAME",
    "CHANNEL_REVIEW_DIRNAME",
    "REPORTS_DIRNAME",
    "SAMPLES_DIRNAME",
    "SPLITS_DIRNAME",
    "TASKS_DIRNAME",
    "TRACES_DIRNAME",
    "annotation_docs_dir",
    "annotations_dir",
    "artifact_root",
    "calls_artifact_path",
    "calls_dir",
    "calls_manifest_path",
    "channel_review_dir",
    "outputs_root",
    "project_root",
    "reports_dir",
    "resolve_corpus_dir",
    "samples_dir",
    "split_manifest_path",
    "splits_dir",
    "tasks_dir",
    "traces_root",
]
