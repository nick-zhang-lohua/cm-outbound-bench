from __future__ import annotations

import json
from pathlib import Path

from cm_outbound_bench.benchmark import cli as benchmark_cli
from cm_outbound_bench.benchmark.demo_agent import BenchmarkDemoAgent
from cm_outbound_bench.benchmark.domains import all_tasks, find_task
from cm_outbound_bench.benchmark.execution import execute_task
from cm_outbound_bench.benchmark.metrics import (
    aggregate,
    build_manifest,
    load_manifests,
    render_markdown,
    write_manifest,
)
from cm_outbound_bench.cli import main


def test_benchmark_list_domains(capsys) -> None:
    assert main(["benchmark", "list-domains"]) == 0
    output = capsys.readouterr().out
    for domain_id in ("D01", "D02", "D03", "D04", "D05", "D06"):
        assert domain_id in output
    assert "CROSS_CUTTING" in output


def test_benchmark_list_tasks(capsys) -> None:
    assert main(["benchmark", "list-tasks"]) == 0
    output = capsys.readouterr().out
    assert len(output.strip().splitlines()) == len(all_tasks())
    assert "d02_offer_benefit_inquiry_accept" in output

    assert main(["benchmark", "list-tasks", "--domain", "D05"]) == 0
    output = capsys.readouterr().out
    assert "d05_ownership_transfer_conditions" in output
    assert "d02_offer_benefit_inquiry_accept" not in output


def test_benchmark_verify(capsys) -> None:
    assert main(["benchmark", "verify"]) == 0
    output = capsys.readouterr().out
    assert "Catalog OK" in output
    assert "14 tasks" in output


def test_benchmark_run_writes_manifests(tmp_path: Path, capsys) -> None:
    result = main(
        [
            "benchmark",
            "run",
            "--task",
            "d02_offer_benefit_inquiry_accept",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert result == 0
    output = capsys.readouterr().out
    assert "d02_offer_benefit_inquiry_accept\tPASS" in output
    assert "Task success rate" in output

    manifests = load_manifests(tmp_path)
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.task_success is True
    assert manifest.domain_id == "D02"
    assert manifest.agent_type == "benchmark_demo"
    assert manifest.policy_violations == []


def test_benchmark_run_json_output(tmp_path: Path, capsys) -> None:
    assert (
        main(
            [
                "benchmark",
                "run",
                "--task",
                "d05_ownership_transfer_conditions",
                "--output-dir",
                str(tmp_path),
                "--json",
            ]
        )
        == 0
    )
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    payload = json.loads("\n".join(lines))
    assert payload["runs"][0]["task_id"] == "d05_ownership_transfer_conditions"
    assert payload["aggregate"]["manifests"] == 1


def test_benchmark_run_unknown_task(tmp_path: Path, capsys) -> None:
    assert (
        main(["benchmark", "run", "--task", "not_a_task", "--output-dir", str(tmp_path)])
        == 2
    )
    assert "Unknown benchmark task" in capsys.readouterr().err


def test_benchmark_run_requires_provider_for_llm(tmp_path: Path, capsys) -> None:
    result = main(
        [
            "benchmark",
            "run",
            "--task",
            "d02_offer_benefit_inquiry_accept",
            "--agent",
            "llm",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert result == 2
    assert "provider" in capsys.readouterr().err.lower()


def test_manifest_files_contain_no_secrets_or_text(tmp_path: Path) -> None:
    task = find_task("d03_arrears_recharge_with_consent")
    outcome = execute_task(task, agent_factory=BenchmarkDemoAgent)
    manifest = build_manifest(outcome, agent_type="benchmark_demo")
    path = write_manifest(manifest, tmp_path)
    payload = path.read_text(encoding="utf-8")

    assert "TEST_IDENTITY" not in payload
    assert "text_masked" not in payload
    assert "api_key" not in payload.lower()
    assert '"conversation"' not in payload


def test_manifest_round_trip(tmp_path: Path) -> None:
    outcome = execute_task(
        find_task("d01_plan_content_explanation"),
        agent_factory=BenchmarkDemoAgent,
    )
    manifest = build_manifest(outcome, agent_type="benchmark_demo")
    write_manifest(manifest, tmp_path)

    loaded = load_manifests(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].model_dump() == manifest.model_dump()


def test_aggregate_markdown_has_no_task_text(tmp_path: Path) -> None:
    manifests = []
    for task_id in ("d02_offer_benefit_inquiry_accept", "d06_recharge_confirmation_notification"):
        outcome = execute_task(find_task(task_id), agent_factory=BenchmarkDemoAgent)
        manifests.append(build_manifest(outcome, agent_type="benchmark_demo"))

    markdown = render_markdown(aggregate(manifests))

    assert "# Benchmark Aggregate Report" in markdown
    assert "d02_offer_benefit_inquiry_accept" in markdown
    assert "TEST_IDENTITY" not in markdown
    assert "模拟流量加赠包" not in markdown


def test_legacy_commands_still_work_after_benchmark_registration(capsys) -> None:
    assert main(["run-all", "--agent", "demo"]) == 0
    output = capsys.readouterr().out
    assert "Overall: 5/5 tasks passed" in output


def test_default_output_directory_is_the_working_directory(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An installed CLI must never write into the interpreter tree."""
    monkeypatch.delenv(benchmark_cli.RUN_DIR_ENV, raising=False)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert main(["benchmark", "run", "--task", "d01_plan_comparison"]) == 0
    capsys.readouterr()

    expected = workdir / "outputs" / "runs"
    assert expected.is_dir()
    assert len(load_manifests(expected)) == 1
    assert (expected / load_manifests(expected)[0].trace_path).is_file()
    assert benchmark_cli.default_run_dir() == expected


def test_run_directory_environment_override(tmp_path: Path, monkeypatch, capsys) -> None:
    target = tmp_path / "elsewhere"
    monkeypatch.setenv(benchmark_cli.RUN_DIR_ENV, str(target))
    monkeypatch.chdir(tmp_path)

    assert main(["benchmark", "run", "--task", "d01_plan_comparison"]) == 0
    capsys.readouterr()
    assert len(load_manifests(target)) == 1
    assert not (tmp_path / "outputs").exists()


def test_explicit_output_dir_still_wins(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(benchmark_cli.RUN_DIR_ENV, str(tmp_path / "envdir"))
    explicit = tmp_path / "explicit"
    assert (
        main(
            [
                "benchmark",
                "run",
                "--task",
                "d01_plan_comparison",
                "--output-dir",
                str(explicit),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert len(load_manifests(explicit)) == 1
    assert not (tmp_path / "envdir").exists()


def test_package_version_matches_the_benchmark_version() -> None:
    """The distribution and the benchmark contract share one release number."""
    import tomllib

    from cm_outbound_bench.benchmark.execution import BENCHMARK_VERSION

    pyproject = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    assert pyproject["project"]["version"] == BENCHMARK_VERSION


def test_cli_documents_both_command_families(capsys) -> None:
    import pytest

    with pytest.raises(SystemExit):
        main(["--help"])
    output = capsys.readouterr().out
    assert "benchmark" in output
    assert "run-all" in output


def test_benchmark_help_lists_every_documented_command(capsys) -> None:
    import argparse

    import pytest

    from cm_outbound_bench.benchmark.cli import add_parser

    parser = argparse.ArgumentParser(prog="cm-outbound")
    sub = parser.add_subparsers(dest="command")
    add_parser(sub)
    with pytest.raises(SystemExit):
        parser.parse_args(["benchmark", "--help"])
    output = capsys.readouterr().out
    for command in (
        "list-domains",
        "list-tasks",
        "verify",
        "run",
        "evaluate",
        "replay",
        "aggregate",
        "compare",
    ):
        assert command in output
