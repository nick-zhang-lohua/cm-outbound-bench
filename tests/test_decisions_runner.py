"""Runner and trace tests: session isolation, failure handling and secret safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.decisions.agent import ScriptedDecisionAgent
from cm_outbound_bench.decisions.evaluator import evaluate_invocation
from cm_outbound_bench.decisions.runner import (
    AgentRunInfo,
    BenchmarkRunner,
    RunnerConfig,
    RunnerError,
)
from cm_outbound_bench.decisions.schema import (
    AgentInvocation,
    PrivateTaskReference,
    PublicDecisionTask,
    VisibleTurn,
)
from cm_outbound_bench.decisions.store import TaskStore
from cm_outbound_bench.decisions.trace import (
    TraceManager,
    TraceRecord,
    TraceSecretError,
    iter_traces,
)
from cm_outbound_bench.decisions.verification import verify_trace_inputs
from decision_support import (
    build_tasks,
    dialogue,
    distinct_label,
    load_calls,
    manifest_for,
    write_corpus,
)

SECRET = "sk-device-test-secret-value"


def _records(index: int) -> list[dict]:
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


def _store(tmp_path: Path) -> TaskStore:
    corpus = write_corpus(
        tmp_path / "corpus",
        [
            {
                "schema_version": "1.0",
                "transcript_id": f"CALL_FIXTURE_{index:02d}",
                "duration_ms": 30_000,
                "records": _records(index),
            }
            for index in range(5)
        ],
    )
    calls = load_calls(corpus)
    manifest = manifest_for(calls, max_hamming=0)
    store, _, _ = build_tasks(calls, manifest, tmp_path / "tasks")
    return store


def _development_split(store: TaskStore) -> str:
    counts = store.counts_by_split()
    for split in ("dev", "train_f", "train_r"):
        if counts.get(split):
            return split
    raise AssertionError("fixture store has no development tasks")


def _runner(
    store: TaskStore,
    tmp_path: Path,
    factory,
    *,
    secrets: tuple[str, ...] = (),
    agent_info: AgentRunInfo | None = None,
) -> tuple[BenchmarkRunner, TraceManager]:
    manager = TraceManager(tmp_path / "traces", "RS-TEST-FIXTURE", secrets=secrets)
    runner = BenchmarkRunner(
        store,
        factory,
        manager,
        agent_info=agent_info
        or AgentRunInfo(label="scripted", model_name="none", config_hash="cfg-hash"),
        dataset_version="neutral_test_fixture",
    )
    return runner, manager


def test_each_task_runs_in_a_fresh_agent_instance(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created: list[ScriptedDecisionAgent] = []

    def factory() -> ScriptedDecisionAgent:
        agent = ScriptedDecisionAgent()
        created.append(agent)
        return agent

    runner, manager = _runner(store, tmp_path, factory)
    split = _development_split(store)
    expected = min(3, len(store.task_ids_for_split(split)))
    manifest = runner.run(RunnerConfig(split=split, task_limit=3))

    assert expected > 0
    assert manifest.tasks_executed == expected
    assert len(created) == expected
    assert all(agent.sessions == 1 for agent in created)
    assert len(list(manager.directory.glob("RUN-*.json"))) == expected
    assert manifest.aggregate.execution_status_counts == {"success": expected}


def test_trace_records_the_required_identity_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner, _ = _runner(store, tmp_path, ScriptedDecisionAgent)
    split = _development_split(store)
    runner.run(RunnerConfig(split=split, task_limit=1))

    (trace,) = iter_traces(_trace_dir(tmp_path))
    assert trace.condition == "A"
    assert trace.split == split
    assert trace.benchmark_version
    assert trace.dataset_version == "neutral_test_fixture"
    assert len(trace.input_hash) == 64
    assert trace.config_hash == "cfg-hash"
    assert trace.prompt_version
    assert trace.evaluator_version
    assert trace.agent_version
    assert trace.execution_status == "success"
    assert trace.agent_output is not None
    assert trace.raw_output_text is None
    assert trace.evaluation.business.scoring_status == "unscored"


def _trace_dir(tmp_path: Path) -> Path:
    return tmp_path / "traces" / "RS-TEST-FIXTURE"


def test_provider_failure_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    store = _store(tmp_path)

    class FailingAgent:
        def decide(self, request) -> AgentInvocation:  # noqa: ANN001
            return AgentInvocation(
                status="provider_error",
                error_code="http_error",
                error_message="provider returned HTTP 500",
                latency_ms=5,
                attempts=2,
                http_status=500,
            )

    runner, _ = _runner(store, tmp_path, FailingAgent)
    split = _development_split(store)
    manifest = runner.run(RunnerConfig(split=split, task_limit=2))
    assert manifest.tasks_failed == 2
    assert manifest.aggregate.execution_status_counts == {"provider_error": 2}
    traces = iter_traces(_trace_dir(tmp_path))
    assert all(trace.error["code"] == "http_error" for trace in traces)
    assert all(trace.evaluation.business.reason.startswith("technical_failure") for trace in traces)


def test_agent_exception_is_contained_and_redacted(tmp_path: Path) -> None:
    store = _store(tmp_path)

    class ExplodingAgent:
        def decide(self, request) -> AgentInvocation:  # noqa: ANN001
            raise RuntimeError(f"unexpected crash with {SECRET}")

    runner, _ = _runner(
        store,
        tmp_path,
        ExplodingAgent,
        secrets=(SECRET,),
        agent_info=AgentRunInfo(
            label="failing", model_name="none", secrets=(SECRET,)
        ),
    )
    split = _development_split(store)
    manifest = runner.run(RunnerConfig(split=split, task_limit=2))
    assert manifest.tasks_failed == 2
    traces = iter_traces(_trace_dir(tmp_path))
    assert all(trace.execution_status == "provider_error" for trace in traces)
    assert all(trace.error["code"] == "agent_exception" for trace in traces)
    serialized = "\n".join(
        path.read_text(encoding="utf-8") for path in _trace_dir(tmp_path).glob("*.json")
    )
    assert SECRET not in serialized
    assert "[REDACTED]" in serialized


def test_test_split_is_refused_without_an_explicit_opt_in(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner, _ = _runner(store, tmp_path, ScriptedDecisionAgent)
    with pytest.raises(RunnerError) as caught:
        runner.run(RunnerConfig(split="test", task_limit=1))
    assert "isolated" in str(caught.value)


def test_explicit_task_from_another_split_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner, _ = _runner(store, tmp_path, ScriptedDecisionAgent)
    split = _development_split(store)
    other = next(
        candidate
        for candidate in store.task_ids
        if store.split_of(candidate) != split
    )
    with pytest.raises(RunnerError):
        runner.run(RunnerConfig(split=split, task_ids=[other]))


def test_trace_manager_refuses_to_persist_a_secret(tmp_path: Path) -> None:
    manager = TraceManager(tmp_path / "traces", "RS-SECRET", secrets=(SECRET,))
    task = PublicDecisionTask(
        task_id="TASK_FIXTURE_1",
        visible_context=[VisibleTurn(role="customer", content="占位客户")],
    )
    reference = PrivateTaskReference(
        task_id="TASK_FIXTURE_1",
        call_id="CALL_FIXTURE_1",
        split="dev",
        cutoff_record_index=0,
        cutoff_seq=0,
        cutoff_segment_id="CALL_FIXTURE_1:0",
        cutoff_start_ms=0,
        cutoff_visible_turn_count=1,
        hidden_speech_turn_count=0,
    )
    invocation = AgentInvocation(
        status="provider_error", error_message=SECRET, latency_ms=1, attempts=1
    )
    evaluation = evaluate_invocation(task, reference, invocation)
    trace = TraceRecord(
        run_id="RUN-FIXTURE",
        run_set_id="RS-SECRET",
        task_id="TASK_FIXTURE_1",
        evaluation=evaluation,
        error={"message": SECRET},
        execution_status="provider_error",
    )
    with pytest.raises(TraceSecretError):
        manager.write_trace(trace)


def test_run_set_manifest_has_no_conversation_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner, manager = _runner(store, tmp_path, ScriptedDecisionAgent)
    split = _development_split(store)
    runner.run(RunnerConfig(split=split, task_limit=2))
    manifest = json.loads(
        (manager.directory / "run_set_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["condition"] == "A"
    assert manifest["split"] == split
    assert manifest["tasks_executed"] == 2
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "占位客服" not in serialized
    assert "占位客户" not in serialized


def test_trace_input_verification_recomputes_the_prompt_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    runner, _ = _runner(store, tmp_path, ScriptedDecisionAgent)
    split = _development_split(store)
    runner.run(RunnerConfig(split=split, task_limit=2))
    traces = iter_traces(_trace_dir(tmp_path))
    verification = verify_trace_inputs(traces, store)
    assert verification.ok, verification.model_dump()
    assert verification.traces_checked == len(traces)

    traces[0].input_hash = "0" * 64
    tampered = verify_trace_inputs(traces, store)
    assert tampered.ok is False
    assert tampered.prompt_hash_mismatches == 1
