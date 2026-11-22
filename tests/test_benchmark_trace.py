"""Stage 1: persisted execution traces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.execution import BENCHMARK_VERSION, execute_task
from cm_outbound_bench.benchmark.metrics import load_manifests
from cm_outbound_bench.benchmark.trace import (
    TRACE_SCHEMA_VERSION,
    EpisodeTrace,
    TraceIntegrityError,
    TraceSafetyError,
    TraceSchemaError,
    build_trace,
    load_trace,
    trace_file_sha256,
    write_trace,
)
from cm_outbound_bench.cli import main


def _trace_for(task_id: str) -> EpisodeTrace:
    task = find_task(task_id)
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    return build_trace(
        run_id=outcome.run_id,
        episode_id=outcome.episode_id,
        task=task,
        run=outcome.run,
        evaluation=outcome.evaluation,
        benchmark_version=BENCHMARK_VERSION,
        agent=_agent_stub(),
    )


def _agent_stub():
    from cm_outbound_bench.benchmark.trace import TraceAgent

    return TraceAgent(agent_type="benchmark_demo", prompt_version="scripted-v1")


def test_every_episode_writes_a_trace_the_manifest_references(tmp_path: Path) -> None:
    assert (
        main(
            [
                "benchmark",
                "run",
                "--task",
                "d02_offer_benefit_inquiry_accept",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    manifests = load_manifests(tmp_path)
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.run_status == "success"
    assert manifest.trace_schema_version == TRACE_SCHEMA_VERSION
    assert manifest.trace_sha256

    trace_path = tmp_path / manifest.trace_path
    assert trace_path.is_file()
    assert trace_file_sha256(trace_path) == manifest.trace_sha256
    trace = load_trace(trace_path)
    assert trace.task_id == manifest.task_id
    assert trace.run_id == manifest.run_id
    assert trace.episode_id == manifest.episode_id


def test_trace_contains_the_required_sections() -> None:
    trace = _trace_for("d02_offer_benefit_inquiry_accept")

    # identity
    assert trace.trace_schema_version == TRACE_SCHEMA_VERSION
    assert trace.run_id.startswith("RUN-")
    assert trace.episode_id.startswith("EP-")
    assert trace.task_id == "d02_offer_benefit_inquiry_accept"
    assert trace.domain_id == "D02"
    assert trace.scenario_id == "D02-OFFER-001"
    assert trace.task_version
    assert trace.benchmark_version

    # initial state
    assert trace.initial_state_id == "STATE-D02-001"
    assert len(trace.initial_state_hash) == 64
    assert trace.allowed_tools

    # agent
    assert trace.agent.agent_type == "benchmark_demo"

    # ordered execution
    assert trace.actions
    assert trace.conversation
    assert trace.simulator_events
    assert trace.tool_calls
    assert trace.consent_events
    assert [action.step for action in trace.actions] == sorted(
        action.step for action in trace.actions
    )
    assert [entry.step for entry in trace.tool_calls] == sorted(
        entry.step for entry in trace.tool_calls
    )

    # termination
    assert trace.stop.stop_reason == "agent_finished"
    assert trace.stop.episode_completed is True
    assert trace.stop.customer_conversation_closed is True

    # final state
    assert len(trace.final.final_state_hash) == 64
    assert trace.final.database.enrollments
    assert trace.final.completion_status == "completed"

    # integrity
    assert len(trace.integrity.trace_hash) == 64
    assert trace.integrity.created_at
    assert trace.integrity.schema_version == TRACE_SCHEMA_VERSION


def test_trace_stores_no_corpus_or_credential_content() -> None:
    trace = _trace_for("d04_plan_upgrade_with_consent")
    payload = trace.to_json()
    for task in (find_task("d04_plan_upgrade_with_consent"),):
        for reference in task.evidence_refs:
            assert reference.transcript_id not in payload
    assert "text_masked" not in payload
    assert "CALL_" not in payload
    assert "api_key" not in payload.lower()
    assert "bearer " not in payload.lower()
    assert "authorization" not in payload.lower()


def test_trace_round_trip_preserves_the_hash(tmp_path: Path) -> None:
    trace = _trace_for("d03_arrears_recharge_with_consent")
    path, file_hash = write_trace(trace, tmp_path)

    reloaded = load_trace(path)
    assert reloaded.compute_hash() == trace.compute_hash()
    assert reloaded.model_dump() == trace.model_dump()
    assert trace_file_sha256(path) == file_hash


def test_trace_write_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    trace = _trace_for("d01_plan_comparison")
    write_trace(trace, tmp_path / "run")
    leftovers = [path.name for path in (tmp_path / "run").iterdir()]
    assert leftovers == ["trace.json"]


def test_modified_trace_field_is_rejected(tmp_path: Path) -> None:
    trace = _trace_for("d02_offer_benefit_inquiry_accept")
    payload = trace.model_dump(mode="json")
    payload["conversation"][0]["content"] = "篡改后的消息"
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(TraceIntegrityError) as error:
        load_trace(path)
    assert "recorded hash" in str(error.value)


def test_modified_final_state_is_rejected_even_with_a_recomputed_hash(
    tmp_path: Path,
) -> None:
    trace = _trace_for("d02_offer_benefit_inquiry_accept")
    payload = trace.model_dump(mode="json")
    payload["final"]["database"]["plans"][0]["monthly_fee"] = 30.0
    tampered = EpisodeTrace.model_validate(payload)
    tampered = tampered.model_copy(
        update={
            "integrity": tampered.integrity.model_copy(
                update={"trace_hash": tampered.compute_hash()}
            )
        }
    )
    path = tmp_path / "trace.json"
    path.write_text(tampered.to_json(), encoding="utf-8")

    with pytest.raises(TraceIntegrityError) as error:
        load_trace(path)
    assert "final state" in str(error.value)


def test_removed_tool_result_is_rejected(tmp_path: Path) -> None:
    trace = _trace_for("d02_offer_benefit_inquiry_accept")
    payload = trace.model_dump(mode="json")
    payload["tool_calls"] = payload["tool_calls"][1:]
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(TraceIntegrityError):
        load_trace(path)


def test_reordered_events_are_rejected(tmp_path: Path) -> None:
    trace = _trace_for("d02_offer_benefit_inquiry_accept")
    payload = trace.model_dump(mode="json")
    payload["tool_calls"] = list(reversed(payload["tool_calls"]))
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(TraceIntegrityError):
        load_trace(path)


def test_incompatible_trace_schema_version_is_rejected(tmp_path: Path) -> None:
    trace = _trace_for("d01_plan_content_explanation")
    payload = trace.model_dump(mode="json")
    payload["trace_schema_version"] = "0.9"
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(TraceSchemaError) as error:
        load_trace(path)
    assert "schema version" in str(error.value)


def test_unparseable_trace_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(TraceSchemaError):
        load_trace(path)


def test_unknown_stop_reason_is_rejected(tmp_path: Path) -> None:
    trace = _trace_for("d01_plan_content_explanation")
    payload = trace.model_dump(mode="json")
    payload["stop"]["stop_reason"] = "invented_reason"
    tampered = EpisodeTrace.model_validate(payload)
    tampered = tampered.model_copy(
        update={
            "integrity": tampered.integrity.model_copy(
                update={"trace_hash": tampered.compute_hash()}
            )
        }
    )
    path = tmp_path / "trace.json"
    path.write_text(tampered.to_json(), encoding="utf-8")
    with pytest.raises(TraceSchemaError):
        load_trace(path).to_run_result()


def test_trace_safety_scan_rejects_credential_shaped_values(tmp_path: Path) -> None:
    trace = _trace_for("d01_plan_content_explanation")
    payload = trace.model_dump(mode="json")
    payload["conversation"][0]["content"] = "Authorization: Bearer abcdef123456"
    unsafe = EpisodeTrace.model_validate(payload)
    unsafe = unsafe.model_copy(
        update={
            "integrity": unsafe.integrity.model_copy(
                update={"trace_hash": unsafe.compute_hash()}
            )
        }
    )
    with pytest.raises(TraceSafetyError):
        write_trace(unsafe, tmp_path)


def test_trace_safety_scan_rejects_forbidden_keys(tmp_path: Path) -> None:
    trace = _trace_for("d01_plan_content_explanation")
    payload = trace.model_dump(mode="json")
    payload["agent"]["api_key"] = "placeholder"
    # ``TraceAgent`` forbids extra fields, so the unsafe key may only exist in a
    # raw payload; the scan is exercised directly instead.
    from cm_outbound_bench.benchmark.trace import assert_trace_is_safe

    with pytest.raises(TraceSafetyError):
        assert_trace_is_safe(payload)
