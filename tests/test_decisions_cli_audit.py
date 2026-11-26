"""CLI coverage for the Milestone 1.1 commands."""

from __future__ import annotations

import json
from pathlib import Path

from cm_outbound_bench import cli
from cm_outbound_bench.decisions.store import TaskStore
from decision_support import dialogue, distinct_label, unit, write_corpus


def _fixture_corpus(root: Path, *, calls: int = 10) -> Path:
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
        for index in range(calls)
    ]
    return write_corpus(root, units)


def _fixture_config(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "dataset.toml"
    config.write_text(
        "\n".join(
            [
                f'artifact_root = "{tmp_path.as_posix()}/data/decision"',
                f'outputs_root = "{tmp_path.as_posix()}/outputs/decision"',
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


def _write_manifest(corpus: Path) -> None:
    """Write a manifest whose hashes match the files, so validate-data passes."""
    import hashlib

    calls = corpus / "calls.jsonl"
    quarantine = corpus / "quarantine.jsonl"
    calls_bytes = calls.read_bytes()
    quarantine_bytes = quarantine.read_bytes() if quarantine.is_file() else b""
    if not quarantine.is_file():
        quarantine.write_bytes(b"")
    manifest = {
        "manifest_version": "2.0",
        "schema_version": "1.0",
        "pipeline_version": "test",
        "dataset": "neutral_test_fixture",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "output": {
            "calls": sum(1 for line in calls_bytes.splitlines() if line.strip()),
            "quarantined": 0,
            "records": sum(
                len(json.loads(line)["records"])
                for line in calls_bytes.splitlines()
                if line.strip()
            ),
            "speech_records": 0,
            "event_records": 0,
            "files": {
                "calls.jsonl": {
                    "sha256": hashlib.sha256(calls_bytes).hexdigest(),
                    "lines": sum(1 for line in calls_bytes.splitlines() if line.strip()),
                },
                "quarantine.jsonl": {
                    "sha256": hashlib.sha256(quarantine_bytes).hexdigest(),
                    "lines": 0,
                },
            },
        },
    }
    (corpus / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _prepare(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    _fixture_config(tmp_path, monkeypatch)
    corpus = _fixture_corpus(tmp_path / "corpus")
    _write_manifest(corpus)
    calls = tmp_path / "calls" / "calls.jsonl"
    split = tmp_path / "split.json"
    tasks_dir = tmp_path / "tasks"
    assert cli.main(
        ["decision", "build-calls", "--corpus", str(corpus), "--out", str(calls)]
    ) == 0
    assert cli.main(
        ["decision", "split", "--calls", str(calls), "--out", str(split), "--json"]
    ) == 0
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
    return {
        "corpus": corpus,
        "calls": calls,
        "split": split,
        "tasks": tasks_dir,
    }


def test_the_new_commands_are_registered() -> None:
    parser = cli._build_parser()
    for command in (
        "audit-identity",
        "audit-channels",
        "audit-business-sources",
        "sample",
        "export-annotations",
        "import-annotations",
        "adjudication-worklist",
        "import-adjudications",
        "agreement",
        "apply-annotations",
        "export-output-review",
        "import-output-review",
        "business-baseline",
    ):
        args = parser.parse_args(["decision", command, *_minimal_args(command)])
        assert args.command == "decision"
        assert args.decision_command == command


def _minimal_args(command: str) -> list[str]:
    required = {
        "sample": ["--purpose", "train_r_annotation_pilot"],
        "export-annotations": ["--plan", "x.json"],
        "import-annotations": ["--file", "x.jsonl"],
        "import-adjudications": ["--file", "x.jsonl"],
        "export-output-review": ["--run-set", "x"],
        "import-output-review": ["--file", "x.jsonl"],
        "business-baseline": ["--run-set", "x"],
    }
    return required.get(command, [])


def test_audit_identity_runs_on_a_fixture_corpus(tmp_path: Path, capsys, monkeypatch) -> None:
    paths = _prepare(tmp_path, monkeypatch)
    out = tmp_path / "identity.md"
    code = cli.main(
        [
            "decision",
            "audit-identity",
            "--corpus",
            str(paths["corpus"]),
            "--split-manifest",
            str(paths["split"]),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "Conversation identity" in text
    assert "Baseline matches Milestone 1" in text
    assert "占位客服" not in text
    assert "units" in capsys.readouterr().out.lower()


def test_audit_channels_writes_the_review_package(tmp_path: Path, capsys, monkeypatch) -> None:
    paths = _prepare(tmp_path, monkeypatch)
    out_dir = tmp_path / "channel_review"
    code = cli.main(
        [
            "decision",
            "audit-channels",
            "--corpus",
            str(paths["corpus"]),
            "--out-dir",
            str(out_dir),
        ]
    )
    assert code == 0
    assert (out_dir / "channel_review_summary.csv").is_file()
    for channel in (3, 4, 5):
        assert (out_dir / f"channel_{channel}_samples.jsonl").is_file()
    printed = capsys.readouterr().out
    assert "production channel mapping: unchanged" in printed


def test_audit_business_sources_reports_no_authoritative_source(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _fixture_config(tmp_path, monkeypatch)
    out = tmp_path / "inventory.json"
    code = cli.main(
        ["decision", "audit-business-sources", "--out", str(out), "--workspace-root", str(tmp_path)]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["raw_transcript_export_read"] is False
    assert "policy_verification_supported" in payload
    assert payload["authoritative_sources"] >= 0
    assert "sources" in payload


def test_sample_export_import_and_agreement(tmp_path: Path, capsys, monkeypatch) -> None:
    paths = _prepare(tmp_path, monkeypatch)
    store = TaskStore.load(paths["tasks"])
    split = next(
        name for name in ("dev", "train_f", "train_r") if store.counts_by_split().get(name)
    )
    plan_path = tmp_path / "plan.json"
    assert cli.main(
        [
            "decision",
            "sample",
            "--purpose",
            "train_r_annotation_pilot",
            "--split",
            split,
            "--size",
            "5",
            "--tasks-dir",
            str(paths["tasks"]),
            "--out",
            str(plan_path),
            "--verify",
        ]
    ) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["selected_size"] > 0
    assert plan["stratification"]["by_business_domain"] is False

    packet_dir = tmp_path / "packets"
    assert cli.main(
        [
            "decision",
            "export-annotations",
            "--plan",
            str(plan_path),
            "--tasks-dir",
            str(paths["tasks"]),
            "--out-dir",
            str(packet_dir),
        ]
    ) == 0
    packets = (packet_dir / "annotation_packets.jsonl").read_text(encoding="utf-8")
    assert packets.strip()
    assert "客服语句" in packets and "客户语句" in packets

    task_id = plan["selected"][0]["task_id"]
    submission = {
        "task_id": task_id,
        "annotator_id": "reviewer-a",
        "guideline_version": plan["guideline_version"],
        "annotation_status": "reviewed",
        "task_answerability": "sufficient_information",
        "primary_expected_decision": {
            "decision_id": "D1",
            "label": "占位决策",
            "match_keywords": ["占位决策"],
        },
        "required_steps": ["占位步骤"],
        "evidence_refs": ["visible_turn:2"],
        "provenance": {
            "authority": "human_primary_annotation",
            "annotator_id": "reviewer-a",
            "guideline_version": plan["guideline_version"],
        },
        "reviewer_confidence": 0.7,
    }
    submission_path = tmp_path / "subs.jsonl"
    submission_path.write_text(
        json.dumps(submission, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    assert cli.main(
        [
            "decision",
            "import-annotations",
            "--file",
            str(submission_path),
            "--tasks-dir",
            str(paths["tasks"]),
            "--out-dir",
            str(tmp_path / "annotations"),
        ]
    ) == 0

    agreement_out = tmp_path / "agreement.json"
    assert cli.main(
        [
            "decision",
            "agreement",
            "--submissions",
            str(tmp_path / "annotations" / "annotation_submissions.jsonl"),
            "--out",
            str(agreement_out),
        ]
    ) == 0
    report = json.loads(agreement_out.read_text(encoding="utf-8"))
    assert report["tasks_with_one_rating"] == 1
    assert report["statistics"]

    assert cli.main(
        [
            "decision",
            "adjudication-worklist",
            "--submissions",
            str(tmp_path / "annotations" / "annotation_submissions.jsonl"),
            "--out-dir",
            str(tmp_path / "annotations"),
        ]
    ) == 0
    assert (tmp_path / "annotations" / "adjudication_worklist.jsonl").is_file()


def test_export_annotations_refuses_a_test_plan(tmp_path: Path, capsys, monkeypatch) -> None:
    paths = _prepare(tmp_path, monkeypatch)
    plan = {
        "purpose": "train_r_annotation_pilot",
        "split": "test",
        "selected": [],
        "selection_sha256": "0" * 64,
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    code = cli.main(
        [
            "decision",
            "export-annotations",
            "--plan",
            str(plan_path),
            "--tasks-dir",
            str(paths["tasks"]),
            "--out-dir",
            str(tmp_path / "packets"),
        ]
    )
    assert code == 2
    assert "Test" in capsys.readouterr().err


def test_business_baseline_reports_the_pending_dependency(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    paths = _prepare(tmp_path, monkeypatch)
    store = TaskStore.load(paths["tasks"])
    split = next(
        name for name in ("dev", "train_f", "train_r") if store.counts_by_split().get(name)
    )
    task_id = store.task_ids_for_split(split)[0]
    traces_root = tmp_path / "traces"
    assert cli.main(
        [
            "decision",
            "run",
            "--agent",
            "scripted",
            "--split",
            split,
            "--task-id",
            task_id,
            "--tasks-dir",
            str(paths["tasks"]),
            "--split-manifest",
            str(paths["split"]),
            "--output-dir",
            str(traces_root),
            "--label",
            "baseline-fixture",
        ]
    ) == 0
    run_set = next(traces_root.glob("*baseline-fixture"))
    out = tmp_path / "report.md"
    assert cli.main(
        [
            "decision",
            "business-baseline",
            "--run-set",
            str(run_set),
            "--tasks-dir",
            str(paths["tasks"]),
            "--out",
            str(out),
        ]
    ) == 0
    text = out.read_text(encoding="utf-8")
    assert "Group A Business Baseline Report" in text
    assert "expert_adjudicated_decision_success_rate" in text
    assert "policy_verified_decision_success_rate" in text
    assert "not available" in text
    assert "pending dependencies" in text.lower()
    assert "占位" not in text
