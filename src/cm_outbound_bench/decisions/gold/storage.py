"""Checkpointed, resumable, idempotent storage for the gold pipeline.

Every API record is keyed by a deterministic cache key over (task id, dataset
version, task artifact hash, model identifier, prompt hash, configuration hash,
pass). A completed record is reused across runs; a stale record whose key no
longer matches the current inputs is never silently reused.

Frozen artifacts are written once with a SHA-256 manifest; a freeze refuses to
overwrite an existing frozen version.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from cm_outbound_bench.decisions.gold.models import (
    AdjudicationResult,
    FinalGoldState,
    PassComparison,
    PassRecord,
)

PILOT_DIRNAME = "pilot"
FEEDBACK_DIRNAME = "feedback"
MANIFESTS_DIRNAME = "manifests"
RECORDS_DIRNAME = "records"

FROZEN_MANIFEST_FILENAME = "frozen_annotation_manifest.json"
RUN_MANIFEST_FILENAME = "annotation_run_manifest.json"
OVERLAP_REPORT_FILENAME = "annotation_overlap_report.json"
FINAL_REFERENCES_FILENAME = "final_references.jsonl"
ANNOTATION_SUMMARY_FILENAME = "annotation_summary.json"

ARTIFACT_ROOT = "outputs/decision/annotations"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class StorageError(RuntimeError):
    """Raised when gold artifacts cannot be read or written safely."""


class GoldStorage:
    """Directory-backed store for pass records, adjudications and finals."""

    def __init__(
        self,
        root: Path,
        *,
        dataset_version: str,
        tasks_artifact_sha256: str,
    ) -> None:
        self._root = Path(root)
        self.dataset_version = dataset_version
        self.tasks_artifact_sha256 = tasks_artifact_sha256

    @classmethod
    def default(cls, *, dataset_version: str, tasks_artifact_sha256: str) -> "GoldStorage":
        return cls(
            Path(ARTIFACT_ROOT),
            dataset_version=dataset_version,
            tasks_artifact_sha256=tasks_artifact_sha256,
        )

    # -- layout -----------------------------------------------------------

    def _split_root(self, split: str) -> Path:
        return self._root / split

    def split_root(self, split: str) -> Path:
        """The public root directory for one split's frozen artifacts."""
        return self._split_root(split)

    def stage_dir(self, split: str, stage: str) -> Path:
        """stage is ``pilot``, ``feedback`` or ``main``."""
        return self._split_root(split) / stage

    def records_dir(self, split: str, stage: str) -> Path:
        return self.stage_dir(split, stage) / RECORDS_DIRNAME

    def pass_dir(self, split: str, stage: str, pass_id: str) -> Path:
        return self.records_dir(split, stage) / f"pass_{pass_id.casefold()}"

    def adjudications_path(self, split: str, stage: str) -> Path:
        return self.stage_dir(split, stage) / "adjudications.jsonl"

    def comparisons_path(self, split: str, stage: str) -> Path:
        return self.stage_dir(split, stage) / "comparisons.jsonl"

    def finals_path(self, split: str, stage: str) -> Path:
        return self.stage_dir(split, stage) / "finals.jsonl"

    def manifests_dir(self) -> Path:
        return self._root / MANIFESTS_DIRNAME

    # -- cache keys -------------------------------------------------------

    def cache_key(self, task_id: str, *, pass_id: str, model: str, prompt_hash: str, config_hash: str) -> str:
        payload = "\n".join(
            [
                task_id,
                self.dataset_version,
                self.tasks_artifact_sha256,
                model,
                prompt_hash,
                config_hash,
                pass_id,
            ]
        )
        return sha256_text(payload)

    # -- pass records -----------------------------------------------------

    def record_path(self, split: str, stage: str, task_id: str, pass_id: str) -> Path:
        return self.pass_dir(split, stage, pass_id) / f"{task_id}.json"

    def has_pass(self, split: str, stage: str, task_id: str, pass_id: str) -> bool:
        return self.record_path(split, stage, task_id, pass_id).is_file()

    def load_pass(
        self,
        split: str,
        stage: str,
        task_id: str,
        pass_id: str,
        *,
        expected_cache_key: str,
    ) -> PassRecord | None:
        """Return a cached record only when its cache key is still current."""
        path = self.record_path(split, stage, task_id, pass_id)
        if not path.is_file():
            return None
        try:
            record = PassRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as error:
            raise StorageError(f"corrupt pass record {path}: {error}") from error
        if record.cache_key != expected_cache_key:
            return None  # stale: configuration or prompt changed
        return record

    def save_pass(self, split: str, stage: str, record: PassRecord) -> Path:
        target = self.record_path(split, stage, record.task_id, record.pass_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        target.write_text(serialized + "\n", encoding="utf-8")
        return target

    def adjudication_record_path(
        self, split: str, stage: str, task_id: str
    ) -> Path:
        return self.records_dir(split, stage) / "adjudications" / f"{task_id}.json"

    def load_adjudication_record(
        self,
        split: str,
        stage: str,
        task_id: str,
        *,
        expected_cache_key: str,
    ) -> AdjudicationResult | None:
        """Return a cached adjudication only when its cache key is current."""
        path = self.adjudication_record_path(split, stage, task_id)
        if not path.is_file():
            return None
        try:
            record = AdjudicationResult.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception as error:
            raise StorageError(f"corrupt adjudication record {path}: {error}") from error
        if record.cache_key != expected_cache_key:
            return None
        return record

    def save_adjudication_record(
        self, split: str, stage: str, record: AdjudicationResult
    ) -> Path:
        target = self.adjudication_record_path(split, stage, record.task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return target

    def iter_pass_records(self, split: str, stage: str) -> Iterator[PassRecord]:
        for path in sorted(self.records_dir(split, stage).glob("pass_*/*.json")):
            yield PassRecord.model_validate_json(path.read_text(encoding="utf-8"))

    # -- comparisons / adjudications / finals ------------------------------

    def save_comparison(self, split: str, stage: str, comparison: PassComparison) -> None:
        self._append_jsonl(self.comparisons_path(split, stage), comparison)

    def save_adjudication(
        self, split: str, stage: str, result: AdjudicationResult
    ) -> None:
        self._append_jsonl(self.adjudications_path(split, stage), result)

    def save_final(self, split: str, stage: str, final: FinalGoldState) -> None:
        self._append_jsonl(self.finals_path(split, stage), final)

    @staticmethod
    def _append_jsonl(path: Path, record: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    existing.add(json.loads(line).get("task_id"))
                except json.JSONDecodeError:
                    continue
        serialized = json.dumps(
            record.model_dump(mode="json") if hasattr(record, "model_dump") else record,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if getattr(record, "task_id", None) in existing:
            return  # idempotent: never append a second row for one task
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")

    def rewrite_jsonl(self, path: Path, records: Iterable[object]) -> None:
        """Write a JSONL artifact wholesale from a full set of records.

        Used by the re-comparison pass so a refined comparison rubric replaces
        the previous rows atomically instead of appending conflicting ones.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(
                record.model_dump(mode="json") if hasattr(record, "model_dump") else record,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for record in sorted(
                records, key=lambda item: getattr(item, "task_id", "")
            )
        ]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    # -- iteration ---------------------------------------------------------

    def iter_jsonl(self, path: Path, model: type) -> Iterator[object]:
        if not path.is_file():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            yield model.model_validate_json(line)


__all__ = [
    "ANNOTATION_SUMMARY_FILENAME",
    "ARTIFACT_ROOT",
    "FEEDBACK_DIRNAME",
    "FINAL_REFERENCES_FILENAME",
    "FROZEN_MANIFEST_FILENAME",
    "MANIFESTS_DIRNAME",
    "OVERLAP_REPORT_FILENAME",
    "PILOT_DIRNAME",
    "RECORDS_DIRNAME",
    "RUN_MANIFEST_FILENAME",
    "GoldStorage",
    "StorageError",
    "sha256_file",
    "sha256_text",
    "utc_now",
]
