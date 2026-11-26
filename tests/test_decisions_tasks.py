"""Task construction, agent-safe loading and prefix-audit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.decisions.audit import audit_task_prefixes
from cm_outbound_bench.decisions.builder import (
    REJECT_DUPLICATE_PREFIX,
    REJECT_OTHER_SPEAKER_IN_PREFIX,
    BuilderConfig,
    DecisionTaskBuilder,
)
from cm_outbound_bench.decisions.schema import PUBLIC_TASK_FIELDS
from cm_outbound_bench.decisions.store import (
    FORBIDDEN_PUBLIC_KEYS,
    TaskArtifactError,
    validate_task_artifacts,
)
from cm_outbound_bench.transcripts.store import write_calls
from decision_support import (
    build_tasks,
    call_from,
    dialogue,
    distinct_label,
    load_calls,
    manifest_for,
    write_corpus,
)


def _call():
    return call_from(
        "CALL_FIXTURE_DIALOGUE",
        [
            (1, "占位客服开场"),
            (2, "占位客户第一"),
            (1, "占位客服第二"),
            (2, "占位客户第二"),
            (1, "占位客服第三"),
            (2, "占位客户第三"),
            (1, "占位客服第四"),
            (2, "占位客户第四"),
            (1, "占位客服第五"),
        ],
    )


def test_cutoff_is_after_a_customer_turn_and_before_an_agent_turn() -> None:
    call = _call()
    builder = DecisionTaskBuilder(BuilderConfig(max_tasks_per_call=3, min_cutoff_gap=1))
    pairs = builder.add_call(call, split="dev")
    assert pairs
    speech = call.speech_turns
    for _, reference in pairs:
        prefix = [
            turn for turn in speech if turn.turn_id <= reference.cutoff_record_index + 1
        ]
        cutoff = [turn for turn in speech if turn.turn_id == reference.cutoff_record_index + 1][0]
        assert cutoff.speaker_role == "customer"
        following = [turn for turn in speech if turn.turn_id > cutoff.turn_id]
        assert following[0].speaker_role == "historical_agent"
        assert reference.historical_next_action_reference == following[0].text
        assert reference.cutoff_visible_turn_count == len(prefix)


def test_visible_context_is_exactly_the_prefix() -> None:
    call = _call()
    builder = DecisionTaskBuilder(BuilderConfig(max_tasks_per_call=1))
    (public, reference), = builder.add_call(call, split="dev")
    visible = [turn.content for turn in public.visible_context]
    assert visible == [turn.text for turn in call.speech_turns[: reference.cutoff_visible_turn_count]]
    assert public.visible_context[-1].content == call.speech_turns[
        reference.cutoff_visible_turn_count - 1
    ].text
    assert len(visible) < len(call.speech_turns)


def test_no_future_turn_text_is_exposed() -> None:
    call = _call()
    builder = DecisionTaskBuilder(BuilderConfig(max_tasks_per_call=1))
    (public, reference), = builder.add_call(call, split="dev")
    hidden = [turn.text for turn in call.speech_turns[reference.cutoff_visible_turn_count :]]
    visible = [turn.content for turn in public.visible_context]
    for text in hidden:
        assert text not in visible


def test_task_id_is_opaque() -> None:
    call = _call()
    builder = DecisionTaskBuilder(BuilderConfig(max_tasks_per_call=2))
    pairs = builder.add_call(call, split="dev")
    for public, reference in pairs:
        payload = json.dumps(public.public_payload(), ensure_ascii=False)
        assert call.call_id not in payload
        assert public.task_id.startswith("TASK_")
        assert call.call_id not in public.task_id
        assert reference.cutoff_segment_id.startswith(call.call_id)


def test_max_tasks_per_call_and_min_gap_are_enforced() -> None:
    call = _call()
    two = DecisionTaskBuilder(
        BuilderConfig(max_tasks_per_call=2, min_cutoff_gap=4)
    ).add_call(call, split="dev")
    assert len(two) == 2
    spaced = DecisionTaskBuilder(
        BuilderConfig(max_tasks_per_call=3, min_cutoff_gap=10)
    ).add_call(call, split="dev")
    assert len(spaced) == 1
    first = DecisionTaskBuilder(BuilderConfig(max_tasks_per_call=1)).add_call(
        call, split="dev"
    )
    assert len(first) == 1


def test_duplicate_visible_prefix_is_rejected_across_calls() -> None:
    call_a = call_from("CALL_FIXTURE_SAME_A", [(1, "占位客服开场"), (2, "占位客户第一"), (1, "占位客服第二")])
    call_b = call_from("CALL_FIXTURE_SAME_B", [(1, "占位客服开场"), (2, "占位客户第一"), (1, "占位客服第二")])
    builder = DecisionTaskBuilder(BuilderConfig(max_tasks_per_call=1))
    assert len(builder.add_call(call_a, split="dev")) == 1
    assert builder.add_call(call_b, split="dev") == []
    assert builder.stats.rejection_counts[REJECT_DUPLICATE_PREFIX] == 1


def test_prefix_containing_a_third_channel_is_rejected() -> None:
    call = call_from(
        "CALL_FIXTURE_THREE",
        [(1, "占位客服开场"), (3, "占位第三通道"), (2, "占位客户"), (1, "占位客服回应")],
    )
    builder = DecisionTaskBuilder(BuilderConfig(max_tasks_per_call=2))
    assert builder.add_call(call, split="dev") == []
    assert builder.stats.rejection_counts[REJECT_OTHER_SPEAKER_IN_PREFIX] == 1


def test_one_sided_call_yields_no_tasks() -> None:
    call = call_from("CALL_FIXTURE_ONE_SIDED", [(1, "占位甲"), (1, "占位乙")])
    builder = DecisionTaskBuilder()
    assert builder.add_call(call, split="dev") == []
    assert builder.stats.calls_skipped == 1


def _distinct_records(index: int) -> list[dict]:
    """A neutral dialogue whose every line is unique to this fixture call."""
    label = distinct_label(index)
    return dialogue(
        [
            (
                1 if position % 2 == 0 else 2,
                f"通话{label}{'客服' if position % 2 == 0 else '客户'}语句之{position + 1}",
            )
            for position in range(10)
        ]
    )


def _fixture_store(tmp_path: Path):
    corpus = write_corpus(
        tmp_path / "corpus",
        [
            {
                "schema_version": "1.0",
                "transcript_id": f"CALL_FIXTURE_{index:02d}",
                "duration_ms": 30_000,
                "records": _distinct_records(index),
            }
            for index in range(6)
        ],
    )
    calls = load_calls(corpus)
    manifest = manifest_for(calls, max_hamming=0)
    store, publics, references = build_tasks(calls, manifest, tmp_path / "tasks")
    return corpus, calls, manifest, store, publics, references


def test_store_roundtrip_and_agent_visible_fields(tmp_path: Path) -> None:
    _, _, _, store, publics, references = _fixture_store(tmp_path)
    assert len(publics) == len(references) > 0
    payload = store.agent_visible_payload(publics[0].task_id)
    assert set(payload) == PUBLIC_TASK_FIELDS
    assert not (set(payload) & FORBIDDEN_PUBLIC_KEYS)
    assert payload["task_id"] == publics[0].task_id


def test_loader_guard_rejects_a_leaked_private_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _, store, publics, _ = _fixture_store(tmp_path)
    task_id = publics[0].task_id

    def leaky_payload(self) -> dict:  # noqa: ANN001
        return {"task_id": self.task_id, "call_id": "CALL_FIXTURE_LEAK"}

    monkeypatch.setattr(type(publics[0]), "public_payload", leaky_payload)
    with pytest.raises(TaskArtifactError):
        store.agent_visible_payload(task_id)


def test_artifact_validation_detects_a_missing_reference(tmp_path: Path) -> None:
    _, _, _, _, _, references = _fixture_store(tmp_path)
    tasks_dir = tmp_path / "tasks"
    lines = (tasks_dir / "references.jsonl").read_text(encoding="utf-8").splitlines()
    (tasks_dir / "references.jsonl").write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    result = validate_task_artifacts(
        tasks_dir / "tasks.jsonl", tasks_dir / "references.jsonl"
    )
    assert result.ok is False
    assert result.tasks_without_reference == [references[0].task_id]


def test_tasks_from_one_call_share_the_call_split(tmp_path: Path) -> None:
    _, calls, manifest, _, _, references = _fixture_store(tmp_path)
    by_call: dict[str, set[str]] = {}
    for reference in references:
        by_call.setdefault(reference.call_id, set()).add(reference.split)
    assert by_call
    for call_id, splits in by_call.items():
        assert len(splits) == 1
        assert splits == {manifest.split_by_call[call_id]}


def test_prefix_audit_passes_on_valid_artifacts_and_detects_tampering(tmp_path: Path) -> None:
    corpus, calls, manifest, _, _, _ = _fixture_store(tmp_path)
    tasks_dir = tmp_path / "tasks"
    calls_path = tmp_path / "calls.jsonl"
    write_calls(calls_path, calls)
    audit = audit_task_prefixes(
        tasks_dir / "tasks.jsonl",
        tasks_dir / "references.jsonl",
        calls_path,
        split_by_call=manifest.split_by_call,
    )
    assert audit.ok, audit.model_dump()

    publics = [
        json.loads(line)
        for line in (tasks_dir / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    publics[0]["visible_context"][-1]["content"] = "被篡改的占位内容"
    (tasks_dir / "tasks.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in publics) + "\n",
        encoding="utf-8",
    )
    tampered = audit_task_prefixes(
        tasks_dir / "tasks.jsonl",
        tasks_dir / "references.jsonl",
        calls_path,
    )
    assert tampered.ok is False
    assert tampered.visible_prefix_mismatch >= 1
