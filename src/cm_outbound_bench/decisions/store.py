"""Artifact storage and the agent-safe task loader.

Two physically separate files are produced:

``tasks.jsonl``
    agent-visible tasks only (:class:`PublicDecisionTask`);
``references.jsonl``
    evaluator-only references (:class:`PrivateTaskReference`).

The loader enforces the boundary instead of trusting callers: the agent-facing
method returns a plain dictionary containing only the declared public fields, and
it refuses to return anything that carries a call id, a cutoff position, a future
turn, a historical response or a scoring label.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator

from pydantic import ValidationError

from cm_outbound_bench.decisions.schema import (
    PUBLIC_TASK_FIELDS,
    REFERENCE_SCHEMA_VERSION,
    TASK_SCHEMA_VERSION,
    PrivateTaskReference,
    PublicDecisionTask,
)
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.splitting import ALL_SPLITS

TASK_ARTIFACT_VERSION = "1.0"
PUBLIC_TASKS_FILENAME = "tasks.jsonl"
PRIVATE_REFERENCES_FILENAME = "references.jsonl"
TASK_MANIFEST_FILENAME = "tasks.manifest.json"

#: Field names that must never appear in an agent-visible task payload.
FORBIDDEN_PUBLIC_KEYS: frozenset[str] = frozenset(
    {
        "call_id",
        "transcript_id",
        "split",
        "cutoff_record_index",
        "cutoff_seq",
        "cutoff_segment_id",
        "cutoff_start_ms",
        "cutoff_visible_turn_count",
        "hidden_speech_turn_count",
        "historical_next_action_reference",
        "historical_next_action_segment_id",
        "historical_next_action_authority",
        "scoring_status",
        "annotation_status",
        "verified_business_rules",
        "expected_decisions",
        "evidence_refs",
        "data_quality_status",
        "builder_version",
        "constructed_at",
        "source_segment_id",
    }
)


class TaskArtifactError(RuntimeError):
    """Raised when the task artifacts are missing or inconsistent."""


class ArtifactValidation(DomainModel):
    """Result of validating the task artifacts against each other."""

    public_tasks: int = 0
    private_references: int = 0
    tasks_by_split: dict[str, int] = {}
    duplicate_task_ids: list[str] = []
    tasks_without_reference: list[str] = []
    references_without_task: list[str] = []
    public_tasks_with_forbidden_fields: list[str] = []
    prefix_length_mismatches: list[str] = []
    unknown_splits: list[str] = []
    errors: list[str] = []

    @property
    def ok(self) -> bool:
        return (
            not self.errors
            and not self.duplicate_task_ids
            and not self.tasks_without_reference
            and not self.references_without_task
            and not self.public_tasks_with_forbidden_fields
            and not self.prefix_length_mismatches
            and not self.unknown_splits
        )


def write_task_artifacts(
    public_path: str | Path,
    reference_path: str | Path,
    pairs: Iterable[tuple[PublicDecisionTask, PrivateTaskReference]],
) -> dict[str, object]:
    """Write both artifacts in one streaming pass and return hashes and counts."""
    public_target = Path(public_path)
    reference_target = Path(reference_path)
    public_target.parent.mkdir(parents=True, exist_ok=True)
    reference_target.parent.mkdir(parents=True, exist_ok=True)

    public_digest = hashlib.sha256()
    reference_digest = hashlib.sha256()
    tasks = 0
    by_split: dict[str, int] = {}

    with (
        public_target.open("w", encoding="utf-8", newline="\n") as public_handle,
        reference_target.open("w", encoding="utf-8", newline="\n") as reference_handle,
    ):
        for public, reference in pairs:
            if public.task_id != reference.task_id:
                raise TaskArtifactError("public task and reference ids disagree")
            public_line = json.dumps(
                public.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            reference_line = json.dumps(
                reference.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            public_handle.write(public_line + "\n")
            reference_handle.write(reference_line + "\n")
            public_digest.update(public_line.encode("utf-8"))
            reference_digest.update(reference_line.encode("utf-8"))
            tasks += 1
            by_split[reference.split] = by_split.get(reference.split, 0) + 1

    return {
        "artifact_version": TASK_ARTIFACT_VERSION,
        "task_schema_version": TASK_SCHEMA_VERSION,
        "reference_schema_version": REFERENCE_SCHEMA_VERSION,
        "tasks": tasks,
        "tasks_by_split": dict(sorted(by_split.items())),
        "public_sha256": public_digest.hexdigest(),
        "private_sha256": reference_digest.hexdigest(),
    }


def iter_public_tasks(path: str | Path) -> Iterator[PublicDecisionTask]:
    """Stream agent-visible tasks from an artifact."""
    target = Path(path)
    if not target.is_file():
        raise TaskArtifactError(f"task artifact not found: {target}")
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield PublicDecisionTask.model_validate_json(line)
            except ValidationError as error:
                raise TaskArtifactError(
                    f"invalid public task on line {line_number} of {target.name}"
                ) from error


def iter_references(path: str | Path) -> Iterator[PrivateTaskReference]:
    """Stream evaluator-only references from an artifact."""
    target = Path(path)
    if not target.is_file():
        raise TaskArtifactError(f"reference artifact not found: {target}")
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield PrivateTaskReference.model_validate_json(line)
            except ValidationError as error:
                raise TaskArtifactError(
                    f"invalid private reference on line {line_number} of {target.name}"
                ) from error


class TaskStore:
    """In-memory index over one task artifact directory."""

    def __init__(
        self,
        tasks: dict[str, PublicDecisionTask],
        references: dict[str, PrivateTaskReference],
    ) -> None:
        self._tasks = tasks
        self._references = references

    @classmethod
    def load(cls, directory: str | Path) -> "TaskStore":
        root = Path(directory)
        tasks = {
            task.task_id: task for task in iter_public_tasks(root / PUBLIC_TASKS_FILENAME)
        }
        references = {
            reference.task_id: reference
            for reference in iter_references(root / PRIVATE_REFERENCES_FILENAME)
        }
        return cls(tasks, references)

    def __len__(self) -> int:
        return len(self._tasks)

    @property
    def task_ids(self) -> list[str]:
        return sorted(self._tasks)

    def split_of(self, task_id: str) -> str:
        return self._reference(task_id).split

    def task_ids_for_split(self, split: str) -> list[str]:
        return sorted(
            task_id
            for task_id, reference in self._references.items()
            if reference.split == split
        )

    def counts_by_split(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for reference in self._references.values():
            counts[reference.split] = counts.get(reference.split, 0) + 1
        return dict(sorted(counts.items()))

    def load_public_task(self, task_id: str) -> PublicDecisionTask:
        """Return the agent-visible task object for *task_id*."""
        return self._task(task_id)

    def agent_visible_payload(self, task_id: str) -> dict[str, object]:
        """Return only the fields an evaluated agent may see.

        This is the single entry point the runner uses. It validates the field set
        on every call, so a future schema change cannot silently leak a private
        field into the prompt.
        """
        payload = self._task(task_id).public_payload()
        leaked = set(payload) - PUBLIC_TASK_FIELDS
        if leaked:
            raise TaskArtifactError(
                f"agent-visible payload contains non-public fields: {sorted(leaked)}"
            )
        forbidden = set(payload) & FORBIDDEN_PUBLIC_KEYS
        if forbidden:
            raise TaskArtifactError(
                f"agent-visible payload contains forbidden fields: {sorted(forbidden)}"
            )
        return payload

    def require_reference(self, task_id: str) -> PrivateTaskReference:
        """Return the evaluator reference. Never called on the prompt path."""
        return self._reference(task_id)

    def _task(self, task_id: str) -> PublicDecisionTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskArtifactError(f"unknown task id: {task_id}")
        return task

    def _reference(self, task_id: str) -> PrivateTaskReference:
        reference = self._references.get(task_id)
        if reference is None:
            raise TaskArtifactError(f"no private reference for task: {task_id}")
        return reference


def validate_task_artifacts(
    public_path: str | Path,
    reference_path: str | Path,
) -> ArtifactValidation:
    """Cross-check the public and private artifacts before any run."""
    result = ArtifactValidation()
    public_by_id: dict[str, PublicDecisionTask] = {}
    duplicates: set[str] = set()
    for public in iter_public_tasks(public_path):
        result.public_tasks += 1
        if public.task_id in public_by_id:
            duplicates.add(public.task_id)
        public_by_id[public.task_id] = public
        payload = public.public_payload()
        if set(payload) - PUBLIC_TASK_FIELDS:
            result.public_tasks_with_forbidden_fields.append(public.task_id)
        elif set(payload) & FORBIDDEN_PUBLIC_KEYS:
            result.public_tasks_with_forbidden_fields.append(public.task_id)

    reference_ids: set[str] = set()
    for reference in iter_references(reference_path):
        result.private_references += 1
        if reference.task_id in reference_ids:
            duplicates.add(reference.task_id)
        reference_ids.add(reference.task_id)
        if reference.split not in ALL_SPLITS:
            result.unknown_splits.append(reference.task_id)
        else:
            result.tasks_by_split[reference.split] = (
                result.tasks_by_split.get(reference.split, 0) + 1
            )
        task = public_by_id.get(reference.task_id)
        if task is not None and len(task.visible_context) != (
            reference.cutoff_visible_turn_count
        ):
            result.prefix_length_mismatches.append(reference.task_id)

    public_ids = set(public_by_id)
    result.duplicate_task_ids = sorted(duplicates)
    result.tasks_without_reference = sorted(public_ids - reference_ids)
    result.references_without_task = sorted(reference_ids - public_ids)
    result.tasks_by_split = dict(sorted(result.tasks_by_split.items()))
    if not result.public_tasks:
        result.errors.append("no public tasks found")
    if result.public_tasks != result.private_references:
        result.errors.append("public and private artifact counts differ")
    return result


__all__ = [
    "FORBIDDEN_PUBLIC_KEYS",
    "PRIVATE_REFERENCES_FILENAME",
    "PUBLIC_TASKS_FILENAME",
    "TASK_ARTIFACT_VERSION",
    "TASK_MANIFEST_FILENAME",
    "ArtifactValidation",
    "TaskArtifactError",
    "TaskStore",
    "iter_public_tasks",
    "iter_references",
    "validate_task_artifacts",
    "write_task_artifacts",
]
