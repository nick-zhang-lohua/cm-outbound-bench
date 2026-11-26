"""Execution traces and run-set manifests.

Every execution writes one immutable JSON trace. The trace carries the identity of
the run, the exact prompt hash, the model configuration, the validated agent
output, the evaluation and the usage figures. It never carries an API key or a
custom header value: the manager refuses to write a trace containing a known
secret, so a future logging change cannot silently leak one.

Traces are sensitive local artefacts. They contain de-identified conversation
derived content, so ``outputs/`` is excluded from version control.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from pydantic import Field

from cm_outbound_bench.decisions.evaluator import AggregateMetrics, TaskEvaluation
from cm_outbound_bench.decisions.schema import BENCHMARK_VERSION
from cm_outbound_bench.domain.data_model import DomainModel

TRACE_SCHEMA_VERSION = "1.0"
CONDITION_BASELINE = "A"
TRACE_PREFIX = "RUN-"
MANIFEST_FILENAME = "run_set_manifest.json"

_SAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]+")


class TraceError(RuntimeError):
    """Raised when a trace cannot be written safely."""


class TraceSecretError(TraceError):
    """Raised when a trace would contain a configured credential."""


def new_run_id() -> str:
    """Return a fresh opaque run identifier."""
    return f"{TRACE_PREFIX}{uuid.uuid4().hex[:16]}"


def new_run_set_id(label: str, *, now: datetime | None = None) -> str:
    """Return a sortable run-set id built from UTC time and a safe label."""
    moment = now or datetime.now(timezone.utc)
    safe_label = _SAFE_LABEL.sub("-", label).strip("-")[:40] or "run"
    return f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{safe_label}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TraceRecord(DomainModel):
    """One reproducible record of one agent execution on one task."""

    trace_schema_version: str = TRACE_SCHEMA_VERSION
    run_id: str
    run_set_id: str
    task_id: str
    condition: str = CONDITION_BASELINE
    benchmark_version: str = BENCHMARK_VERSION
    dataset_version: str = ""
    split: str = ""
    executed_at: str = ""
    model_provider: str = ""
    model_name: str = ""
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    agent_version: str = ""
    prompt_version: str = ""
    evaluator_version: str = ""
    input_hash: str = ""
    input_bytes: int = 0
    config_hash: str = ""
    agent_output: dict[str, Any] | None = None
    raw_output_text: str | None = None
    evaluation: TaskEvaluation
    latency_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    attempts: int = 0
    execution_status: str = ""
    error: dict[str, Any] | None = None


class TraceIndexEntry(DomainModel):
    """One row of the run-set index. Contains no conversation content."""

    run_id: str
    task_id: str
    split: str
    execution_status: str
    scoring_status: str
    latency_ms: int
    total_tokens: int | None = None
    trace_file: str = ""


class RunSetManifest(DomainModel):
    """Manifest for one batch of executions."""

    manifest_schema_version: str = "1.0"
    run_set_id: str
    condition: str = CONDITION_BASELINE
    benchmark_version: str = BENCHMARK_VERSION
    dataset_version: str = ""
    split: str = ""
    created_at: str = ""
    completed_at: str = ""
    agent_label: str = ""
    model_name: str = ""
    base_url_host: str = ""
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    config_hash: str = ""
    prompt_version: str = ""
    evaluator_version: str = ""
    agent_version: str = ""
    split_manifest_sha256: str = ""
    tasks_artifact_sha256: str = ""
    tasks_requested: int = 0
    tasks_executed: int = 0
    tasks_failed: int = 0
    aggregate: AggregateMetrics = Field(default_factory=AggregateMetrics)
    entries: list[TraceIndexEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TraceManager:
    """Write traces and the run-set manifest under one output directory."""

    def __init__(
        self,
        root: str | Path,
        run_set_id: str,
        *,
        secrets: Sequence[str] = (),
    ) -> None:
        self._directory = Path(root) / run_set_id
        self._directory.mkdir(parents=True, exist_ok=True)
        self._run_set_id = run_set_id
        self._secrets = tuple(secret for secret in secrets if secret)

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def run_set_id(self) -> str:
        return self._run_set_id

    def write_trace(self, trace: TraceRecord) -> Path:
        """Write one trace after proving it contains no configured secret."""
        payload = trace.model_dump(mode="json")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        self._assert_secret_free(serialized, trace.run_id)
        target = self._directory / f"{trace.run_id}.json"
        target.write_text(serialized + "\n", encoding="utf-8")
        return target

    def write_manifest(self, manifest: RunSetManifest) -> Path:
        """Write the run-set manifest."""
        serialized = json.dumps(
            manifest.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        self._assert_secret_free(serialized, manifest.run_set_id)
        target = self._directory / MANIFEST_FILENAME
        target.write_text(serialized + "\n", encoding="utf-8")
        return target

    def _assert_secret_free(self, serialized: str, identifier: str) -> None:
        for secret in self._secrets:
            if secret and secret in serialized:
                raise TraceSecretError(
                    f"refusing to write trace {identifier}: a configured credential "
                    "would be persisted"
                )


def read_trace(path: str | Path) -> TraceRecord:
    """Read one trace back, e.g. for re-evaluation."""
    target = Path(path)
    if not target.is_file():
        raise TraceError(f"trace not found: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TraceError(f"trace is not valid JSON: {target}") from error
    return TraceRecord.model_validate(payload)


def iter_traces(directory: str | Path) -> list[TraceRecord]:
    """Read every trace in a run-set directory, sorted by file name."""
    root = Path(directory)
    if not root.is_dir():
        raise TraceError(f"run set not found: {root}")
    traces: list[TraceRecord] = []
    for path in sorted(root.glob(f"{TRACE_PREFIX}*.json")):
        traces.append(read_trace(path))
    return traces


__all__ = [
    "CONDITION_BASELINE",
    "MANIFEST_FILENAME",
    "TRACE_PREFIX",
    "TRACE_SCHEMA_VERSION",
    "RunSetManifest",
    "TraceError",
    "TraceIndexEntry",
    "TraceManager",
    "TraceRecord",
    "TraceSecretError",
    "iter_traces",
    "new_run_id",
    "new_run_set_id",
    "read_trace",
    "sha256_text",
    "utc_now",
]
