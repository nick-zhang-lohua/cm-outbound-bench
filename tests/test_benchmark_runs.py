"""Stage 5: repeated runs, seed control, aggregation and agent comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.benchmark import cli as benchmark_cli
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import find_task
from cm_outbound_bench.benchmark.metrics import (
    aggregate,
    compare_runs,
    load_manifests,
)
from cm_outbound_bench.benchmark.toolkits import tool_surface_hash
from cm_outbound_bench.cli import main


def _run(directory: Path, *extra: str) -> int:
    return main(
        [
            "benchmark",
            "run",
            "--agent",
            "demo",
            "--output-dir",
            str(directory),
            *extra,
        ]
    )


def test_repeat_produces_separate_episodes(tmp_path: Path) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison", "--repeat", "3") == 0
    manifests = load_manifests(tmp_path)

    assert len(manifests) == 3
    assert len({item.run_id for item in manifests}) == 3
    assert len({item.episode_id for item in manifests}) == 3
    assert sorted(item.repeat_index for item in manifests) == [0, 1, 2]
    assert all(item.repeat_count == 3 for item in manifests)
    assert all(item.run_status == "success" for item in manifests)

    traces = [tmp_path / item.trace_path for item in manifests]
    assert all(path.is_file() for path in traces)
    assert len({path for path in traces}) == 3
    # All repetitions start from the same initial state.
    assert len({item.initial_state_hash for item in manifests}) == 1


def test_repeated_invocations_never_overwrite_previous_runs(tmp_path: Path) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison", "--repeat", "2") == 0
    first = {item.run_id for item in load_manifests(tmp_path)}
    assert _run(tmp_path, "--task", "d01_plan_comparison", "--repeat", "2") == 0
    second = {item.run_id for item in load_manifests(tmp_path)}
    assert len(second) == 4
    assert first < second


def test_seed_is_recorded_with_an_explicit_status(tmp_path: Path) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison", "--seed", "42") == 0
    manifest = load_manifests(tmp_path)[0]
    assert manifest.random_seed == 42
    # The scripted agent and simulator use no RNG, and the manifest says so
    # instead of claiming the seed changed the run.
    assert manifest.seed_status == "unused_deterministic_component"


def test_no_seed_means_no_seed_claim(tmp_path: Path) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison") == 0
    manifest = load_manifests(tmp_path)[0]
    assert manifest.random_seed is None
    assert manifest.seed_status == ""


def test_manifest_records_the_initial_tool_surface_not_the_task_hash(
    tmp_path: Path,
) -> None:
    """``initial_tools_hash`` must identify the visible tool surface."""
    assert _run(tmp_path, "--task", "d01_plan_comparison") == 0
    manifest = load_manifests(tmp_path)[0]
    task = find_task(manifest.task_id)
    expected = tool_surface_hash(task.allowed_tools)
    assert manifest.initial_tools_hash == expected
    assert manifest.initial_tools_hash != manifest.task_hash
    assert len(manifest.initial_tools_hash) == 64


def test_tool_surface_hash_is_stable_and_allow_list_sensitive() -> None:
    assert tool_surface_hash(["b_tool", "a_tool"]) == tool_surface_hash(
        ["a_tool", "b_tool"]
    )
    assert tool_surface_hash(["a_tool"]) != tool_surface_hash(["a_tool", "b_tool"])
    # An unknown name is still hashed, so a typo cannot hide.
    assert tool_surface_hash(["a_tool"]) != tool_surface_hash(
        ["a_tool", "unknown_tool"]
    )


def test_aggregate_reads_existing_manifests_without_rerunning(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison", "--repeat", "2") == 0
    assert _run(tmp_path, "--task", "d02_hesitation_then_decline") == 0
    capsys.readouterr()

    def _explode(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("aggregate must not execute tasks")

    monkeypatch.setattr(benchmark_cli, "execute_task", _explode)
    assert main(["benchmark", "aggregate", "--dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "Benchmark Aggregate Report" in output
    assert "Manifests: 3" in output


def test_aggregate_groups_by_task_domain_and_agent(tmp_path: Path) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison", "--repeat", "2") == 0
    assert _run(tmp_path, "--task", "d02_hesitation_then_decline") == 0
    report = aggregate(load_manifests(tmp_path))

    assert report.manifests == 3
    assert {domain.domain_id for domain in report.domains} == {"D01", "D02"}
    by_task = {task.task_id: task for task in report.tasks}
    assert by_task["d01_plan_comparison"].runs == 2
    assert by_task["d01_plan_comparison"].repetitions == 2
    assert by_task["d02_hesitation_then_decline"].runs == 1
    assert report.distinct_agents == ["benchmark_demo"]
    assert report.agents[0].runs == 3
    assert report.run_status_counts["success"] == 3


def test_single_run_has_no_confidence_interval(tmp_path: Path) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison") == 0
    report = aggregate(load_manifests(tmp_path))
    task = report.tasks[0]
    assert task.success_rate_ci95 is None
    assert task.success_rate_stdev == 0.0


def test_aggregate_separates_runtime_and_evaluation_failures(
    tmp_path: Path, monkeypatch
) -> None:
    real_execute = benchmark_cli.execute_task

    def flaky(task, **kwargs):
        if task.task_id == "d03_bill_explanation":
            raise RuntimeError("injected")
        return real_execute(task, **kwargs)

    monkeypatch.setattr(benchmark_cli, "execute_task", flaky)
    assert _run(tmp_path, "--domain", "D03") == 1
    report = aggregate(load_manifests(tmp_path))
    assert report.run_status_counts["runtime_failure"] == 1
    assert report.run_status_counts["success"] == 1
    assert "unexpected_runtime_error" in report.failure_categories


def _copy_dir_with_agent(source: Path, target: Path, agent_type: str) -> None:
    target.mkdir(parents=True)
    for path in sorted(source.glob("RUN-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["agent_type"] = agent_type
        (target / path.name).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )


def test_compare_two_agents(tmp_path: Path, monkeypatch, capsys) -> None:
    left = tmp_path / "agent_a"
    right = tmp_path / "agent_b"
    assert _run(left, "--task", "d01_plan_comparison") == 0

    monkeypatch.setattr(
        benchmark_cli,
        "_build_agent",
        lambda args, seed=None: (
            BenchmarkDemoAgent,
            "agent_b",
            {"prompt_version": "scripted-v1", "seed_component": "deterministic"},
        ),
    )
    assert _run(right, "--task", "d01_plan_comparison") == 0
    capsys.readouterr()

    assert (
        main(
            [
                "benchmark",
                "compare",
                "--dir",
                str(left),
                "--dir",
                str(right),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["compatible"] is True
    assert payload["left_manifests"] == 1
    assert payload["right_manifests"] == 1
    task = payload["tasks"][0]
    assert task["left_success_rate"] == 1.0
    assert task["right_success_rate"] == 1.0
    assert task["success_rate_delta"] == 0.0


def test_compare_reports_incompatible_initial_states(tmp_path: Path) -> None:
    left = tmp_path / "a"
    right = tmp_path / "b"
    assert _run(left, "--task", "d01_plan_comparison") == 0
    right.mkdir()
    for path in sorted(left.glob("RUN-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["initial_state_hash"] = "0" * 64
        (right / path.name).write_text(json.dumps(payload), encoding="utf-8")

    report = compare_runs(load_manifests(left), load_manifests(right))
    assert report.compatible is False
    assert any("initial_state_hash differs" in item for item in report.incompatibilities)
    assert report.tasks[0].compatible is False


def test_compare_reports_benchmark_version_mismatch(tmp_path: Path) -> None:
    left = tmp_path / "a"
    right = tmp_path / "b"
    assert _run(left, "--task", "d01_plan_comparison") == 0
    right.mkdir()
    for path in sorted(left.glob("RUN-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["benchmark_version"] = "0.9.0"
        (right / path.name).write_text(json.dumps(payload), encoding="utf-8")

    report = compare_runs(load_manifests(left), load_manifests(right))
    assert report.compatible is False
    assert any("benchmark_version differs" in item for item in report.incompatibilities)


def test_compare_distinguishes_absent_tasks(tmp_path: Path) -> None:
    left = tmp_path / "a"
    right = tmp_path / "b"
    assert _run(left, "--task", "d01_plan_comparison") == 0
    assert _run(right, "--task", "d02_hesitation_then_decline") == 0
    report = compare_runs(load_manifests(left), load_manifests(right))
    by_task = {task.task_id: task for task in report.tasks}
    assert by_task["d01_plan_comparison"].right_success_rate is None
    assert by_task["d01_plan_comparison"].success_rate_delta is None


def test_compare_cli_returns_nonzero_for_incompatible_runs(
    tmp_path: Path, capsys
) -> None:
    left = tmp_path / "a"
    right = tmp_path / "b"
    assert _run(left, "--task", "d01_plan_comparison") == 0
    right.mkdir()
    for path in sorted(left.glob("RUN-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["task_version"] = "9.9"
        (right / path.name).write_text(json.dumps(payload), encoding="utf-8")
    capsys.readouterr()
    assert main(["benchmark", "compare", "--dir", str(left), "--dir", str(right)]) == 1
    output = capsys.readouterr().out
    assert "Comparable conditions: False" in output
    assert "task_version differs" in output


def test_aggregate_ignores_non_manifest_files(tmp_path: Path) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison") == 0
    (tmp_path / "report.md").write_text("# report", encoding="utf-8")
    (tmp_path / "aggregate.json").write_text("{}", encoding="utf-8")
    assert len(load_manifests(tmp_path)) == 1


@pytest.mark.parametrize("repeat", ["0", "-1"])
def test_invalid_repeat_is_rejected(tmp_path: Path, repeat: str) -> None:
    assert _run(tmp_path, "--task", "d01_plan_comparison", "--repeat", repeat) == 2
