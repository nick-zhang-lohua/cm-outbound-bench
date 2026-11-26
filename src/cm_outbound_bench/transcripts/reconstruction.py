"""Reconstruct complete two-channel conversations from transcript units.

Pipeline position: ``ingestion`` -> **reconstruction** -> ``validation``.

Reconstruction does four things and nothing else:

1. checks the structural invariants of a unit (contiguous ``seq``, non-negative
   timing, ``end_ms >= start_ms``);
2. orders records by ``(start_ms, seq)`` so a timestamp tie preserves the
   original source order;
3. maps channel 1 to the historical agent and channel 2 to the customer, leaving
   any other channel explicitly ``other`` instead of guessing;
4. keeps every record on the timeline, including non-speech events, and records
   whether a record overlaps the previous one instead of flattening it.

Simultaneous or overlapping speech is preserved as an overlap flag. The
conversation is never rewritten into an artificial strict alternation.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from cm_outbound_bench.transcripts.fingerprint import fingerprint_hex, simhash64
from cm_outbound_bench.transcripts.ingestion import (
    PAYLOAD_EVENT,
    PAYLOAD_SPEECH,
    TranscriptUnit,
)
from cm_outbound_bench.transcripts.models import (
    CHANNEL_CUSTOMER,
    CHANNEL_HISTORICAL_AGENT,
    NormalizedCall,
    SourceRef,
    Turn,
    call_content_sha256,
    format_offset_timestamp,
    role_for_channel,
)

RECONSTRUCTION_VERSION = "1.0"

#: A clock that jumps back by more than this after the call has already run for
#: more than twice the threshold looks like a restart, not a scheduling artefact.
TIMING_RESET_THRESHOLD_MS = 30_000

#: Flags that make a unit unusable as a conversation.
STRUCTURAL_FLAGS = frozenset(
    {
        "no_records",
        "negative_timing",
        "end_before_start",
        "records_noncontiguous_seq",
        "no_speech",
    }
)

#: Flags that keep a unit usable but mark it for review.
SUSPECT_FLAGS = frozenset(
    {
        "duration_mismatch",
        "timing_inversion",
        "timing_reset",
    }
)


def reconstruct_call(
    unit: TranscriptUnit,
    *,
    corpus: str,
    corpus_schema_version: str,
) -> NormalizedCall:
    """Reconstruct one complete conversation from its transcript unit."""
    flags: set[str] = set()

    if not unit.records:
        flags.add("no_records")

    seq_contiguous = all(record.seq == index for index, record in enumerate(unit.records))
    if not seq_contiguous:
        flags.add("records_noncontiguous_seq")

    if any(
        record.start_ms < 0 or record.end_ms < 0 for record in unit.records
    ):
        flags.add("negative_timing")
    if any(record.end_ms < record.start_ms for record in unit.records):
        flags.add("end_before_start")

    ordered = sorted(unit.records, key=lambda record: (record.start_ms, record.seq))

    previous_start = -1
    for record in unit.records:
        if record.start_ms < previous_start:
            flags.add("timing_inversion")
            break
        previous_start = record.start_ms

    previous = -1
    for record in unit.records:
        if (
            record.start_ms < previous - TIMING_RESET_THRESHOLD_MS
            and previous > TIMING_RESET_THRESHOLD_MS * 2
        ):
            flags.add("timing_reset")
            break
        previous = max(previous, record.start_ms)

    max_end = max((record.end_ms for record in ordered), default=0)
    if unit.duration_ms < max_end:
        flags.add("duration_mismatch")

    speech_records = [record for record in ordered if record.payload_type == PAYLOAD_SPEECH]
    if not speech_records:
        flags.add("no_speech")

    turns: list[Turn] = []
    previous_end = -1
    overlaps = False
    for index, record in enumerate(ordered, start=1):
        overlap = record.start_ms < previous_end
        overlaps = overlaps or overlap
        turns.append(
            Turn(
                turn_id=index,
                seq=record.seq,
                start_ms=record.start_ms,
                end_ms=record.end_ms,
                timestamp=format_offset_timestamp(record.start_ms),
                channel=record.channel_id,
                speaker_role=role_for_channel(record.channel_id),  # type: ignore[arg-type]
                record_type="event" if record.payload_type == PAYLOAD_EVENT else "speech",
                event=record.event,
                text=record.text,
                is_truncated=record.is_truncated,
                source_segment_id=f"{unit.transcript_id}:{record.seq}",
                overlaps_previous=overlap,
            )
        )
        previous_end = max(previous_end, record.end_ms)

    if overlaps:
        flags.add("overlapping_records")
    if not any(
        turn.record_type == "speech" and turn.channel == CHANNEL_CUSTOMER and turn.is_speech
        for turn in turns
    ):
        flags.add("no_customer_speech")
    if not any(
        turn.record_type == "speech"
        and turn.channel == CHANNEL_HISTORICAL_AGENT
        and turn.is_speech
        for turn in turns
    ):
        flags.add("no_agent_speech")
    if any(turn.speaker_role == "other" for turn in turns):
        flags.add("other_channel_present")

    channel_counts: dict[str, int] = {}
    for record in ordered:
        key = str(record.channel_id)
        channel_counts[key] = channel_counts.get(key, 0) + 1

    text = "\n".join(
        turn.text for turn in turns if turn.record_type == "speech" and turn.is_speech
    )
    content_hash = call_content_sha256(
        [
            (
                record.seq,
                record.start_ms,
                record.end_ms,
                record.channel_id,
                record.payload_type,
                record.event,
                record.text,
            )
            for record in ordered
        ]
    )

    if flags & STRUCTURAL_FLAGS:
        status = "invalid"
    elif flags & SUSPECT_FLAGS:
        status = "suspect"
    else:
        status = "valid"

    return NormalizedCall(
        call_id=unit.transcript_id,
        business_domain="unknown",
        source=SourceRef(
            corpus=corpus,
            corpus_schema_version=corpus_schema_version,
        ),
        duration_ms=max(unit.duration_ms, 0),
        record_count=len(unit.records),
        speech_record_count=len(speech_records),
        event_record_count=len(unit.records) - len(speech_records),
        channel_counts=channel_counts,
        quality_flags=sorted(flags),
        data_status=status,  # type: ignore[arg-type]
        content_sha256=content_hash,
        text_simhash=fingerprint_hex(simhash64(text)),
        turns=turns,
    )


def reconstruct_calls(
    units: Iterable[TranscriptUnit],
    *,
    corpus: str,
    corpus_schema_version: str,
) -> Iterator[NormalizedCall]:
    """Stream reconstructed conversations, one per delivered transcript unit."""
    for unit in units:
        yield reconstruct_call(
            unit, corpus=corpus, corpus_schema_version=corpus_schema_version
        )


__all__ = [
    "RECONSTRUCTION_VERSION",
    "STRUCTURAL_FLAGS",
    "SUSPECT_FLAGS",
    "TIMING_RESET_THRESHOLD_MS",
    "reconstruct_call",
    "reconstruct_calls",
]
