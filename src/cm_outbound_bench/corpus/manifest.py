"""Corpus manifest loading and input verification.

The importer never trusts the manifest: every declared hash and count is
recomputed from the actual input files before a single row is written.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

CALLS_FILENAME = "calls.jsonl"
QUARANTINE_FILENAME = "quarantine.jsonl"
MANIFEST_FILENAME = "manifest.json"


class CorpusInputError(RuntimeError):
    """Raised when corpus inputs are missing, malformed or inconsistent."""


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 of a file using bounded memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _FileRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sha256: str = ""
    lines: int | None = None


class _OutputSection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    calls: int = 0
    quarantined: int = 0
    records: int = 0
    speech_records: int = 0
    event_records: int = 0
    files: dict[str, _FileRecord] = Field(default_factory=dict)


class CorpusManifest(BaseModel):
    """The subset of ``manifest.json`` the importer depends on."""

    model_config = ConfigDict(extra="ignore")

    manifest_version: str = ""
    schema_version: str = ""
    dataset: str = ""
    generated_at: str = ""
    output: _OutputSection = Field(default_factory=_OutputSection)

    @property
    def calls_hash(self) -> str:
        record = self.output.files.get(CALLS_FILENAME)
        return record.sha256 if record is not None else ""

    @property
    def quarantine_hash(self) -> str:
        record = self.output.files.get(QUARANTINE_FILENAME)
        return record.sha256 if record is not None else ""


class ManifestValidation(BaseModel):
    """Recomputed facts about the corpus input files."""

    corpus_dir: str
    manifest_path: str
    manifest_hash: str
    calls_path: str
    calls_hash: str
    quarantine_path: str
    quarantine_hash: str
    dataset: str
    manifest_version: str
    manifest_schema_version: str
    expected_units: int
    expected_records: int
    expected_speech_records: int
    expected_event_records: int
    expected_quarantine_units: int
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_manifest(corpus_dir: Path) -> tuple[CorpusManifest, Path, str]:
    """Load the manifest and return it with its path and content hash."""
    manifest_path = corpus_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise CorpusInputError(f"Corpus manifest not found: {manifest_path}")
    raw = manifest_path.read_text(encoding="utf-8")
    try:
        manifest = CorpusManifest.model_validate_json(raw)
    except ValueError as error:
        raise CorpusInputError("Corpus manifest is not valid JSON") from error
    manifest_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return manifest, manifest_path, manifest_hash


def validate_inputs(corpus_dir: Path) -> ManifestValidation:
    """Recompute every declared hash and count-related input fact."""
    manifest, manifest_path, manifest_hash = load_manifest(corpus_dir)
    calls_path = corpus_dir / CALLS_FILENAME
    quarantine_path = corpus_dir / QUARANTINE_FILENAME

    errors: list[str] = []
    for path in (calls_path, quarantine_path):
        if not path.is_file():
            raise CorpusInputError(f"Corpus input file not found: {path}")

    calls_hash = sha256_file(calls_path)
    quarantine_hash = sha256_file(quarantine_path)

    if not manifest.calls_hash:
        errors.append("manifest_missing_calls_hash")
    elif manifest.calls_hash != calls_hash:
        errors.append("calls_hash_mismatch")
    if not manifest.quarantine_hash:
        errors.append("manifest_missing_quarantine_hash")
    elif manifest.quarantine_hash != quarantine_hash:
        errors.append("quarantine_hash_mismatch")

    declared_lines = manifest.output.files.get(CALLS_FILENAME)
    if declared_lines is not None and declared_lines.lines is not None:
        actual_lines = _count_lines(calls_path)
        if declared_lines.lines != actual_lines:
            errors.append("calls_line_count_mismatch")
    quarantine_lines = manifest.output.files.get(QUARANTINE_FILENAME)
    if quarantine_lines is not None and quarantine_lines.lines is not None:
        actual_quarantine = _count_lines(quarantine_path)
        if quarantine_lines.lines != actual_quarantine:
            errors.append("quarantine_line_count_mismatch")

    return ManifestValidation(
        corpus_dir=str(corpus_dir),
        manifest_path=str(manifest_path),
        manifest_hash=manifest_hash,
        calls_path=str(calls_path),
        calls_hash=calls_hash,
        quarantine_path=str(quarantine_path),
        quarantine_hash=quarantine_hash,
        dataset=manifest.dataset,
        manifest_version=manifest.manifest_version,
        manifest_schema_version=manifest.schema_version,
        expected_units=manifest.output.calls,
        expected_records=manifest.output.records,
        expected_speech_records=manifest.output.speech_records,
        expected_event_records=manifest.output.event_records,
        expected_quarantine_units=manifest.output.quarantined,
        errors=sorted(set(errors)),
    )


def _count_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count
