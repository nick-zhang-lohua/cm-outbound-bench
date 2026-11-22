"""Synthetic corpus fixtures.

All content here is invented test data. It deliberately contains no transcript
material from the real corpus.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: Marker string used by corpus tests to prove that transcript text never
#: reaches default command output.
PRIVATE_MARKER = "PRIVATE_MARKER_SHOULD_NOT_LEAK"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(
    seq: int,
    *,
    payload_type: str,
    channel_id: int,
    role: str = "UNKNOWN",
    start_ms: int | None = None,
    text: str | None = None,
    event: str | None = None,
    is_truncated: bool = False,
    asr_flags: list[str] | None = None,
    redaction_flags: list[str] | None = None,
) -> dict[str, object]:
    start = start_ms if start_ms is not None else seq * 1000
    return {
        "seq": seq,
        "start_ms": start,
        "end_ms": start + 900,
        "channel_id": channel_id,
        "role": role,
        "payload_type": payload_type,
        "event": event,
        "text_masked": text,
        "is_truncated": is_truncated,
        "asr_flags": asr_flags or [],
        "redaction_flags": redaction_flags or [],
    }


def build_corpus(
    root: Path,
    *,
    units: int = 3,
    records_per_unit: int = 3,
    quarantine_units: int = 2,
) -> Path:
    """Write a tiny, fully synthetic corpus directory and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    calls_path = root / "calls.jsonl"
    quarantine_path = root / "quarantine.jsonl"

    records_total = 0
    speech_total = 0
    event_total = 0
    calls_lines: list[str] = []
    for unit_index in range(units):
        transcript_id = f"SYNTH-CALL-{unit_index:04d}"
        records = []
        for seq in range(records_per_unit):
            is_event = seq % 3 == 1
            records.append(
                _record(
                    seq,
                    payload_type="EVENT" if is_event else "SPEECH",
                    channel_id=1 if unit_index % 2 == 0 else 2,
                    text=(
                        None
                        if is_event
                        else f"{PRIVATE_MARKER} utterance {unit_index}-{seq}"
                    ),
                    event="SYSTEM" if is_event else None,
                    is_truncated=seq == 0,
                    asr_flags=["yen_token"] if is_event else [],
                    redaction_flags=["PHONE"] if seq == 2 else [],
                )
            )
            if is_event:
                event_total += 1
            else:
                speech_total += 1
        records_total += len(records)
        calls_lines.append(
            json.dumps(
                {
                    "transcript_id": transcript_id,
                    "schema_version": "1.0",
                    "duration_ms": 900 * len(records) + 500,
                    "records": records,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    calls_path.write_text("\n".join(calls_lines) + "\n", encoding="utf-8")

    quarantine_lines: list[str] = []
    for index in range(quarantine_units):
        quarantine_lines.append(
            json.dumps(
                {
                    "transcript_id": f"SYNTH-BLOCKED-{index:04d}",
                    "reason_codes": (
                        ["AMBIGUOUS_PHONE"]
                        if index % 2 == 0
                        else ["PRIVACY_RESIDUAL", "ADDRESS_AMBIGUOUS"]
                    ),
                    "affected_seq": [1, 2],
                    "status": "QUARANTINED",
                },
                separators=(",", ":"),
            )
        )
    quarantine_path.write_text("\n".join(quarantine_lines) + "\n", encoding="utf-8")

    manifest = {
        "manifest_version": "2.0",
        "schema_version": "1.0",
        "dataset": "synthetic_corpus_test",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "output": {
            "calls": units,
            "quarantined": quarantine_units,
            "records": records_total,
            "speech_records": speech_total,
            "event_records": event_total,
            "files": {
                "calls.jsonl": {"sha256": sha256_file(calls_path), "lines": units},
                "quarantine.jsonl": {
                    "sha256": sha256_file(quarantine_path),
                    "lines": quarantine_units,
                },
            },
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return root


def rewrite_manifest(root: Path, **overrides: object) -> None:
    """Rewrite declared manifest values, leaving file hashes unchanged."""
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output"].update(overrides)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_text_corpus(root: Path, unit_texts: list[list[str]]) -> Path:
    """Build a corpus whose speech records carry caller-supplied synthetic text.

    Every record is a ``SPEECH`` record so classification tests control the
    exact lexical evidence. The supplied strings must be invented test text.
    """
    root.mkdir(parents=True, exist_ok=True)
    calls_path = root / "calls.jsonl"
    quarantine_path = root / "quarantine.jsonl"

    records_total = 0
    calls_lines: list[str] = []
    for unit_index, texts in enumerate(unit_texts):
        transcript_id = f"SYNTH-TEXT-{unit_index:04d}"
        records = [
            _record(seq, payload_type="SPEECH", channel_id=1, text=text)
            for seq, text in enumerate(texts)
        ]
        records_total += len(records)
        calls_lines.append(
            json.dumps(
                {
                    "transcript_id": transcript_id,
                    "schema_version": "1.0",
                    "duration_ms": 1000 * len(records),
                    "records": records,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    calls_path.write_text("\n".join(calls_lines) + "\n", encoding="utf-8")
    quarantine_path.write_text("", encoding="utf-8")

    manifest = {
        "manifest_version": "2.0",
        "schema_version": "1.0",
        "dataset": "synthetic_text_corpus",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "output": {
            "calls": len(unit_texts),
            "quarantined": 0,
            "records": records_total,
            "speech_records": records_total,
            "event_records": 0,
            "files": {
                "calls.jsonl": {
                    "sha256": sha256_file(calls_path),
                    "lines": len(unit_texts),
                },
                "quarantine.jsonl": {
                    "sha256": sha256_file(quarantine_path),
                    "lines": 0,
                },
            },
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return root


def read_manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))
