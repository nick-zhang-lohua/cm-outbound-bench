"""Channel semantics invariants and channel-audit behaviour.

These tests protect the *decision not to decide*: channels 3, 4 and 5 stay unknown,
the production mapping cannot be promoted silently, and the audit is a read-only
measurement whose representative samples keep the original timing and context.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.decisions.channel_audit import (
    SPEECH_SAMPLE_QUOTA,
    audit_channel_semantics,
    write_channel_review_package,
)
from cm_outbound_bench.decisions.channels import (
    CHANNEL_HYPOTHESES,
    CONFIRMED_CHANNEL_ROLES,
    UNKNOWN_CHANNEL_IDS,
    assert_production_mapping_unchanged,
    channel_registry,
    hypothesis_catalogue,
    is_unknown_channel,
    production_channel_mapping,
    role_for_channel,
    unregistered_channel_ids,
)
from cm_outbound_bench.transcripts.models import (
    CHANNEL_CUSTOMER,
    CHANNEL_HISTORICAL_AGENT,
    CHANNEL_ROLES,
    OTHER_ROLE,
)
from decision_support import dialogue, record, unit, write_corpus

CORPUS = "channel_audit_fixture"


def _channel_fixture(root: Path) -> Path:
    """A neutral corpus containing every channel, including unknown ones."""
    units = [
        unit(
            "CALL_FIXTURE_PLAIN",
            dialogue(
                [
                    (1, "占位客服开场白"),
                    (2, "占位客户回应一"),
                    (1, "占位客服说明"),
                    (2, "占位客户回应二"),
                    (1, "占位客服收尾"),
                ]
            ),
        ),
        unit(
            "CALL_FIXTURE_THIRD_PARTY",
            dialogue(
                [
                    (1, "占位客服开场白"),
                    (2, "占位客户回应一"),
                    (3, "占位第三方发言"),
                    (1, "占位客服追问"),
                    (4, "占位另一位发言"),
                    (2, "占位客户回应二"),
                    (1, "占位客服收尾"),
                ]
            ),
        ),
        unit(
            "CALL_FIXTURE_FIFTH_CHANNEL_ONLY",
            [
                record(0, 1000, 1500, 1, None, payload_type="EVENT", event="SYSTEM"),
                record(1, 2000, 2500, 2, "占位客户发言"),
                record(
                    2, 3000, 3500, 5, None, payload_type="EVENT", event="UNCLASSIFIED"
                ),
                record(3, 4000, 4500, 1, "占位客服收尾"),
            ],
        ),
        unit(
            "CALL_FIXTURE_EVENT_ONLY",
            [
                record(0, 0, 500, 1, None, payload_type="EVENT", event="SYSTEM"),
                record(1, 600, 900, 1, None, payload_type="EVENT", event="ENS"),
            ],
        ),
    ]
    return write_corpus(root, units)


# ---------------------------------------------------------------------------
# The mapping cannot be promoted silently
# ---------------------------------------------------------------------------


def test_confirmed_channels_keep_their_protocol_role() -> None:
    assert role_for_channel(CHANNEL_HISTORICAL_AGENT) == "historical_agent"
    assert role_for_channel(CHANNEL_CUSTOMER) == "customer"
    assert production_channel_mapping() == CONFIRMED_CHANNEL_ROLES


def test_unknown_channels_are_never_assigned_a_speaker_role() -> None:
    for channel in UNKNOWN_CHANNEL_IDS:
        assert role_for_channel(channel) == OTHER_ROLE
        assert is_unknown_channel(channel)
    assert unregistered_channel_ids() == tuple(sorted(UNKNOWN_CHANNEL_IDS))


def test_production_mapping_matches_the_runtime_channel_table() -> None:
    assert dict(CHANNEL_ROLES) == dict(CONFIRMED_CHANNEL_ROLES)


def test_promoting_an_unknown_channel_is_rejected() -> None:
    with pytest.raises(ValueError) as caught:
        assert_production_mapping_unchanged({1: "historical_agent", 2: "customer", 3: "customer"})
    assert "unknown" in str(caught.value)


def test_registry_marks_unknown_channels_as_unapproved() -> None:
    registrations = {item.channel_id: item for item in channel_registry()}
    assert registrations[1].confirmation_status == "confirmed_by_research_protocol"
    assert registrations[1].automatic_assignment_allowed is True
    for channel in UNKNOWN_CHANNEL_IDS:
        entry = registrations[channel]
        assert entry.confirmation_status == "unknown_pending_human_review"
        assert entry.automatic_assignment_allowed is False
        assert entry.is_confirmed is False
        assert entry.role == OTHER_ROLE


def test_hypothesis_catalogue_is_never_a_mapping() -> None:
    assert CHANNEL_HYPOTHESES
    for hypothesis in hypothesis_catalogue():
        assert hypothesis["supports_mapping"] is False
        assert hypothesis["evidence_level"]
        assert hypothesis["counter_evidence"], hypothesis["hypothesis_id"]


# ---------------------------------------------------------------------------
# Audit behaviour
# ---------------------------------------------------------------------------


def _audit(path: Path):
    return audit_channel_semantics(path, corpus=CORPUS, samples_per_channel=12)


def test_audit_reports_every_channel_without_changing_the_mapping(
    tmp_path: Path,
) -> None:
    corpus = _channel_fixture(tmp_path / "corpus")
    before = production_channel_mapping()
    report, _ = _audit(corpus)
    assert production_channel_mapping() == before

    channels = {item.channel_id: item for item in report.channels}
    assert set(channels) == {1, 2, 3, 4, 5}
    assert channels[1].semantics_status == "confirmed_by_research_protocol"
    for channel in UNKNOWN_CHANNEL_IDS:
        assert channels[channel].semantics_status == "unknown_pending_human_review"
        assert channels[channel].human_review_required is True
        assert channels[channel].approved_mapping == "none"


def test_audit_separates_speech_from_event_records(tmp_path: Path) -> None:
    corpus = _channel_fixture(tmp_path / "corpus")
    report, _ = _audit(corpus)
    channels = {item.channel_id: item for item in report.channels}
    assert channels[3].speech_records == 1
    assert channels[3].event_records == 0
    assert channels[4].speech_records == 1
    assert channels[5].speech_records == 0
    assert channels[5].event_records == 1
    assert channels[5].structural_homogeneity == "event_only"


def test_audit_measures_unit_presence_and_core_counters(tmp_path: Path) -> None:
    corpus = _channel_fixture(tmp_path / "corpus")
    report, _ = _audit(corpus)
    channels = {item.channel_id: item for item in report.channels}
    assert channels[3].units_containing == 1
    assert channels[4].units_containing == 1
    assert channels[5].units_containing == 1
    assert report.corpus["units"] == 4
    assert report.corpus["records"] == 5 + 7 + 4 + 2
    assert report.units_with_unknown_channel == 2
    assert report.units_with_unknown_channel_speech == 1


def test_audit_quantifies_every_interpretation_without_applying_it(
    tmp_path: Path,
) -> None:
    corpus = _channel_fixture(tmp_path / "corpus")
    report, _ = _audit(corpus)
    by_id = {item.interpretation_id: item for item in report.benchmark_impact}
    assert "I0_preserve_unknown" in by_id
    assert by_id["I0_preserve_unknown"].delta_tasks_vs_current == 0
    assert by_id["I1_unknown_as_customer"].changes_production_mapping is True
    for item in report.benchmark_impact:
        assert item.approved is False


def test_samples_preserve_original_timing_and_context(tmp_path: Path) -> None:
    corpus = _channel_fixture(tmp_path / "corpus")
    report, samples = _audit(corpus)
    assert samples[3], "channel 3 must produce at least one sample"

    from cm_outbound_bench.transcripts.ingestion import iter_units
    from cm_outbound_bench.transcripts.reconstruction import reconstruct_calls

    calls = {
        call.call_id: call
        for call in reconstruct_calls(
            iter_units(corpus), corpus=CORPUS, corpus_schema_version="1.0"
        )
    }
    for channel, items in samples.items():
        for sample in items:
            call = calls[sample.conversation_reference]
            turn = call.turns[sample.turn_index - 1]
            assert sample.content == turn.text
            assert sample.start_ms == turn.start_ms
            assert sample.end_ms == turn.end_ms
            assert sample.timestamp_offset == turn.timestamp
            assert sample.record_type == turn.record_type
            for context in sample.previous_context + sample.next_context:
                neighbour = call.turns[context["turn_index"] - 1]
                assert context["channel"] == neighbour.channel
                assert context["timestamp"] == neighbour.timestamp
                assert context["text"] == neighbour.text
                assert context["relative_index"] != 0


def test_samples_export_everything_when_the_channel_is_small(tmp_path: Path) -> None:
    corpus = _channel_fixture(tmp_path / "corpus")
    _, samples = _audit(corpus)
    assert len(samples[5]) == 1


def test_samples_prefer_speech_over_events_when_both_exist(tmp_path: Path) -> None:
    records = [record(index, index * 1000, index * 1000 + 900, 1, "占位客服") for index in range(2)]
    events = [
        record(2 + index, 5000 + index * 1000, 5500 + index * 1000, 3, None,
               payload_type="EVENT", event="UNCLASSIFIED")
        for index in range(4)
    ]
    speech = [
        record(10 + index, 20_000 + index * 1000, 20_900 + index * 1000, 3, f"占位第三方{index}")
        for index in range(6)
    ]
    corpus = write_corpus(
        tmp_path / "corpus",
        [unit("CALL_FIXTURE_MIX", records + events + speech)],
    )
    _, samples = _audit(corpus)
    kinds = [sample.record_type for sample in samples[3]]
    assert kinds.count("speech") >= min(SPEECH_SAMPLE_QUOTA, 6)


def test_review_package_is_written_with_summary_and_samples(tmp_path: Path) -> None:
    corpus = _channel_fixture(tmp_path / "corpus")
    report, samples = _audit(corpus)
    out = tmp_path / "review"
    written = write_channel_review_package(report, samples, out)

    for channel in UNKNOWN_CHANNEL_IDS:
        path = Path(written[f"channel_{channel}_samples"])
        assert path.is_file()
        for line in path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            assert payload["channel"] == channel
            assert "conversation_reference" in payload
            assert "previous_context" in payload
            assert "next_context" in payload

    summary = Path(written["channel_review_summary_csv"])
    header = summary.read_text(encoding="utf-8").splitlines()[0]
    assert "human_review_required" in header
    assert "approved_mapping" in header
    assert "records" in header
