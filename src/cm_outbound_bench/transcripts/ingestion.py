"""Read the de-identified transcript corpus in its real on-disk format.

Supported input is exactly the published ``cm_outbound_v2`` corpus layout:

* ``calls.jsonl`` - one JSON object per delivered transcript unit;
* ``quarantine.jsonl`` - withheld units, metadata only;
* ``manifest.json`` - corpus version, counts and hashes.

Project policy (``docs/data_privacy.md`` §2.1) forbids benchmark code from
opening the confidential raw ``data/TXT`` export. This module therefore reads only
the de-identified JSONL. The raw TXT field layout is documented in the format
audit report; it is not parsed here and no raw text can reach the benchmark.

The reader is strict on purpose: an unexpected type or a missing field is a data
error, never silently coerced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

CALLS_FILENAME = "calls.jsonl"
QUARANTINE_FILENAME = "quarantine.jsonl"
MANIFEST_FILENAME = "manifest.json"

#: Payload types the corpus documents. Anything else is a data error.
PAYLOAD_SPEECH = "SPEECH"
PAYLOAD_EVENT = "EVENT"
_PAYLOAD_TYPES = {PAYLOAD_SPEECH, PAYLOAD_EVENT}

_REQUIRED_RECORD_FIELDS = (
    "seq",
    "start_ms",
    "end_ms",
    "channel_id",
    "role",
    "payload_type",
    "event",
    "text_masked",
    "is_truncated",
    "asr_flags",
    "redaction_flags",
)


class TranscriptCorpusError(RuntimeError):
    """A structural corpus problem that must stop the pipeline."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SourceRecord:
    """One original corpus record, typed and checked."""

    seq: int
    start_ms: int
    end_ms: int
    channel_id: int
    payload_type: str
    event: str | None
    text: str
    is_truncated: bool
    asr_flags: tuple[str, ...]
    redaction_flags: tuple[str, ...]

    @property
    def is_speech(self) -> bool:
        return self.payload_type == PAYLOAD_SPEECH


@dataclass(frozen=True)
class TranscriptUnit:
    """One delivered transcript unit, which the format audit equates to a call."""

    transcript_id: str
    schema_version: str
    duration_ms: int
    records: tuple[SourceRecord, ...]


def resolve_calls_path(corpus_dir: str | Path) -> Path:
    """Return the ``calls.jsonl`` path inside a corpus directory."""
    return Path(corpus_dir).expanduser() / CALLS_FILENAME


def read_dataset_manifest(corpus_dir: str | Path) -> dict[str, Any]:
    """Return the parsed corpus manifest, or an empty dict when absent."""
    path = Path(corpus_dir).expanduser() / MANIFEST_FILENAME
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TranscriptCorpusError(
            "invalid_manifest", "The corpus manifest could not be read as JSON."
        ) from error
    if not isinstance(payload, dict):
        raise TranscriptCorpusError("invalid_manifest", "The manifest is not an object.")
    return payload


def dataset_version(corpus_dir: str | Path) -> str:
    """Return the corpus dataset version, defaulting to the directory name."""
    manifest = read_dataset_manifest(corpus_dir)
    value = manifest.get("dataset")
    if isinstance(value, str) and value:
        return value
    return Path(corpus_dir).expanduser().name


def iter_units(corpus_dir: str | Path) -> Iterator[TranscriptUnit]:
    """Stream every transcript unit from ``calls.jsonl``.

    The file is streamed: a malformed line fails the run instead of producing a
    partial conversation, and no whole-file copy is held in memory.
    """
    path = resolve_calls_path(corpus_dir)
    if not path.is_file():
        raise TranscriptCorpusError(
            "corpus_not_found", f"De-identified corpus not found: {path}"
        )
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as error:  # pragma: no cover - filesystem dependent
        raise TranscriptCorpusError(
            "corpus_unreadable", "The corpus file could not be opened."
        ) from error
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            yield _parse_unit(line, path.name, line_number)


def _parse_unit(line: str, filename: str, line_number: int) -> TranscriptUnit:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as error:
        raise TranscriptCorpusError(
            "invalid_json", f"Invalid JSON on line {line_number} of {filename}."
        ) from error
    if not isinstance(payload, dict):
        raise TranscriptCorpusError(
            "invalid_unit", f"Line {line_number} of {filename} is not an object."
        )

    transcript_id = payload.get("transcript_id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptCorpusError(
            "missing_transcript_id", f"Line {line_number} has no transcript_id."
        )
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise TranscriptCorpusError(
            "missing_schema_version", f"{transcript_id} has no schema_version."
        )
    duration_ms = _require_int(payload.get("duration_ms"), "duration_ms", transcript_id)
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise TranscriptCorpusError(
            "missing_records", f"{transcript_id} has no records list."
        )

    records = tuple(
        _parse_record(record, transcript_id, index)
        for index, record in enumerate(raw_records)
    )
    return TranscriptUnit(
        transcript_id=transcript_id,
        schema_version=schema_version,
        duration_ms=duration_ms,
        records=records,
    )


def _parse_record(record: Any, transcript_id: str, index: int) -> SourceRecord:
    if not isinstance(record, dict):
        raise TranscriptCorpusError(
            "invalid_record", f"{transcript_id} record {index} is not an object."
        )
    missing = [field for field in _REQUIRED_RECORD_FIELDS if field not in record]
    if missing:
        raise TranscriptCorpusError(
            "missing_record_field",
            f"{transcript_id} record {index} is missing: {', '.join(sorted(missing))}.",
        )

    payload_type = record["payload_type"]
    if not isinstance(payload_type, str) or payload_type not in _PAYLOAD_TYPES:
        raise TranscriptCorpusError(
            "unknown_payload_type",
            f"{transcript_id} record {index} has an unknown payload_type.",
        )

    event = record["event"]
    if event is not None and not isinstance(event, str):
        raise TranscriptCorpusError(
            "invalid_event", f"{transcript_id} record {index} has an invalid event."
        )

    text = record["text_masked"]
    if text is None:
        # Event records legitimately carry no text. A speech record without text
        # is not a crash, but it must not look like an utterance.
        text = ""
    if not isinstance(text, str):
        raise TranscriptCorpusError(
            "invalid_text", f"{transcript_id} record {index} has a non-string text."
        )

    is_truncated = record["is_truncated"]
    if not isinstance(is_truncated, bool):
        raise TranscriptCorpusError(
            "invalid_truncation_flag",
            f"{transcript_id} record {index} has a non-boolean is_truncated.",
        )

    return SourceRecord(
        seq=_require_int(record["seq"], "seq", transcript_id),
        start_ms=_require_int(record["start_ms"], "start_ms", transcript_id),
        end_ms=_require_int(record["end_ms"], "end_ms", transcript_id),
        channel_id=_require_int(record["channel_id"], "channel_id", transcript_id),
        payload_type=payload_type,
        event=event,
        text=text,
        is_truncated=is_truncated,
        asr_flags=_require_flags(record["asr_flags"], "asr_flags", transcript_id),
        redaction_flags=_require_flags(
            record["redaction_flags"], "redaction_flags", transcript_id
        ),
    )


def _require_int(value: Any, field: str, transcript_id: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TranscriptCorpusError(
            "invalid_field_type", f"{transcript_id} field {field!r} is not an integer."
        )
    return value


def _require_flags(value: Any, field: str, transcript_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TranscriptCorpusError(
            "invalid_field_type",
            f"{transcript_id} field {field!r} is not a list of strings.",
        )
    return tuple(value)


__all__ = [
    "CALLS_FILENAME",
    "MANIFEST_FILENAME",
    "PAYLOAD_EVENT",
    "PAYLOAD_SPEECH",
    "QUARANTINE_FILENAME",
    "SourceRecord",
    "TranscriptCorpusError",
    "TranscriptUnit",
    "dataset_version",
    "iter_units",
    "read_dataset_manifest",
    "resolve_calls_path",
]
