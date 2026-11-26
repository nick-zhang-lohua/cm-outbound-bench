"""Read and write the reconstructed-call artifact as JSON Lines.

The artifact lives outside version control because it contains de-identified but
still confidential conversation text. A companion manifest records the counts and
the SHA-256 of the file so a later stage can prove which exact input it used.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from pydantic import ValidationError

from cm_outbound_bench.transcripts.models import NormalizedCall
from cm_outbound_bench.transcripts.validation import CallFingerprint

CALL_ARTIFACT_VERSION = "1.0"
CALLS_ARTIFACT_FILENAME = "calls.jsonl"
CALLS_MANIFEST_FILENAME = "calls.manifest.json"


class CallArtifactError(RuntimeError):
    """Raised when a call artifact is missing or inconsistent."""


def write_calls(
    path: str | Path,
    calls: Iterable[NormalizedCall],
) -> dict[str, object]:
    """Write calls as JSON Lines and return a small artifact manifest."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    valid = 0
    suspect = 0
    invalid = 0
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for call in calls:
            line = json.dumps(
                call.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            handle.write(line + "\n")
            digest.update(line.encode("utf-8"))
            count += 1
            if call.data_status == "valid":
                valid += 1
            elif call.data_status == "suspect":
                suspect += 1
            else:
                invalid += 1
    return {
        "artifact_version": CALL_ARTIFACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calls": count,
        "calls_valid": valid,
        "calls_suspect": suspect,
        "calls_invalid": invalid,
        "sha256": digest.hexdigest(),
        "path": target.name,
    }


def iter_calls(path: str | Path) -> Iterator[NormalizedCall]:
    """Stream normalized calls from a JSON Lines artifact."""
    target = Path(path)
    if not target.is_file():
        raise CallArtifactError(f"call artifact not found: {target}")
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield NormalizedCall.model_validate_json(line)
            except ValidationError as error:
                raise CallArtifactError(
                    f"invalid call on line {line_number} of {target.name}: "
                    f"{error.error_count()} validation errors"
                ) from error


def iter_fingerprints(path: str | Path) -> Iterator[CallFingerprint]:
    """Stream only the compact fields needed for splitting.

    The full conversation is never materialized here, so a split over thousands of
    calls stays small in memory and cannot accidentally print conversation text.
    """
    target = Path(path)
    if not target.is_file():
        raise CallArtifactError(f"call artifact not found: {target}")
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise CallArtifactError(
                    f"invalid JSON on line {line_number} of {target.name}"
                ) from error
            flags = payload.get("quality_flags") or []
            yield CallFingerprint(
                call_id=payload["call_id"],
                data_status=payload.get("data_status", "valid"),
                content_sha256=payload["content_sha256"],
                text_simhash=payload.get("text_simhash", "0000000000000000"),
                speech_record_count=int(payload.get("speech_record_count", 0)),
                duration_ms=int(payload.get("duration_ms", 0)),
                has_customer_speech="no_customer_speech" not in flags,
                has_agent_speech="no_agent_speech" not in flags,
                business_domain=payload.get("business_domain", "unknown"),
            )


def sha256_file(path: str | Path) -> str:
    """Return the hex SHA-256 of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "CALLS_ARTIFACT_FILENAME",
    "CALLS_MANIFEST_FILENAME",
    "CALL_ARTIFACT_VERSION",
    "CallArtifactError",
    "iter_calls",
    "iter_fingerprints",
    "sha256_file",
    "write_calls",
]
