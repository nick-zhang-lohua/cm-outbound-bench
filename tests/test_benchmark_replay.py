"""Stage 2: re-evaluation and deterministic replay of saved traces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import BENCHMARK_VERSION, execute_task
from cm_outbound_bench.benchmark.replay import (
    TraceReplayError,
    evaluate_trace,
    replay_trace,
)
from cm_outbound_bench.benchmark.trace import (
    EpisodeTrace,
    TraceAgent,
    TraceIntegrityError,
    TraceSchemaError,
    build_trace,
    load_trace,
    write_trace,
)
from cm_outbound_bench.cli import main


def _recorded(task_id: str) -> EpisodeTrace:
    task = find_task(task_id)
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    return build_trace(
        run_id=outcome.run_id,
        episode_id=outcome.episode_id,
        task=task,
        run=outcome.run,
        evaluation=outcome.evaluation,
        benchmark_version=BENCHMARK_VERSION,
        agent=TraceAgent(agent_type="benchmark_demo"),
    )


def test_evaluating_a_saved_trace_reproduces_the_recorded_result() -> None:
    for task_id in (
        "d01_plan_content_explanation",
        "d02_offer_benefit_inquiry_accept",
        "d03_arrears_recharge_with_consent",
        "d05_real_name_not_verified",
        "d06_enrollment_confirmation_notification",
    ):
        trace = _recorded(task_id)
        replay = evaluate_trace(trace)
        assert replay.matches_recorded, (task_id, replay.differences)
        assert replay.evaluation.success == trace.evaluation.success
        assert replay.evaluation.final_state_hash == trace.final.final_state_hash


def test_replay_reproduces_every_state_hash() -> None:
    failures: list[str] = []
    for task in all_tasks():
        trace = _recorded(task.task_id)
        outcome = replay_trace(trace)
        if not outcome.ok:
            failures.append(f"{task.task_id}: {outcome.divergences}")
        if outcome.replayed_final_state_hash != trace.final.final_state_hash:
            failures.append(f"{task.task_id}: final state hash differs")
        if outcome.replayed_evaluation.success != trace.evaluation.success:
            failures.append(f"{task.task_id}: evaluation differs")
    assert failures == []


def test_replay_does_not_construct_a_live_agent(monkeypatch) -> None:
    """Replay drives recorded actions, never a provider-backed agent."""
    from cm_outbound_bench import llm_agent

    def _forbidden(self, client):  # pragma: no cover - must not be reached
        raise AssertionError("replay must not construct an LLM-backed agent")

    monkeypatch.setattr(llm_agent.LLMBackedAgent, "__init__", _forbidden)
    trace = _recorded("d02_offer_benefit_inquiry_accept")
    outcome = replay_trace(trace)
    assert outcome.ok, outcome.divergences


def test_modified_tool_arguments_are_detected_by_replay() -> None:
    trace = _recorded("d03_arrears_recharge_with_consent")
    payload = trace.model_dump(mode="json")
    target = next(
        action
        for action in payload["actions"]
        if action["action_type"] == "call_tool"
        and action["tool_name"] == "recharge_account"
    )
    target["arguments"]["amount_cents"] = 6000
    tampered = EpisodeTrace.model_validate(payload)
    tampered = tampered.model_copy(
        update={
            "integrity": tampered.integrity.model_copy(
                update={"trace_hash": tampered.compute_hash()}
            )
        }
    )

    outcome = replay_trace(tampered)
    assert not outcome.ok
    assert "tool_trace_mismatch" in outcome.divergences
    assert "final_state_mismatch" in outcome.divergences


def test_removed_consent_event_is_detected_by_replay() -> None:
    trace = _recorded("d02_offer_benefit_inquiry_accept")
    payload = trace.model_dump(mode="json")
    payload["consent_events"] = []
    tampered = EpisodeTrace.model_validate(payload)
    tampered = tampered.model_copy(
        update={
            "integrity": tampered.integrity.model_copy(
                update={"trace_hash": tampered.compute_hash()}
            )
        }
    )

    outcome = replay_trace(tampered)
    assert not outcome.ok
    assert "consent_event_mismatch" in outcome.divergences


def test_removed_tool_result_is_detected_by_replay() -> None:
    trace = _recorded("d02_offer_benefit_inquiry_accept")
    payload = trace.model_dump(mode="json")
    payload["tool_calls"] = payload["tool_calls"][:-1]
    tampered = EpisodeTrace.model_validate(payload)
    tampered = tampered.model_copy(
        update={
            "integrity": tampered.integrity.model_copy(
                update={"trace_hash": tampered.compute_hash()}
            )
        }
    )

    outcome = replay_trace(tampered)
    assert not outcome.ok
    assert "tool_trace_mismatch" in outcome.divergences


def test_unknown_task_version_is_rejected() -> None:
    trace = _recorded("d01_plan_comparison")
    tampered = trace.model_copy(update={"task_version": "9.9"})
    with pytest.raises(TraceSchemaError):
        evaluate_trace(tampered)


def test_unknown_task_id_is_rejected() -> None:
    trace = _recorded("d01_plan_comparison")
    tampered = trace.model_copy(update={"task_id": "not_a_task"})
    with pytest.raises(TraceSchemaError):
        evaluate_trace(tampered)


def test_initial_state_hash_mismatch_is_rejected() -> None:
    trace = _recorded("d01_plan_comparison")
    tampered = trace.model_copy(update={"initial_state_hash": "0" * 64})
    with pytest.raises(TraceIntegrityError):
        replay_trace(tampered)


def test_initial_state_id_mismatch_is_rejected() -> None:
    trace = _recorded("d01_plan_comparison")
    tampered = trace.model_copy(update={"initial_state_id": "STATE-D02-001"})
    with pytest.raises(TraceReplayError):
        replay_trace(tampered)


def test_cli_evaluate_and_replay_round_trip(tmp_path: Path, capsys) -> None:
    trace = _recorded("d02_offer_benefit_inquiry_accept")
    path, _ = write_trace(trace, tmp_path / "run")

    assert main(["benchmark", "evaluate", "--trace", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Matches recorded result: True" in output

    assert main(["benchmark", "replay", "--trace", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Deterministic replay reproduced the episode: True" in output


def test_cli_evaluate_reports_a_tampered_trace(tmp_path: Path, capsys) -> None:
    trace = _recorded("d01_plan_content_explanation")
    payload = trace.model_dump(mode="json")
    payload["conversation"][0]["content"] = "被修改"
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert main(["benchmark", "evaluate", "--trace", str(path)]) == 1
    error = capsys.readouterr().err
    assert "TraceIntegrityError" in error

    assert main(["benchmark", "evaluate", "--trace", str(path), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error_category"] == "TraceIntegrityError"


def test_cli_replay_reports_divergences_as_failure(tmp_path: Path, capsys) -> None:
    trace = _recorded("d02_offer_benefit_inquiry_accept")
    payload = trace.model_dump(mode="json")
    payload["simulator_events"] = []
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

    assert main(["benchmark", "replay", "--trace", str(path)]) == 1
    output = capsys.readouterr().out
    assert "Deterministic replay reproduced the episode: False" in output
    assert "simulator_event_mismatch" in output


def test_loaded_trace_can_be_replayed_from_disk(tmp_path: Path) -> None:
    trace = _recorded("d06_recharge_confirmation_notification")
    path, file_hash = write_trace(trace, tmp_path / "run")
    reloaded = load_trace(path)
    assert reloaded.compute_hash() == trace.compute_hash()
    assert file_hash
    outcome = replay_trace(reloaded)
    assert outcome.ok, outcome.divergences
