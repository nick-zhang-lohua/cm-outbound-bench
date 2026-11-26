"""Channel semantics audit: statistics, representative samples, benchmark impact.

This module implements Work Package B of Milestone 1.1. It measures what the
unknown channels *are* in the data, extracts de-identified representative
records with their real surrounding context, and quantifies how each candidate
interpretation would change the benchmark.

It never changes the production channel mapping. The mapping lives in
:mod:`cm_outbound_bench.transcripts.models` and
:func:`cm_outbound_bench.decisions.channels.assert_production_mapping_unchanged`
is called before any result is returned, so an audit cannot silently promote an
unknown channel.

Nothing here reads the raw ``data/TXT`` export: the corpus reader is
``transcripts.ingestion``, which is restricted to the de-identified JSONL.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import Field

from cm_outbound_bench.decisions.builder import BuilderConfig, DecisionTaskBuilder
from cm_outbound_bench.decisions.channels import (
    CONFIRMED_CHANNEL_ROLES,
    UNKNOWN_CHANNEL_IDS,
    UNKNOWN_CHANNEL_ROLE,
    assert_production_mapping_unchanged,
    channel_registry_payload,
    hypothesis_catalogue,
    is_unknown_channel,
)
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.ingestion import (
    SourceRecord,
    TranscriptUnit,
    iter_units,
)
from cm_outbound_bench.transcripts.models import NormalizedCall
from cm_outbound_bench.transcripts.reconstruction import reconstruct_call

CHANNEL_AUDIT_VERSION = "1.0"

#: Default number of representative records per unknown channel.
DEFAULT_SAMPLES_PER_CHANNEL = 12

#: How many of those records must be text-bearing speech records. A channel's
#: meaning cannot be judged from non-speech events, so speech always dominates
#: the exported set.
SPEECH_SAMPLE_QUOTA = 10

#: Context window (records on each side) kept with a representative record.
CONTEXT_WINDOW = 3

SAMPLES_FILENAME_TEMPLATE = "channel_{channel}_samples.jsonl"
SUMMARY_FILENAME = "channel_review_summary.csv"
SUMMARY_JSON_FILENAME = "channel_review_summary.json"

#: Columns of the human-review CSV. Aggregate counts only: no conversation text.
SUMMARY_COLUMNS: tuple[str, ...] = (
    "channel",
    "role_in_production_mapping",
    "semantics_status",
    "records",
    "record_pct_of_corpus",
    "units_containing",
    "units_with_speech",
    "units_with_event",
    "speech_records",
    "event_records",
    "non_empty_text_records",
    "empty_text_records",
    "truncated_records",
    "records_overlapping_previous",
    "record_type_values",
    "event_values",
    "position_p10",
    "position_p50",
    "position_p90",
    "text_length_min",
    "text_length_median",
    "text_length_max",
    "structural_homogeneity",
    "samples_exported",
    "human_review_required",
    "approved_mapping",
)


class ChannelRecordStatistics(DomainModel):
    """Aggregate description of one channel. Contains no conversation text."""

    channel_id: int
    role: str
    semantics_status: str
    records: int = 0
    record_pct: float = 0.0
    units_containing: int = 0
    units_with_speech: int = 0
    units_with_event: int = 0
    speech_records: int = 0
    event_records: int = 0
    non_empty_text_records: int = 0
    empty_text_records: int = 0
    truncated_records: int = 0
    records_overlapping_previous: int = 0
    first_record_in_unit: int = 0
    last_record_in_unit: int = 0
    record_type_distribution: dict[str, int] = Field(default_factory=dict)
    event_distribution: dict[str, int] = Field(default_factory=dict)
    asr_flag_distribution: dict[str, int] = Field(default_factory=dict)
    redaction_flag_distribution: dict[str, int] = Field(default_factory=dict)
    previous_channel_distribution: dict[str, int] = Field(default_factory=dict)
    next_channel_distribution: dict[str, int] = Field(default_factory=dict)
    position_quantiles: dict[str, float] = Field(default_factory=dict)
    start_ms_quantiles: dict[str, int] = Field(default_factory=dict)
    text_length: dict[str, int | None] = Field(default_factory=dict)
    structural_homogeneity: str = ""
    human_review_required: bool = True
    approved_mapping: str = "none"

    @property
    def is_speech_bearing(self) -> bool:
        return self.speech_records > 0


class ChannelSample(DomainModel):
    """One representative de-identified record with its real context."""

    channel: int
    sample_id: str
    conversation_reference: str
    transcript_unit_reference: str
    record_type: str
    event: str | None = None
    timestamp_offset: str
    start_ms: int
    end_ms: int
    duration_ratio: float | None = None
    turn_index: int
    turn_count: int
    content: str
    is_truncated: bool
    overlaps_previous: bool
    asr_flags: list[str] = Field(default_factory=list)
    redaction_flags: list[str] = Field(default_factory=list)
    previous_context: list[dict[str, object]] = Field(default_factory=list)
    next_context: list[dict[str, object]] = Field(default_factory=list)
    associated_event_metadata: dict[str, object] = Field(default_factory=dict)
    unit_channels: list[int] = Field(default_factory=list)
    unit_record_count: int = 0
    data_status: str = ""
    evidence_source: str = ""


class BenchmarkImpact(DomainModel):
    """How each candidate interpretation would change the benchmark."""

    interpretation_id: str
    description: str
    tasks: int
    units_with_tasks: int
    delta_tasks_vs_current: int
    delta_units_vs_current: int
    affected_units: int
    changes_production_mapping: bool
    approved: bool = False
    note: str = ""


class ChannelAuditReport(DomainModel):
    """Complete channel audit result."""

    audit_version: str = CHANNEL_AUDIT_VERSION
    corpus: dict[str, object] = Field(default_factory=dict)
    channels: list[ChannelRecordStatistics] = Field(default_factory=list)
    unit_channel_combinations: dict[str, int] = Field(default_factory=dict)
    units_with_unknown_channel: int = 0
    units_with_unknown_channel_speech: int = 0
    units_with_no_confirmed_agent_speech: int = 0
    registry: dict[str, object] = Field(default_factory=dict)
    hypotheses: list[dict[str, object]] = Field(default_factory=list)
    benchmark_impact: list[BenchmarkImpact] = Field(default_factory=list)
    tasks_total: int = 0
    tasks_in_units_with_unknown_channel: int = 0
    tasks_by_split_in_affected_units: dict[str, int] = Field(default_factory=dict)
    builder_rejection_counts: dict[str, int] = Field(default_factory=dict)
    samples_exported: dict[str, int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @property
    def unknown_channels(self) -> list[ChannelRecordStatistics]:
        return [
            statistics_
            for statistics_ in self.channels
            if is_unknown_channel(statistics_.channel_id)
        ]


def _quantile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(
        len(sorted_values) - 1,
        max(0, int(round(fraction * (len(sorted_values) - 1)))),
    )
    return sorted_values[index]


class _ChannelAccumulator:
    """Mutable counters for one channel."""

    def __init__(self) -> None:
        self.records = 0
        self.speech = 0
        self.event = 0
        self.non_empty = 0
        self.empty = 0
        self.truncated = 0
        self.overlap = 0
        self.first_in_unit = 0
        self.last_in_unit = 0
        self.units: set[str] = set()
        self.units_with_speech: set[str] = set()
        self.units_with_event: set[str] = set()
        self.record_types: Counter[str] = Counter()
        self.events: Counter[str] = Counter()
        self.asr_flags: Counter[str] = Counter()
        self.redaction_flags: Counter[str] = Counter()
        self.previous_channel: Counter[int] = Counter()
        self.next_channel: Counter[int] = Counter()
        self.position: list[float] = []
        self.start_ms: list[int] = []
        self.text_length: list[int] = []


def audit_channel_semantics(
    corpus_dir: str | Path,
    *,
    corpus: str,
    corpus_schema_version: str = "1.0",
    samples_per_channel: int = DEFAULT_SAMPLES_PER_CHANNEL,
    builder_config: BuilderConfig | None = None,
    split_by_call: dict[str, str] | None = None,
) -> tuple[ChannelAuditReport, dict[int, list[ChannelSample]]]:
    """Audit every channel, including the unknown ones, in one streaming pass.

    Returns the aggregate report and the representative samples per unknown
    channel. The production mapping is verified before returning.
    """
    assert_production_mapping_unchanged()

    accumulators: dict[int, _ChannelAccumulator] = defaultdict(_ChannelAccumulator)
    unit_combinations: Counter[tuple[int, ...]] = Counter()
    units = 0
    total_records = 0
    units_with_unknown = 0
    units_with_unknown_speech = 0
    units_without_confirmed_agent = 0
    builder = DecisionTaskBuilder(builder_config or BuilderConfig())
    affected_units: set[str] = set()
    tasks_in_affected_units = 0
    tasks_by_split_in_affected: Counter[str] = Counter()

    # Sample selection: keep the first and last record per channel plus an even
    # spread, so the exported evidence covers the whole timeline rather than one
    # conversation. Deterministic given the corpus order.
    candidates: dict[int, list[tuple[int, str, ChannelSample]]] = defaultdict(list)
    candidate_counter: Counter[int] = Counter()

    for unit in iter_units(corpus_dir):
        call = reconstruct_call(
            unit, corpus=corpus, corpus_schema_version=corpus_schema_version
        )
        units += 1
        total_records += call.record_count
        present = tuple(sorted({turn.channel for turn in call.turns}))
        unit_combinations[present] += 1
        has_unknown = any(channel in UNKNOWN_CHANNEL_IDS for channel in present)
        if has_unknown:
            units_with_unknown += 1
        has_unknown_speech = any(
            turn.record_type == "speech" and turn.text.strip()
            for turn in call.turns
            if turn.channel in UNKNOWN_CHANNEL_IDS
        )
        if has_unknown_speech:
            units_with_unknown_speech += 1
        if not any(
            turn.record_type == "speech"
            and turn.channel == 1
            and turn.text.strip()
            for turn in call.turns
        ):
            units_without_confirmed_agent += 1
        for channel in present:
            accumulator = accumulators[channel]
            accumulator.units.add(call.call_id)
        for channel in UNKNOWN_CHANNEL_IDS:
            if any(
                turn.channel == channel and turn.record_type == "speech"
                for turn in call.turns
            ):
                accumulators[channel].units_with_speech.add(call.call_id)
            if any(
                turn.channel == channel and turn.record_type == "event"
                for turn in call.turns
            ):
                accumulators[channel].units_with_event.add(call.call_id)

        records_by_seq = {record.seq: record for record in unit.records}
        for index, turn in enumerate(call.turns):
            channel = turn.channel
            accumulator = accumulators[channel]
            accumulator.records += 1
            accumulator.record_types[turn.record_type] += 1
            if turn.record_type == "event":
                accumulator.event += 1
                accumulator.events[turn.event or "NONE"] += 1
            else:
                accumulator.speech += 1
                if turn.text.strip():
                    accumulator.non_empty += 1
                    accumulator.text_length.append(len(turn.text))
                else:
                    accumulator.empty += 1
                if turn.is_truncated:
                    accumulator.truncated += 1
            source = records_by_seq.get(turn.seq)
            if source is not None:
                accumulator.asr_flags.update(source.asr_flags)
                accumulator.redaction_flags.update(source.redaction_flags)
            accumulator.position.append(
                (turn.start_ms / call.duration_ms) if call.duration_ms else 0.0
            )
            accumulator.start_ms.append(turn.start_ms)
            if turn.overlaps_previous:
                accumulator.overlap += 1
            if index == 0:
                accumulator.first_in_unit += 1
            if index == len(call.turns) - 1:
                accumulator.last_in_unit += 1
            if index > 0:
                accumulator.previous_channel[call.turns[index - 1].channel] += 1
            if index + 1 < len(call.turns):
                accumulator.next_channel[call.turns[index + 1].channel] += 1

            if channel in UNKNOWN_CHANNEL_IDS:
                candidate_counter[channel] += 1
                candidates[channel].append(
                    (
                        candidate_counter[channel],
                        call.call_id,
                        _build_sample(
                            call,
                            unit,
                            index,
                            accumulator.position[-1],
                            source,
                        ),
                    )
                )

        if has_unknown_speech and split_by_call is not None:
            affected_units.add(call.call_id)
        if split_by_call is not None:
            for public, reference in builder.add_call(
                call, split=split_by_call.get(call.call_id, "unknown")
            ):
                if reference.call_id in affected_units:
                    tasks_in_affected_units += 1
                    tasks_by_split_in_affected[reference.split] += 1

    channel_statistics: list[ChannelRecordStatistics] = []
    for channel_id in sorted(accumulators):
        accumulator = accumulators[channel_id]
        known = channel_id in CONFIRMED_CHANNEL_ROLES
        positions = sorted(accumulator.position)
        starts = sorted(accumulator.start_ms)
        homogeneity = _homogeneity(accumulator)
        channel_statistics.append(
            ChannelRecordStatistics(
                channel_id=channel_id,
                role=(
                    CONFIRMED_CHANNEL_ROLES[channel_id]
                    if known
                    else UNKNOWN_CHANNEL_ROLE
                ),
                semantics_status=(
                    "confirmed_by_research_protocol"
                    if known
                    else "unknown_pending_human_review"
                ),
                records=accumulator.records,
                record_pct=round(accumulator.records / total_records * 100, 4)
                if total_records
                else 0.0,
                units_containing=len(accumulator.units),
                units_with_speech=len(accumulator.units_with_speech),
                units_with_event=len(accumulator.units_with_event),
                speech_records=accumulator.speech,
                event_records=accumulator.event,
                non_empty_text_records=accumulator.non_empty,
                empty_text_records=accumulator.empty,
                truncated_records=accumulator.truncated,
                records_overlapping_previous=accumulator.overlap,
                first_record_in_unit=accumulator.first_in_unit,
                last_record_in_unit=accumulator.last_in_unit,
                record_type_distribution=dict(accumulator.record_types.most_common()),
                event_distribution=dict(accumulator.events.most_common()),
                asr_flag_distribution=dict(accumulator.asr_flags.most_common()),
                redaction_flag_distribution=dict(
                    accumulator.redaction_flags.most_common()
                ),
                previous_channel_distribution={
                    str(key): value
                    for key, value in accumulator.previous_channel.most_common()
                },
                next_channel_distribution={
                    str(key): value
                    for key, value in accumulator.next_channel.most_common()
                },
                position_quantiles={
                    "p10": round(_quantile(positions, 0.10), 4),
                    "p50": round(_quantile(positions, 0.50), 4),
                    "p90": round(_quantile(positions, 0.90), 4),
                },
                start_ms_quantiles={
                    "p10": int(_quantile([float(value) for value in starts], 0.10)),
                    "p50": int(_quantile([float(value) for value in starts], 0.50)),
                    "p90": int(_quantile([float(value) for value in starts], 0.90)),
                },
                text_length={
                    "min": min(accumulator.text_length)
                    if accumulator.text_length
                    else None,
                    "median": int(statistics.median(accumulator.text_length))
                    if accumulator.text_length
                    else None,
                    "max": max(accumulator.text_length)
                    if accumulator.text_length
                    else None,
                },
                structural_homogeneity=homogeneity,
                human_review_required=not known,
                approved_mapping="none",
            )
        )

    samples = {
        channel: _select_samples(
            candidates.get(channel, []),
            samples_per_channel,
            speech_quota=SPEECH_SAMPLE_QUOTA,
        )
        for channel in UNKNOWN_CHANNEL_IDS
    }

    impact = _benchmark_impact(
        corpus_dir,
        corpus=corpus,
        corpus_schema_version=corpus_schema_version,
        split_by_call=split_by_call,
        builder_config=builder_config,
    )

    report = ChannelAuditReport(
        corpus={
            "units": units,
            "records": total_records,
            "corpus": corpus,
            "corpus_schema_version": corpus_schema_version,
            "channel_record_counts_source": "reconstructed from the de-identified corpus",
        },
        channels=channel_statistics,
        unit_channel_combinations={
            "+".join(str(channel) for channel in combination): count
            for combination, count in unit_combinations.most_common()
        },
        units_with_unknown_channel=units_with_unknown,
        units_with_unknown_channel_speech=units_with_unknown_speech,
        units_with_no_confirmed_agent_speech=units_without_confirmed_agent,
        registry=channel_registry_payload(),
        hypotheses=hypothesis_catalogue(),
        benchmark_impact=impact,
        tasks_total=builder.stats.tasks_built,
        tasks_in_units_with_unknown_channel=tasks_in_affected_units,
        tasks_by_split_in_affected_units=dict(sorted(tasks_by_split_in_affected.items())),
        builder_rejection_counts=dict(sorted(builder.stats.rejection_counts.items())),
        samples_exported={
            str(channel): len(items) for channel, items in samples.items()
        },
        limitations=[
            "The corpus carries no speaker attribute (role=UNKNOWN everywhere), so "
            "no channel meaning can be confirmed from the data alone.",
            "The de-identified corpus drops the source gender/language attribute, so "
            "per-channel voice profile cannot be measured here.",
            "Channel 5 has two records, both events: two records cannot support a "
            "statistical characterisation.",
            "Timestamps are offsets from the start of the recording, not wall-clock "
            "times, so absolute placement in the call flow is unavailable.",
            "No external call record or vendor channel specification is available in "
            "the workspace to confirm any hypothesis.",
        ],
    )
    assert_production_mapping_unchanged()
    return report, samples


def _build_sample(
    call: NormalizedCall,
    unit: TranscriptUnit,
    index: int,
    position: float,
    source: SourceRecord | None,
) -> ChannelSample:
    """Build one representative sample with its real surrounding context."""
    turn = call.turns[index]
    window_start = max(0, index - CONTEXT_WINDOW)

    def _context(offsets: Iterable[int]) -> list[dict[str, object]]:
        rendered: list[dict[str, object]] = []
        for offset in offsets:
            item = call.turns[offset]
            rendered.append(
                {
                    "relative_index": offset - index,
                    "turn_index": item.turn_id,
                    "channel": item.channel,
                    "speaker_role": item.speaker_role,
                    "record_type": item.record_type,
                    "event": item.event,
                    "timestamp": item.timestamp,
                    "text": item.text,
                    "is_truncated": item.is_truncated,
                }
            )
        return rendered

    previous = _context(range(window_start, index))
    following = _context(
        range(index + 1, min(len(call.turns), index + 1 + CONTEXT_WINDOW))
    )

    return ChannelSample(
        channel=turn.channel,
        sample_id=f"CH{turn.channel}-{call.call_id[-8:]}-T{turn.turn_id}",
        conversation_reference=call.call_id,
        transcript_unit_reference=unit.transcript_id,
        record_type=turn.record_type,
        event=turn.event,
        timestamp_offset=turn.timestamp,
        start_ms=turn.start_ms,
        end_ms=turn.end_ms,
        duration_ratio=round(position, 4),
        turn_index=turn.turn_id,
        turn_count=len(call.turns),
        content=turn.text,
        is_truncated=turn.is_truncated,
        overlaps_previous=turn.overlaps_previous,
        asr_flags=list(source.asr_flags) if source is not None else [],
        redaction_flags=list(source.redaction_flags) if source is not None else [],
        previous_context=previous,
        next_context=following,
        associated_event_metadata={
            "unit_event_count": call.event_record_count,
            "unit_speech_record_count": call.speech_record_count,
            "unit_duration_ms": call.duration_ms,
            "unit_quality_flags": list(call.quality_flags),
            "unit_data_status": call.data_status,
            "channel_record_counts": dict(sorted(call.channel_counts.items())),
        },
        unit_channels=sorted({item.channel for item in call.turns}),
        unit_record_count=call.record_count,
        data_status=call.data_status,
        evidence_source=(
            f"deidentified_corpus::{unit.transcript_id}::seq={turn.seq}"
        ),
    )


def _select_samples(
    candidates: Sequence[tuple[int, str, ChannelSample]],
    limit: int,
    *,
    speech_quota: int,
) -> list[ChannelSample]:
    """Deterministically pick at most *limit* representative samples.

    The selection is deliberately mixed, because a channel's meaning cannot be
    judged from event records alone: up to *speech_quota* text-bearing records are
    exported first, then the remaining slots are filled with the channel's event
    records (which show how the vendor tracked the channel on the timeline).

    When a channel has fewer records than *limit*, every record is exported. Both
    lists are ordered by corpus order and sampled with an even spread, so the
    package spans the whole dataset instead of one conversation.
    """
    if limit <= 0:
        return []
    if len(candidates) <= limit:
        return [sample for _, _, sample in candidates]

    speech = [item for item in candidates if item[2].record_type == "speech"]
    events = [item for item in candidates if item[2].record_type != "speech"]
    wanted_speech = min(speech_quota, len(speech), limit)
    wanted_events = min(limit - wanted_speech, len(events))
    chosen = _even_spread(speech, wanted_speech) + _even_spread(events, wanted_events)
    if len(chosen) < limit:
        chosen.extend(
            _even_spread(
                [item for item in candidates if item not in chosen],
                limit - len(chosen),
            )
        )
    return [sample for _, _, sample in chosen]


def _even_spread(
    candidates: Sequence[tuple[int, str, ChannelSample]],
    count: int,
) -> list[tuple[int, str, ChannelSample]]:
    """Return *count* items spread evenly over *candidates*, in corpus order."""
    if count <= 0 or not candidates:
        return []
    if count >= len(candidates):
        return list(candidates)
    step = len(candidates) / count
    picked: list[tuple[int, str, ChannelSample]] = []
    used: set[str] = set()
    for position in range(count):
        candidate = candidates[min(len(candidates) - 1, int(round(position * step)))]
        if candidate[2].sample_id in used:
            continue
        used.add(candidate[2].sample_id)
        picked.append(candidate)
    return picked


def _homogeneity(accumulator: _ChannelAccumulator) -> str:
    """Describe whether the channel's records share one structural shape."""
    if accumulator.records == 0:
        return "no_records"
    if accumulator.speech == 0:
        return "event_only"
    if accumulator.event == 0:
        return "speech_only"
    return "mixed_speech_and_event"


def _benchmark_impact(
    corpus_dir: str | Path,
    *,
    corpus: str,
    corpus_schema_version: str,
    split_by_call: dict[str, str] | None,
    builder_config: BuilderConfig | None,
) -> list[BenchmarkImpact]:
    """Quantify the benchmark effect of each candidate interpretation.

    The interpretations are *measurements*, never applied mappings: the production
    mapping is verified unchanged at the end of the audit. All scenarios are
    measured in one corpus pass so the audit stays cheap.
    """
    scenarios: tuple[tuple[str, str, dict[int, str]], ...] = (
        (
            "I0_preserve_unknown",
            "Keep channels 3/4/5 as 'other' (current production behaviour).",
            {},
        ),
        (
            "I1_unknown_as_customer",
            "Treat channels 3/4/5 as additional customer-side respondents.",
            {3: "customer", 4: "customer", 5: "customer"},
        ),
        (
            "I2_unknown_as_historical_agent",
            "Treat channels 3/4/5 as additional historical-agent speakers.",
            {3: "historical_agent", 4: "historical_agent", 5: "historical_agent"},
        ),
        (
            "I3_channel_3_as_agent_only",
            "Treat channel 3 as an additional historical-agent speaker, leave 4/5.",
            {3: "historical_agent"},
        ),
    )
    build_exclude = "I4_exclude_affected_units"
    config = builder_config or BuilderConfig()
    builders = {
        identifier: DecisionTaskBuilder(config.model_copy(deep=True))
        for identifier, _, _ in scenarios
    }
    exclude_builder = DecisionTaskBuilder(config.model_copy(deep=True))

    affected_units = 0
    for unit in iter_units(corpus_dir):
        call = reconstruct_call(
            unit, corpus=corpus, corpus_schema_version=corpus_schema_version
        )
        has_unknown = any(turn.channel in UNKNOWN_CHANNEL_IDS for turn in call.turns)
        if has_unknown:
            affected_units += 1
        split = (split_by_call or {}).get(call.call_id, "unknown")
        original_roles = [turn.speaker_role for turn in call.turns]
        for identifier, _description, overrides in scenarios:
            for turn, original in zip(call.turns, original_roles):
                turn.speaker_role = overrides.get(turn.channel, original)  # type: ignore[assignment]
            builders[identifier].add_call(call, split=split)
        for turn, original in zip(call.turns, original_roles):
            turn.speaker_role = original  # type: ignore[assignment]
        if not has_unknown:
            exclude_builder.add_call(call, split=split)

    results: dict[str, tuple[int, int, int]] = {
        identifier: (
            builder.stats.tasks_built,
            builder.stats.calls_with_tasks,
            affected_units,
        )
        for identifier, builder in builders.items()
    }
    results[build_exclude] = (
        exclude_builder.stats.tasks_built,
        exclude_builder.stats.calls_with_tasks,
        affected_units,
    )

    baseline_tasks, baseline_units, _ = results["I0_preserve_unknown"]
    descriptions = {identifier: description for identifier, description, _ in scenarios}
    descriptions[build_exclude] = "Exclude every unit that contains an unknown channel."
    mapping_change = {
        identifier: bool(overrides) for identifier, _, overrides in scenarios
    }
    mapping_change[build_exclude] = False
    impact: list[BenchmarkImpact] = []
    for identifier, (tasks, units_with_tasks, touched) in results.items():
        impact.append(
            BenchmarkImpact(
                interpretation_id=identifier,
                description=descriptions[identifier],
                tasks=tasks,
                units_with_tasks=units_with_tasks,
                delta_tasks_vs_current=tasks - baseline_tasks,
                delta_units_vs_current=units_with_tasks - baseline_units,
                affected_units=touched,
                changes_production_mapping=mapping_change[identifier],
                approved=False,
                note=(
                    "Measurement only. The production mapping is unchanged and no "
                    "interpretation is applied."
                ),
            )
        )
    return impact


def write_channel_review_package(
    report: ChannelAuditReport,
    samples: dict[int, list[ChannelSample]],
    output_dir: str | Path,
) -> dict[str, str]:
    """Write the human-review package and return the artifact paths."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    for channel in UNKNOWN_CHANNEL_IDS:
        path = root / SAMPLES_FILENAME_TEMPLATE.format(channel=channel)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for sample in samples.get(channel, []):
                handle.write(
                    json.dumps(
                        sample.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        written[f"channel_{channel}_samples"] = str(path)

    summary_path = root / SUMMARY_FILENAME
    samples_exported = report.samples_exported
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SUMMARY_COLUMNS))
        writer.writeheader()
        for statistics_ in report.channels:
            writer.writerow(
                {
                    "channel": statistics_.channel_id,
                    "role_in_production_mapping": statistics_.role,
                    "semantics_status": statistics_.semantics_status,
                    "records": statistics_.records,
                    "record_pct_of_corpus": statistics_.record_pct,
                    "units_containing": statistics_.units_containing,
                    "units_with_speech": statistics_.units_with_speech,
                    "units_with_event": statistics_.units_with_event,
                    "speech_records": statistics_.speech_records,
                    "event_records": statistics_.event_records,
                    "non_empty_text_records": statistics_.non_empty_text_records,
                    "empty_text_records": statistics_.empty_text_records,
                    "truncated_records": statistics_.truncated_records,
                    "records_overlapping_previous": (
                        statistics_.records_overlapping_previous
                    ),
                    "record_type_values": "|".join(
                        statistics_.record_type_distribution
                    ),
                    "event_values": "|".join(statistics_.event_distribution),
                    "position_p10": statistics_.position_quantiles.get("p10"),
                    "position_p50": statistics_.position_quantiles.get("p50"),
                    "position_p90": statistics_.position_quantiles.get("p90"),
                    "text_length_min": statistics_.text_length.get("min"),
                    "text_length_median": statistics_.text_length.get("median"),
                    "text_length_max": statistics_.text_length.get("max"),
                    "structural_homogeneity": statistics_.structural_homogeneity,
                    "samples_exported": samples_exported.get(
                        str(statistics_.channel_id), 0
                    ),
                    "human_review_required": statistics_.human_review_required,
                    "approved_mapping": statistics_.approved_mapping,
                }
            )
    written["channel_review_summary_csv"] = str(summary_path)

    json_path = root / SUMMARY_JSON_FILENAME
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    written["channel_review_summary_json"] = str(json_path)
    return written


def render_channel_audit_markdown(report: ChannelAuditReport) -> str:
    """Render the audit as markdown. Contains aggregate counts, no record text."""
    lines: list[str] = [
        "# Channel Semantics Audit Data (generated)",
        "",
        "Generated by `cm-outbound decision audit-channels`. This file contains "
        "aggregate counts only. Representative conversation records are written to "
        "the local review package.",
        "",
        "| Channel | Production role | Status | Records | % | Units | Speech | Events |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for statistics_ in report.channels:
        lines.append(
            f"| {statistics_.channel_id} | `{statistics_.role}` | "
            f"{statistics_.semantics_status} | {statistics_.records} | "
            f"{statistics_.record_pct} | {statistics_.units_containing} | "
            f"{statistics_.speech_records} | {statistics_.event_records} |"
        )
    lines.append("")
    if report.benchmark_impact:
        lines.extend(
            [
                "## Benchmark impact per candidate interpretation",
                "",
                "| Interpretation | Tasks | Units with tasks | Δ tasks | Δ units | Approved |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for item in report.benchmark_impact:
            lines.append(
                f"| `{item.interpretation_id}` | {item.tasks} | "
                f"{item.units_with_tasks} | {item.delta_tasks_vs_current:+d} | "
                f"{item.delta_units_vs_current:+d} | {item.approved} |"
            )
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "CHANNEL_AUDIT_VERSION",
    "DEFAULT_SAMPLES_PER_CHANNEL",
    "SPEECH_SAMPLE_QUOTA",
    "SAMPLES_FILENAME_TEMPLATE",
    "SUMMARY_COLUMNS",
    "SUMMARY_FILENAME",
    "SUMMARY_JSON_FILENAME",
    "BenchmarkImpact",
    "ChannelAuditReport",
    "ChannelRecordStatistics",
    "ChannelSample",
    "audit_channel_semantics",
    "render_channel_audit_markdown",
    "write_channel_review_package",
]
