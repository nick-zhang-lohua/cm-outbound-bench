"""Neutral fixtures for the decision-point benchmark tests.

Every string in this module is a **neutral placeholder**. It is not China Mobile
data, not a business policy and not a benchmark task, and it must never be
presented as one. The fixtures exist only to exercise the pipeline mechanics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from cm_outbound_bench.decisions.builder import BuilderConfig, DecisionTaskBuilder
from cm_outbound_bench.decisions.schema import PrivateTaskReference, PublicDecisionTask
from cm_outbound_bench.decisions.store import TaskStore, write_task_artifacts
from cm_outbound_bench.transcripts.ingestion import iter_units
from cm_outbound_bench.transcripts.models import NormalizedCall
from cm_outbound_bench.transcripts.reconstruction import reconstruct_calls
from cm_outbound_bench.transcripts.splitting import (
    SplitManifest,
    SplitTargets,
    assign_splits,
)
from cm_outbound_bench.transcripts.validation import CallFingerprint, CorpusValidator

FIXTURE_CORPUS = "neutral_test_fixture"
FIXTURE_SCHEMA = "1.0"

#: CJK characters used only to make one fixture call textually distinct from the
#: next. Digits are stripped by the SimHash fingerprint on purpose, so a fixture
#: that varies only digits would look like a duplicate conversation.
_DISTINCT_CHARS = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黄宇月盈昃辰宿列张"


def distinct_label(index: int) -> str:
    """Return a distinct CJK label for fixture call *index*."""
    size = len(_DISTINCT_CHARS)
    return "".join(
        _DISTINCT_CHARS[(index // (size**power)) % size] for power in range(3)
    )


def record(
    seq: int,
    start_ms: int,
    end_ms: int,
    channel: int,
    text: str | None,
    *,
    payload_type: str = "SPEECH",
    event: str | None = None,
    truncated: bool = False,
) -> dict:
    return {
        "seq": seq,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "channel_id": channel,
        "role": "UNKNOWN",
        "payload_type": payload_type,
        "event": event,
        "text_masked": text,
        "is_truncated": truncated,
        "asr_flags": [],
        "redaction_flags": [],
    }


def dialogue(
    utterances: Sequence[tuple[int, str]],
    *,
    start_ms: int = 1000,
    step_ms: int = 2000,
) -> list[dict]:
    """Build a clean dialogue from ``(channel, text)`` pairs."""
    records: list[dict] = []
    for index, (channel, text) in enumerate(utterances):
        start = start_ms + index * step_ms
        records.append(record(index, start, start + step_ms - 200, channel, text))
    return records


def unit(
    transcript_id: str,
    records: Sequence[dict],
    *,
    duration_ms: int | None = None,
) -> dict:
    if duration_ms is None:
        duration_ms = max((item["end_ms"] for item in records), default=0) + 500
    return {
        "schema_version": FIXTURE_SCHEMA,
        "transcript_id": transcript_id,
        "duration_ms": duration_ms,
        "records": list(records),
    }


def write_corpus(root: Path, units: Iterable[dict]) -> Path:
    """Write a minimal neutral corpus directory."""
    root.mkdir(parents=True, exist_ok=True)
    materialized = list(units)
    with (root / "calls.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for item in materialized:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    (root / "quarantine.jsonl").write_text("", encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": "2.0",
                "schema_version": "1.0",
                "pipeline_version": "test",
                "dataset": FIXTURE_CORPUS,
                "generated_at": "2026-01-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def load_calls(root: Path) -> list[NormalizedCall]:
    return list(
        reconstruct_calls(
            iter_units(root), corpus=FIXTURE_CORPUS, corpus_schema_version=FIXTURE_SCHEMA
        )
    )


def call_from_records(transcript_id: str, records: Sequence[dict]) -> NormalizedCall:
    """Materialize one call in memory, without writing a corpus."""
    from cm_outbound_bench.transcripts.ingestion import SourceRecord, TranscriptUnit
    from cm_outbound_bench.transcripts.reconstruction import reconstruct_call

    source = TranscriptUnit(
        transcript_id=transcript_id,
        schema_version=FIXTURE_SCHEMA,
        duration_ms=max((item["end_ms"] for item in records), default=0) + 500,
        records=tuple(
            SourceRecord(
                seq=item["seq"],
                start_ms=item["start_ms"],
                end_ms=item["end_ms"],
                channel_id=item["channel_id"],
                payload_type=item["payload_type"],
                event=item["event"],
                text=item["text_masked"] or "",
                is_truncated=item["is_truncated"],
                asr_flags=tuple(item["asr_flags"]),
                redaction_flags=tuple(item["redaction_flags"]),
            )
            for item in records
        ),
    )
    return reconstruct_call(
        source, corpus=FIXTURE_CORPUS, corpus_schema_version=FIXTURE_SCHEMA
    )


def call_from(transcript_id: str, utterances: Sequence[tuple[int, str]]) -> NormalizedCall:
    return call_from_records(transcript_id, dialogue(utterances))


def fingerprints(calls: Sequence[NormalizedCall], max_hamming: int = 3) -> list[CallFingerprint]:
    validator = CorpusValidator(near_duplicate_max_hamming=max_hamming)
    for call in calls:
        validator.add(call)
    return list(validator.fingerprints)


def manifest_for(
    calls: Sequence[NormalizedCall],
    *,
    seed: int = 7,
    max_hamming: int = 3,
) -> SplitManifest:
    return assign_splits(
        fingerprints(calls, max_hamming=max_hamming),
        seed=seed,
        targets=SplitTargets(),
        near_duplicate_max_hamming=max_hamming,
        dataset_version=FIXTURE_CORPUS,
    )


def build_tasks(
    calls: Sequence[NormalizedCall],
    split_manifest: SplitManifest,
    root: Path,
    *,
    config: BuilderConfig | None = None,
) -> tuple[TaskStore, list[PublicDecisionTask], list[PrivateTaskReference]]:
    builder = DecisionTaskBuilder(config)
    pairs: list[tuple[PublicDecisionTask, PrivateTaskReference]] = []
    for call in calls:
        split = split_manifest.split_by_call.get(call.call_id)
        if split is None:
            continue
        pairs.extend(builder.add_call(call, split=split))
    write_task_artifacts(
        root / "tasks.jsonl", root / "references.jsonl", iter(pairs)
    )
    return TaskStore.load(root), [public for public, _ in pairs], [
        reference for _, reference in pairs
    ]


def alternating_dialogue(turns: int = 12) -> list[dict]:
    """A neutral alternating agent/customer dialogue."""
    utterances: list[tuple[int, str]] = []
    for index in range(turns):
        channel = 1 if index % 2 == 0 else 2
        label = "占位客服语句" if channel == 1 else "占位客户语句"
        utterances.append((channel, f"{label}{index + 1}"))
    return dialogue(utterances)


__all__ = [
    "FIXTURE_CORPUS",
    "alternating_dialogue",
    "build_tasks",
    "call_from",
    "call_from_records",
    "dialogue",
    "distinct_label",
    "fingerprints",
    "load_calls",
    "manifest_for",
    "record",
    "unit",
    "write_corpus",
]
