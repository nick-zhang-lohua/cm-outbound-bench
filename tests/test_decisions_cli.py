"""CLI tests: command registration and the offline pipeline end to end."""

from __future__ import annotations

import json
from pathlib import Path

from cm_outbound_bench import cli
from cm_outbound_bench.decisions.store import TaskStore
from decision_support import dialogue, distinct_label, unit, write_corpus


def _fixture_corpus(root: Path) -> Path:
    units = [
        unit(
            f"CALL_FIXTURE_{index:02d}",
            dialogue(
                [
                    (
                        1 if position % 2 == 0 else 2,
                        f"通话{distinct_label(index)}"
                        f"{'客服' if position % 2 == 0 else '客户'}语句之{position + 1}",
                    )
                    for position in range(10)
                ]
            ),
        )
        for index in range(8)
    ]
    return write_corpus(root, units)


def _fixture_config(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "dataset.toml"
    config.write_text(
        "\n".join(
            [
                'artifact_root = "data/decision"',
                'outputs_root = "outputs/decision"',
                "near_duplicate_max_hamming = 0",
                "",
                "[task_construction]",
                "max_tasks_per_call = 2",
                "min_visible_turns = 2",
                "min_cutoff_gap = 1",
                'selection = "even_spread"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CM_DECISION_DATASET_CONFIG", str(config))
    return config


def test_decision_command_is_registered() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["decision", "validate-data"])
    assert args.command == "decision"
    assert args.decision_command == "validate-data"


def test_validate_data_reports_a_missing_corpus(
    tmp_path: Path, capsys
) -> None:
    code = cli.main(["decision", "validate-data", "--corpus", str(tmp_path / "absent")])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_offline_pipeline_end_to_end(tmp_path: Path, capsys, monkeypatch) -> None:
    _fixture_config(tmp_path, monkeypatch)
    corpus = _fixture_corpus(tmp_path / "corpus")
    calls = tmp_path / "calls" / "calls.jsonl"
    split = tmp_path / "split.json"
    tasks_dir = tmp_path / "tasks"

    assert cli.main(
        ["decision", "build-calls", "--corpus", str(corpus), "--out", str(calls)]
    ) == 0
    assert calls.is_file()
    assert (calls.parent / "calls.manifest.json").is_file()

    assert cli.main(
        ["decision", "split", "--calls", str(calls), "--out", str(split), "--json"]
    ) == 0
    split_payload = json.loads(split.read_text(encoding="utf-8"))
    assert all(split_payload["verification"].values())

    assert cli.main(
        [
            "decision",
            "build-tasks",
            "--calls",
            str(calls),
            "--split-manifest",
            str(split),
            "--out-dir",
            str(tasks_dir),
        ]
    ) == 0
    store = TaskStore.load(tasks_dir)
    assert len(store) > 0

    assert cli.main(
        [
            "decision",
            "check",
            "--tasks-dir",
            str(tasks_dir),
            "--split-manifest",
            str(split),
            "--calls",
            str(calls),
        ]
    ) == 0
    assert "prefix audit failures: 0" in capsys.readouterr().out

    split_name = next(
        name for name in ("dev", "train_f", "train_r") if store.counts_by_split().get(name)
    )
    task_id = store.task_ids_for_split(split_name)[0]
    traces_root = tmp_path / "traces"
    assert cli.main(
        [
            "decision",
            "run",
            "--agent",
            "scripted",
            "--split",
            split_name,
            "--task-id",
            task_id,
            "--tasks-dir",
            str(tasks_dir),
            "--split-manifest",
            str(split),
            "--output-dir",
            str(traces_root),
            "--label",
            "cli-fixture",
        ]
    ) == 0
    run_sets = list(traces_root.glob("*cli-fixture"))
    assert len(run_sets) == 1

    assert cli.main(
        ["decision", "evaluate", "--run-set", str(run_sets[0])]
    ) == 0
    assert (run_sets[0] / "evaluation.json").is_file()

    assert cli.main(
        [
            "decision",
            "report",
            "--run-set",
            str(run_sets[0]),
            "--out",
            str(tmp_path / "report.md"),
        ]
    ) == 0
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "not available" in report
    assert "占位" not in report
    assert "[REDACTED]" not in report
