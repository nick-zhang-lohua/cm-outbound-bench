"""CLI registration and offline behaviour of the Milestone 1.2 commands."""

from __future__ import annotations

import json
from pathlib import Path

from cm_outbound_bench import cli


def test_gold_commands_are_registered() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["decision", "annotation", "inspect"])
    assert args.command == "decision"
    assert args.decision_command == "annotation"
    assert args.annotation_command == "inspect"
    args = parser.parse_args(["decision", "evaluate-group-a", "--dry-run"])
    assert args.decision_command == "evaluate-group-a"
    assert args.dry_run is True
    args = parser.parse_args(["decision", "report-group-a"])
    assert args.decision_command == "report-group-a"


def test_annotation_pilot_dry_run_accepts_limit_and_model() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "decision",
            "annotation",
            "pilot",
            "--limit",
            "1",
            "--model",
            "deepseek-v4-pro",
            "--dry-run",
        ]
    )
    assert args.limit == 1
    assert args.model == "deepseek-v4-pro"


def test_inspect_reports_the_verified_model(tmp_path: Path, capsys, monkeypatch) -> None:
    code = cli.main(["decision", "annotation", "inspect", "--json"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["judge"]["model"] == "deepseek-v4-pro"
    assert payload["test_locked"] is True


def test_evaluate_group_a_refuses_without_frozen_ground_truth(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from cm_outbound_bench.decisions.scoring import cli as scoring_cli

    storage = scoring_cli.GoldStorage(
        tmp_path,
        dataset_version="cm_outbound_v2",
        tasks_artifact_sha256="hash",
    )
    monkeypatch.setattr(
        scoring_cli.GoldStorage,
        "default",
        classmethod(lambda cls, **kw: storage),
    )
    code = cli.main(
        [
            "decision",
            "evaluate-group-a",
            "--dry-run",
            "--run-set",
            str(tmp_path / "absent"),
        ]
    )
    captured = capsys.readouterr()
    # With no frozen manifest the command must refuse and explain.
    assert code != 0
    assert "freeze" in (captured.out + captured.err)


def test_report_group_a_requires_saved_evaluation(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    from cm_outbound_bench.decisions.scoring import cli as scoring_cli

    monkeypatch.setattr(scoring_cli, "evaluation_dir", lambda: tmp_path)
    code = cli.main(["decision", "report-group-a"])
    assert code != 0
