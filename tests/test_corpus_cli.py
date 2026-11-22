from __future__ import annotations

import json
from pathlib import Path

import pytest

from cm_outbound_bench.cli import main
from corpus_support import PRIVATE_MARKER


@pytest.fixture
def corpus_args(tmp_path: Path, synthetic_corpus: Path) -> list[str]:
    return [
        "corpus",
        "import",
        "--database",
        str(tmp_path / "corpus.sqlite"),
        "--corpus-dir",
        str(synthetic_corpus),
    ]


def test_corpus_import_verify_and_stats(corpus_args, capsys) -> None:
    assert main(corpus_args) == 0
    import_output = capsys.readouterr().out
    assert "Status: imported" in import_output
    assert "Units: expected 3, imported 3" in import_output
    assert "Records: expected 9, imported 9" in import_output
    assert "Quarantine units: expected 2, imported 2" in import_output
    assert PRIVATE_MARKER not in import_output

    database = corpus_args[corpus_args.index("--database") + 1]

    assert main(["corpus", "verify", "--database", database]) == 0
    verify_output = capsys.readouterr().out
    assert "Result: OK" in verify_output
    assert "Foreign key errors: 0" in verify_output
    assert PRIVATE_MARKER not in verify_output

    assert main(["corpus", "stats", "--database", database]) == 0
    stats_output = capsys.readouterr().out
    assert "Transcript units: 3" in stats_output
    assert "Records: 9" in stats_output
    assert "speech: 6" in stats_output
    assert "event: 3" in stats_output
    assert "Domains: 6" in stats_output
    assert PRIVATE_MARKER not in stats_output


def test_corpus_import_second_run_reports_already_present(corpus_args, capsys) -> None:
    assert main(corpus_args) == 0
    capsys.readouterr()

    assert main(corpus_args) == 0
    output = capsys.readouterr().out
    assert "Status: already_present" in output
    assert PRIVATE_MARKER not in output


def test_corpus_json_outputs_are_machine_readable(corpus_args, capsys) -> None:
    assert main([*corpus_args, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "imported"
    assert payload["imported"]["transcript_units"] == 3

    database = corpus_args[corpus_args.index("--database") + 1]

    assert main(["corpus", "stats", "--database", database, "--json"]) == 0
    statistics = json.loads(capsys.readouterr().out)
    assert statistics["records"] == 9
    assert statistics["payload_types"] == {"EVENT": 3, "SPEECH": 6}

    assert main(["corpus", "verify", "--database", database, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True

    assert (
        main(["corpus", "quarantine-summary", "--database", database, "--json"]) == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["total_units"] == 2

    assert main(["corpus", "list-domains", "--database", database, "--json"]) == 0
    domains = json.loads(capsys.readouterr().out)
    assert len(domains) == 6
    assert domains[0]["domain_id"] == "D01"


def test_corpus_quarantine_and_domain_text_output(corpus_args, capsys) -> None:
    assert main(corpus_args) == 0
    database = corpus_args[corpus_args.index("--database") + 1]
    capsys.readouterr()

    assert main(["corpus", "quarantine-summary", "--database", database]) == 0
    output = capsys.readouterr().out
    assert "Quarantine units: 2" in output
    assert "AMBIGUOUS_PHONE" in output
    assert PRIVATE_MARKER not in output

    assert main(["corpus", "list-domains", "--database", database]) == 0
    output = capsys.readouterr().out
    assert "D02" in output
    assert "PROPOSED" in output
    assert "CROSS_CUTTING" in output


def test_corpus_commands_report_missing_database(tmp_path: Path, capsys) -> None:
    missing = str(tmp_path / "absent.sqlite")
    for command in ("verify", "stats", "quarantine-summary", "list-domains"):
        assert main(["corpus", command, "--database", missing]) == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert "not found" in captured.err


def test_corpus_import_reports_input_mismatch(tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "calls.jsonl").write_text("{}\n", encoding="utf-8")
    (corpus / "quarantine.jsonl").write_text("", encoding="utf-8")
    (corpus / "manifest.json").write_text("{}", encoding="utf-8")

    result = main(
        [
            "corpus",
            "import",
            "--database",
            str(tmp_path / "corpus.sqlite"),
            "--corpus-dir",
            str(corpus),
        ]
    )
    assert result == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert not (tmp_path / "corpus.sqlite").exists()


def test_legacy_commands_still_work_after_corpus_registration(capsys) -> None:
    """The corpus command group must not change the legacy marketing surface."""
    assert main(["list-tasks"]) == 0
    output = capsys.readouterr().out
    assert "marketing_direct_refusal" in output

    assert main(["run", "--task", "marketing_direct_refusal", "--agent", "demo"]) == 0
    output = capsys.readouterr().out
    assert "Result: PASS" in output
