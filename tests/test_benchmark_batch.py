"""Stage 3: batch failure isolation.

A single malformed task must not destroy the results of the other tasks in the
same batch, and a runtime failure must never be converted into a pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark import cli as benchmark_cli
from cm_outbound_bench.benchmark.execution import TaskValidationError
from cm_outbound_bench.benchmark.metrics import load_manifests
from cm_outbound_bench.cli import main


def _run(output_dir: Path, *extra: str) -> int:
    return main(
        [
            "benchmark",
            "run",
            "--agent",
            "demo",
            "--output-dir",
            str(output_dir),
            *extra,
        ]
    )


def test_one_task_failure_does_not_abort_the_batch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A raising task is isolated; every other selected task still runs."""
    real_execute = benchmark_cli.execute_task
    broken_task = "d03_bill_explanation"

    def flaky(task, **kwargs):
        if task.task_id == broken_task:
            raise TaskValidationError("injected runtime failure for the test")
        return real_execute(task, **kwargs)

    monkeypatch.setattr(benchmark_cli, "execute_task", flaky)

    exit_code = _run(tmp_path)
    output = capsys.readouterr()

    assert exit_code == 1, "a failed task must produce a non-zero exit code"
    assert "Benchmark Aggregate Report" in output.out

    manifests = load_manifests(tmp_path)
    assert len(manifests) == 14

    failed = [item for item in manifests if item.run_status == "runtime_failure"]
    assert [item.task_id for item in failed] == [broken_task]
    failure = failed[0]
    assert failure.task_success is False
    assert failure.error is not None
    assert failure.error.error_code == "task_validation_failed"
    assert failure.error.stage_of_failure
    assert failure.error.task_id == broken_task
    assert failure.error.domain_id == "D03"

    successes = [item for item in manifests if item.run_status == "success"]
    assert len(successes) == 13
    assert all(item.final_state_hash for item in successes)
    assert all(item.trace_sha256 for item in successes)

    # The failed task still has a structured artifact and no trace.
    run_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(run_dirs) == 14
    failure_records = [path / "failure.json" for path in run_dirs]
    assert sum(1 for path in failure_records if path.is_file()) == 1
    assert sum(1 for path in run_dirs if (path / "trace.json").is_file()) == 13


def test_batch_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    real_execute = benchmark_cli.execute_task

    def flaky(task, **kwargs):
        if task.task_id == "d01_plan_comparison":
            raise RuntimeError("injected boom")
        return real_execute(task, **kwargs)

    monkeypatch.setattr(benchmark_cli, "execute_task", flaky)
    assert _run(tmp_path, "--domain", "D01") == 1

    captured = capsys.readouterr()
    assert "RUNTIME_FAILURE" in captured.err
    assert "PASS" in captured.out  # the other D01 task still passed
    manifests = load_manifests(tmp_path)
    statuses = {item.task_id: item.run_status for item in manifests}
    assert statuses["d01_plan_comparison"] == "runtime_failure"
    assert statuses["d01_plan_content_explanation"] == "success"


def test_failure_message_is_sanitized(tmp_path: Path, monkeypatch) -> None:
    def flaky(task, **kwargs):
        raise RuntimeError("Authorization: Bearer super-secret-token-value")

    monkeypatch.setattr(benchmark_cli, "execute_task", flaky)
    assert _run(tmp_path, "--task", "d01_plan_comparison") == 1
    manifest = load_manifests(tmp_path)[0]
    assert manifest.error is not None
    assert "super-secret-token-value" not in manifest.error.message
    assert "[REDACTED]" in manifest.error.message


def test_keyboard_interrupt_is_not_treated_as_a_task_failure(
    tmp_path: Path, monkeypatch
) -> None:
    def interrupt(task, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark_cli, "execute_task", interrupt)
    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, "--task", "d01_plan_comparison")


def test_malformed_task_is_rejected_by_verify_before_execution(
    tmp_path: Path, monkeypatch
) -> None:
    """The late AttributeError path must be unreachable from the CLI."""
    from cm_outbound_bench.benchmark.domains import all_tasks

    tasks = all_tasks()
    target = tasks[0]
    payload = target.model_dump(mode="json")
    payload["expected_actions"] = [
        {
            "collection": "enrollments",
            "match": {"no_such_field": "PN201"},
            "expected_count": 0,
            "description": "malformed",
        }
    ]

    from pydantic import ValidationError

    from cm_outbound_bench.benchmark.tasks import BenchmarkTask

    with pytest.raises(ValidationError):
        BenchmarkTask.model_validate(payload)

    # And the catalogue gate still passes for the real catalogue.
    assert main(["benchmark", "verify"]) == 0


def test_batch_json_output_reports_failures(tmp_path: Path, monkeypatch, capsys) -> None:
    real_execute = benchmark_cli.execute_task

    def flaky(task, **kwargs):
        if task.task_id == "d02_hesitation_then_decline":
            raise ValueError("injected")
        return real_execute(task, **kwargs)

    monkeypatch.setattr(benchmark_cli, "execute_task", flaky)
    assert _run(tmp_path, "--domain", "D02", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["aggregate"]["run_status_counts"]["runtime_failure"] == 1
    assert payload["aggregate"]["run_status_counts"]["success"] == 3
