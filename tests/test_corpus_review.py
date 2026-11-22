from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cm_outbound_bench.corpus.classification import classify_corpus
from cm_outbound_bench.corpus.importer import import_corpus
from cm_outbound_bench.corpus.report import build_coverage_report, render_markdown
from cm_outbound_bench.corpus.review import apply_review, export_review_sample
from corpus_support import PRIVATE_MARKER, build_text_corpus

TEXTS = [
    ["这个套餐包含多少流量", "我想参加这个优惠活动"],
    ["我想取消这个优惠活动", "话费余额不足怎么充值"],
    ["需要确认机主本人的身份", "办完会下发短信通知吗"],
    ["这个再说吧", "嗯嗯好的"],
]


@pytest.fixture
def classified_database(tmp_path: Path) -> Path:
    corpus = build_text_corpus(tmp_path / "review-corpus", TEXTS)
    database = tmp_path / "review.sqlite"
    import_corpus(database_path=database, corpus_dir=corpus)
    classify_corpus(database_path=database)
    return database


def test_coverage_report_shape(classified_database: Path) -> None:
    report = build_coverage_report(database_path=classified_database)

    assert report.records_total == 8
    assert report.speech_records_total == 8
    assert report.eligible_speech_records == 8
    assert report.assigned_records > 0
    assert report.units_total == 4
    # The fourth fixture unit contains no domain evidence at all.
    assert report.units_assigned == 3
    assert report.unclassified_units == 1
    assert len(report.domains) == 6
    assert report.assignment_version == "1.0"
    unit_assignments = sum(domain.unit_assignments for domain in report.domains)
    record_assignments = sum(domain.record_assignments for domain in report.domains)
    assert report.assigned_records <= record_assignments
    assert report.review_status_mix == {
        "CANDIDATE": record_assignments + unit_assignments
    }
    assert any("not ground truth" in note for note in report.notes)
    for domain in report.domains:
        assert domain.taxonomy_status == "PROPOSED"
        assert 0.0 <= domain.record_coverage_pct <= 100.0


def test_coverage_markdown_contains_no_transcript_text(
    classified_database: Path,
) -> None:
    report = build_coverage_report(database_path=classified_database)
    markdown = render_markdown(report)

    assert "# Domain Coverage Report" in markdown
    assert "RULE_CANDIDATE" in markdown
    assert PRIVATE_MARKER not in markdown
    assert "这个套餐包含多少流量" not in markdown


def test_export_review_sample_withholds_text_by_default(
    tmp_path: Path, classified_database: Path
) -> None:
    destination = tmp_path / "review.json"
    sample = export_review_sample(
        output_path=destination,
        database_path=classified_database,
        per_stratum=2,
        seed=7,
    )

    assert destination.is_file()
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["include_text"] is False
    assert payload["seed"] == 7
    assert sample.items
    assert all(item.text_masked is None for item in sample.items)
    assert any(
        "Do not copy transcript text" in line for line in payload["instructions"]
    )


def test_export_review_sample_can_include_text_locally(
    tmp_path: Path, classified_database: Path
) -> None:
    destination = tmp_path / "review-local.json"
    sample = export_review_sample(
        output_path=destination,
        database_path=classified_database,
        per_stratum=2,
        seed=7,
        include_text=True,
    )

    assert any(item.text_masked for item in sample.items)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["include_text"] is True


def test_review_sample_is_reproducible(tmp_path: Path, classified_database: Path) -> None:
    first = export_review_sample(
        output_path=tmp_path / "a.json",
        database_path=classified_database,
        per_stratum=2,
        seed=11,
    )
    second = export_review_sample(
        output_path=tmp_path / "b.json",
        database_path=classified_database,
        per_stratum=2,
        seed=11,
    )

    assert [item.target_id for item in first.items] == [
        item.target_id for item in second.items
    ]


def test_apply_review_approves_and_rejects(
    tmp_path: Path, classified_database: Path
) -> None:
    sample_path = tmp_path / "review.json"
    sample = export_review_sample(
        output_path=sample_path,
        database_path=classified_database,
        per_stratum=2,
        seed=3,
    )
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    payload["items"][0]["reviewer_decision"] = "APPROVE"
    payload["items"][1]["reviewer_decision"] = "REJECT"
    for item in payload["items"][2:]:
        item["reviewer_decision"] = "PENDING"
    sample_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    report = apply_review(
        review_path=sample_path,
        reviewer="reviewer-01",
        database_path=classified_database,
    )

    assert report.items_read == len(sample.items)
    assert report.approved == 1
    assert report.rejected == 1
    assert report.skipped == len(sample.items) - 2
    assert report.errors == []

    with sqlite3.connect(classified_database) as connection:
        statuses = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT review_status FROM domain_assignments"
            ).fetchall()
        }
        reviewer = connection.execute(
            """
            SELECT DISTINCT reviewer_or_model FROM domain_assignments
             WHERE review_status IN ('HUMAN_REVIEWED', 'REJECTED')
            """
        ).fetchall()

    assert statuses == {"CANDIDATE", "HUMAN_REVIEWED", "REJECTED"}
    assert reviewer == [("reviewer-01",)]


def test_apply_review_change_replaces_candidate_labels(
    tmp_path: Path, classified_database: Path
) -> None:
    sample_path = tmp_path / "review.json"
    sample = export_review_sample(
        output_path=sample_path,
        database_path=classified_database,
        per_stratum=1,
        seed=5,
    )
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    target = payload["items"][0]["target_id"]
    payload["items"][0]["reviewer_decision"] = "CHANGE"
    payload["items"][0]["reviewer_domains"] = ["D03"]
    for item in payload["items"][1:]:
        item["reviewer_decision"] = "PENDING"
    sample_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = apply_review(
        review_path=sample_path,
        reviewer="reviewer-02",
        database_path=classified_database,
    )

    assert report.changed == 1
    assert report.adjudicated == 1
    assert sample.items[0].target_id == target

    with sqlite3.connect(classified_database) as connection:
        rows = connection.execute(
            """
            SELECT domain_id, label_method, review_status, confidence
              FROM domain_assignments
             WHERE target_type = 'RECORD' AND target_id = ?
            """,
            (target,),
        ).fetchall()

    assert rows == [("D03", "ADJUDICATED", "ADJUDICATED", 1.0)]


def test_review_change_without_domains_is_rejected(
    tmp_path: Path, classified_database: Path
) -> None:
    sample_path = tmp_path / "review.json"
    export_review_sample(
        output_path=sample_path,
        database_path=classified_database,
        per_stratum=1,
        seed=5,
    )
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    payload["items"][0]["reviewer_decision"] = "CHANGE"
    payload["items"][0]["reviewer_domains"] = []
    for item in payload["items"][1:]:
        item["reviewer_decision"] = "PENDING"
    sample_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = apply_review(
        review_path=sample_path,
        reviewer="reviewer-03",
        database_path=classified_database,
    )

    assert report.changed == 0
    assert report.errors
    assert "requires reviewer_domains" in report.errors[0]


def test_human_review_survives_a_reclassification(
    tmp_path: Path, classified_database: Path
) -> None:
    sample_path = tmp_path / "review.json"
    export_review_sample(
        output_path=sample_path,
        database_path=classified_database,
        per_stratum=1,
        seed=5,
    )
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    target = payload["items"][0]["target_id"]
    payload["items"][0]["reviewer_decision"] = "APPROVE"
    for item in payload["items"][1:]:
        item["reviewer_decision"] = "PENDING"
    sample_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    apply_review(
        review_path=sample_path,
        reviewer="reviewer-04",
        database_path=classified_database,
    )

    report = classify_corpus(database_path=classified_database)

    assert report.preserved_reviewed_records == 1
    with sqlite3.connect(classified_database) as connection:
        rows = connection.execute(
            """
            SELECT label_method, review_status FROM domain_assignments
             WHERE target_type = 'RECORD' AND target_id = ?
            """,
            (target,),
        ).fetchall()
    assert ("HUMAN_REVIEWED", "HUMAN_REVIEWED") in rows
    assert all(status != "CANDIDATE" for method, status in rows if method == "HUMAN_REVIEWED")


def test_apply_review_requires_a_reviewer(
    tmp_path: Path, classified_database: Path
) -> None:
    sample_path = tmp_path / "review.json"
    export_review_sample(
        output_path=sample_path,
        database_path=classified_database,
        per_stratum=1,
    )
    with pytest.raises(ValueError):
        apply_review(
            review_path=sample_path,
            reviewer="  ",
            database_path=classified_database,
        )
