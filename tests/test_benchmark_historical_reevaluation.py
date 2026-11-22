"""Cross-version historical re-evaluation (read-only diagnostic).

A trace recorded against task version N may legitimately be re-read against
task version N+1 to audit whether a specification repair changed the verdict of
an *already preserved* episode. That observation must never be confused with
current release evidence:

* the default ``evaluate`` path still refuses a version mismatch;
* only the explicit ``--historical-diagnostic`` path relaxes it;
* the result is labelled ``historical`` and carries both version labels;
* the stored trace file is never modified;
* identity (task id, domain, scenario) is still enforced;
* re-execution (``replay``) stays strict, because it is not a diagnostic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import BENCHMARK_VERSION, execute_task
from cm_outbound_bench.benchmark.replay import (
    evaluate_trace,
    replay_trace,
    resolve_task,
)
from cm_outbound_bench.benchmark.trace import (
    EpisodeTrace,
    TraceAgent,
    TraceIntegrityError,
    TraceSchemaError,
    build_trace,
    write_trace,
)
from cm_outbound_bench.cli import main

DECLINE_TASK = "d02_hesitation_then_decline"


def _recorded_trace(
    task_id: str,
    *,
    trace_version: str | None = None,
    run_id: str = "RUN-historical-0001",
) -> EpisodeTrace:
    """Record one demo episode labelled with an (optionally older) task version.

    The trace is assembled through the supported ``build_trace`` path so its own
    integrity hash stays consistent; only the declared task version differs from
    the installed catalogue, which is exactly the historical situation.
    """
    task = find_task(task_id)
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    recorded_task = (
        task
        if trace_version is None
        else task.model_copy(update={"task_version": trace_version})
    )
    return build_trace(
        run_id=run_id,
        episode_id="EP-historical-0001",
        task=recorded_task,
        run=outcome.run,
        evaluation=outcome.evaluation,
        benchmark_version=BENCHMARK_VERSION,
        agent=TraceAgent(agent_type="benchmark_demo"),
    )


def _reseal(trace: EpisodeTrace) -> EpisodeTrace:
    """Recompute the trace's own integrity hash after a deliberate edit."""
    return trace.model_copy(
        update={
            "integrity": trace.integrity.model_copy(
                update={"trace_hash": trace.compute_hash()}
            )
        }
    )


def _old_decline_trace(run_id: str = "RUN-historical-0001") -> EpisodeTrace:
    return _recorded_trace(DECLINE_TASK, trace_version="1.2", run_id=run_id)


def test_default_evaluate_still_rejects_a_version_mismatch() -> None:
    with pytest.raises(TraceSchemaError) as error:
        evaluate_trace(_old_decline_trace())
    assert "1.2" in str(error.value)
    assert find_task(DECLINE_TASK).task_version in str(error.value)


def test_resolve_task_rejects_a_version_mismatch_by_default() -> None:
    with pytest.raises(TraceSchemaError):
        resolve_task(_old_decline_trace())


def test_historical_diagnostic_re_evaluates_without_rewriting_the_record() -> None:
    trace = _old_decline_trace()
    recorded_success = trace.evaluation.success
    recorded_violations = list(trace.evaluation.policy_violations)

    replay = evaluate_trace(trace, allow_task_version_mismatch=True)

    assert replay.historical is True
    assert replay.task_version_recorded == "1.2"
    assert replay.task_version_installed == find_task(DECLINE_TASK).task_version
    # A cross-version observation never claims to reproduce the old result.
    assert replay.matches_recorded is False
    assert any("cross-version diagnostic" in item for item in replay.differences)
    # The recorded evaluation object is untouched.
    assert trace.evaluation.success == recorded_success
    assert trace.evaluation.policy_violations == recorded_violations


def test_historical_diagnostic_still_enforces_task_identity() -> None:
    trace = _old_decline_trace()
    payload = trace.model_dump(mode="json")
    payload["domain_id"] = "D03"
    with pytest.raises(TraceSchemaError):
        evaluate_trace(
            _reseal(EpisodeTrace.model_validate(payload)),
            allow_task_version_mismatch=True,
        )


def test_historical_diagnostic_rejects_an_unknown_task() -> None:
    trace = _old_decline_trace()
    payload = trace.model_dump(mode="json")
    payload["task_id"] = "d02_no_such_task"
    with pytest.raises(TraceSchemaError):
        evaluate_trace(
            _reseal(EpisodeTrace.model_validate(payload)),
            allow_task_version_mismatch=True,
        )


def test_historical_diagnostic_still_detects_a_tampered_final_state() -> None:
    trace = _old_decline_trace()
    payload = trace.model_dump(mode="json")
    payload["final"]["final_state_hash"] = "0" * 64
    tampered = _reseal(EpisodeTrace.model_validate(payload))
    with pytest.raises(TraceIntegrityError):
        evaluate_trace(tampered, allow_task_version_mismatch=True)


def test_replay_remains_strict_across_versions() -> None:
    """Re-execution is not a diagnostic: it must refuse a version mismatch."""
    with pytest.raises(TraceSchemaError):
        replay_trace(_old_decline_trace())


def test_cli_historical_diagnostic_labels_the_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _old_decline_trace()
    trace_path, _ = write_trace(trace, tmp_path / "RUN-historical-0001")
    before = trace_path.read_bytes()

    exit_code = main(
        [
            "benchmark",
            "evaluate",
            "--trace",
            str(trace_path),
            "--historical-diagnostic",
        ]
    )
    output = capsys.readouterr().out
    assert "cross-version historical diagnostic" in output
    assert "recorded task version 1.2" in output
    assert f"installed task version {find_task(DECLINE_TASK).task_version}" in output
    assert "not release evidence" in output
    assert exit_code in (0, 1)
    # The stored artefact is byte-identical afterwards.
    assert trace_path.read_bytes() == before


def test_cli_without_the_flag_reports_the_version_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _old_decline_trace()
    trace_path, _ = write_trace(trace, tmp_path / "RUN-historical-0002")
    exit_code = main(["benchmark", "evaluate", "--trace", str(trace_path)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "task version" in (captured.err + captured.out)


def test_cli_historical_diagnostic_json_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _old_decline_trace()
    trace_path, _ = write_trace(trace, tmp_path / "RUN-historical-0003")
    main(
        [
            "benchmark",
            "evaluate",
            "--trace",
            str(trace_path),
            "--historical-diagnostic",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["historical_diagnostic"] is True
    assert payload["task_version_recorded"] == "1.2"
    assert payload["task_version_installed"] == find_task(DECLINE_TASK).task_version
    assert payload["run_id"] == "RUN-historical-0001"


def test_same_version_trace_is_not_labelled_historical() -> None:
    replay = evaluate_trace(_recorded_trace(DECLINE_TASK))
    assert replay.historical is False
    assert replay.task_version_recorded == find_task(DECLINE_TASK).task_version
    assert replay.matches_recorded is True


def test_every_task_can_be_re_evaluated_after_a_version_downgrade() -> None:
    """The diagnostic path works for all 14 tasks, not just D02."""
    for task in all_tasks():
        trace = _recorded_trace(task.task_id, trace_version="0.9")
        replay = evaluate_trace(trace, allow_task_version_mismatch=True)
        assert replay.historical is True
        assert replay.task_version_installed == task.task_version


def test_historical_diagnostic_does_not_change_the_evaluator_hash() -> None:
    """The diagnostic path is a read/CLI feature; the evaluator contract is fixed."""
    from cm_outbound_bench.evaluator import EVALUATOR_VERSION, evaluator_hash

    assert EVALUATOR_VERSION == "2.4"
    assert (
        evaluator_hash()
        == "a12c74abb8a5747e83d7aab070fc441a0a213fa2ff732ae607d60ae0d12eaae2"
    )
