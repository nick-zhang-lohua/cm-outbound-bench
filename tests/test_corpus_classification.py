from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cm_outbound_bench.corpus.classification import (
    AMBIGUITY_DELTA,
    LEXICONS,
    classify_corpus,
    classify_text,
    explain_record,
    lexicon_manifest,
)
from cm_outbound_bench.corpus.importer import import_corpus
from corpus_support import build_text_corpus


def _domains(text: str) -> list[str]:
    return classify_text(text).domain_ids


def test_plan_question_is_labelled_d01() -> None:
    result = classify_text("我想问一下这个套餐每个月包含多少流量")
    assert result.eligible
    assert "D01" in result.domain_ids
    d01 = next(score for score in result.domains if score.domain_id == "D01")
    assert d01.confidence > 0.5
    assert "plan_product" in d01.strong_groups or "plan_content" in d01.strong_groups


def test_negated_promotion_term_is_not_evidence() -> None:
    assert "D02" not in _domains("我这边没有优惠")


def test_negated_cancellation_term_is_not_evidence() -> None:
    assert "D04" not in _domains("这个不用取消的")


def test_single_weak_term_is_below_threshold() -> None:
    assert classify_text("这个条件不太清楚").domain_ids == []


def test_two_weak_terms_reach_threshold() -> None:
    result = classify_text("这个条件限制是什么")
    assert result.domain_ids
    d02 = next(score for score in result.domains if score.domain_id == "D02")
    assert d02.strong_groups == ()
    assert set(d02.weak_groups) == {"offer_qualifier"}
    assert d02.confidence == pytest.approx(0.6)


def test_multi_label_record_is_not_forced_to_one_domain() -> None:
    domains = _domains("我想取消这个优惠活动，可以退订吗")
    assert "D02" in domains
    assert "D04" in domains


def test_billing_terms_are_labelled_d03() -> None:
    domains = _domains("上个月话费好像扣费多了，账单能查一下吗")
    assert "D03" in domains


def test_identity_terms_are_labelled_d05() -> None:
    domains = _domains("麻烦确认一下机主本人，需要实名核验身份证")
    assert "D05" in domains


def test_notification_terms_are_labelled_d06() -> None:
    domains = _domains("办理成功后会下发确认短信吗，有没有到账提醒")
    assert "D06" in domains


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (None, "no_text"),
        ("", "empty_text"),
        ("   ", "empty_text"),
        ("好的", "text_too_short"),
    ],
)
def test_ineligible_texts_are_reported_honestly(text, reason) -> None:
    result = classify_text(text)
    assert not result.eligible
    assert result.reason == reason
    assert result.domains == []


def test_classification_is_deterministic() -> None:
    text = "这个套餐的流量和优惠活动都介绍一下"
    first = [score.to_dict() for score in classify_text(text).domains]
    second = [score.to_dict() for score in classify_text(text).domains]
    assert first == second


def test_confidence_is_bounded() -> None:
    text = "套餐流量活动优惠赠送免费折扣话费欠费充值取消退订机主实名短信通知"
    result = classify_text(text)
    assert result.domains
    assert all(0.0 <= score.confidence <= 0.95 for score in result.domains)


def test_ambiguity_flag_uses_the_documented_delta() -> None:
    text = "取消这个优惠活动"
    result = classify_text(text)
    assert len(result.domains) >= 2
    gap = result.domains[0].confidence - result.domains[1].confidence
    assert result.is_ambiguous is (gap < AMBIGUITY_DELTA)


def test_every_domain_has_a_lexicon_with_evidence() -> None:
    domain_ids = {lexicon.domain_id for lexicon in LEXICONS}
    assert domain_ids == {"D01", "D02", "D03", "D04", "D05", "D06"}
    for lexicon in LEXICONS:
        assert lexicon.strong, f"{lexicon.domain_id} needs a strong group"
        for group in (*lexicon.strong, *lexicon.weak):
            assert all(len(term) >= 2 for term in group.terms)


def test_lexicon_manifest_is_reproducible() -> None:
    manifest = lexicon_manifest()
    assert manifest["assignment_version"] == "1.0"
    assert len(manifest["domains"]) == 6
    assert manifest["negation_cues"]


TEXTS = [
    ["这个套餐包含多少流量，每个月多少钱", "套餐的资费是怎么算的"],
    ["我想参加这个优惠活动，有什么条件", "活动有效期多久，到期会自动续费吗"],
    ["我的话费余额不足了，怎么充值", "上个月扣费好像多收了"],
    ["我想把套餐变更一下，怎么降级", "可以取消这个业务吗"],
    ["需要确认机主本人的身份", "实名校验需要身份证"],
    ["办完会下发短信通知吗", "确认短信什么时候到"],
    ["嗯嗯好的", "这个再说吧"],
]


@pytest.fixture
def text_database(tmp_path: Path) -> Path:
    corpus = build_text_corpus(tmp_path / "text-corpus", TEXTS)
    database = tmp_path / "text.sqlite"
    import_corpus(database_path=database, corpus_dir=corpus)
    return database


def test_classify_corpus_persists_candidate_assignments(text_database: Path) -> None:
    report = classify_corpus(database_path=text_database)

    assert report.eligible_records == 14
    assert report.skipped_records == 0
    assert report.record_assignments > 0
    assert report.unit_assignments > 0
    assert set(report.records_by_domain) <= {"D01", "D02", "D03", "D04", "D05", "D06"}

    with sqlite3.connect(text_database) as connection:
        rows = connection.execute(
            "SELECT DISTINCT label_method, review_status FROM domain_assignments"
        ).fetchall()
        primary_units = connection.execute(
            """
            SELECT target_id, COUNT(*) FROM domain_assignments
             WHERE target_type = 'TRANSCRIPT_UNIT' AND is_primary = 1
             GROUP BY target_id HAVING COUNT(*) > 1
            """
        ).fetchall()

    assert rows == [("RULE_CANDIDATE", "CANDIDATE")]
    assert primary_units == []


def test_classification_is_repeatable_without_duplicates(text_database: Path) -> None:
    first = classify_corpus(database_path=text_database)
    with sqlite3.connect(text_database) as connection:
        count_after_first = connection.execute(
            "SELECT COUNT(*) FROM domain_assignments"
        ).fetchone()[0]

    second = classify_corpus(database_path=text_database)

    with sqlite3.connect(text_database) as connection:
        count_after_second = connection.execute(
            "SELECT COUNT(*) FROM domain_assignments"
        ).fetchone()[0]

    assert second.record_assignments == first.record_assignments
    assert second.unit_assignments == first.unit_assignments
    assert count_after_first == count_after_second


def _all_lexicon_terms() -> set[str]:
    return {
        term
        for lexicon in LEXICONS
        for group in (*lexicon.strong, *lexicon.weak)
        for term in group.terms
    }


def test_explain_record_returns_only_curated_lexicon_terms(
    text_database: Path,
) -> None:
    classify_corpus(database_path=text_database)

    explanation = explain_record(
        "SYNTH-TEXT-0000", 0, database_path=text_database
    )

    assert explanation["transcript_id"] == "SYNTH-TEXT-0000"
    assert explanation["eligible"] is True
    assert "text_masked" not in explanation
    first = explanation["domains"][0]
    assert set(first) == {
        "domain_id",
        "confidence",
        "strong_groups",
        "weak_groups",
        "matched_terms",
    }
    # Only curated lexicon tokens may appear; no arbitrary transcript fragment.
    assert set(first["matched_terms"]) <= _all_lexicon_terms()
    full_text = TEXTS[0][0]
    assert full_text not in str(explanation)


def test_rule_based_labels_are_never_marked_reviewed(text_database: Path) -> None:
    classify_corpus(database_path=text_database)
    with sqlite3.connect(text_database) as connection:
        statuses = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT review_status FROM domain_assignments"
            ).fetchall()
        }
    assert statuses == {"CANDIDATE"}


def test_unit_rollup_marks_exactly_one_primary(text_database: Path) -> None:
    classify_corpus(database_path=text_database)
    with sqlite3.connect(text_database) as connection:
        units = connection.execute(
            """
            SELECT target_id, SUM(is_primary) FROM domain_assignments
             WHERE target_type = 'TRANSCRIPT_UNIT'
             GROUP BY target_id
            """
        ).fetchall()
    assert units
    assert all(total == 1 for _, total in units)
