"""Splitting tests: call grouping, reproducibility, allocation and verification."""

from __future__ import annotations

from pathlib import Path

from cm_outbound_bench.transcripts.models import NormalizedCall
from cm_outbound_bench.transcripts.splitting import (
    SPLIT_DEV,
    SPLIT_TEST,
    SPLIT_TRAIN_F,
    SPLIT_TRAIN_R,
    assign_splits,
    load_split_manifest,
    save_split_manifest,
    verify_split_manifest,
)
from cm_outbound_bench.transcripts.validation import group_duplicate_calls
from decision_support import (
    call_from,
    call_from_records,
    dialogue,
    distinct_label,
    fingerprints,
)


def _calls(count: int) -> list[NormalizedCall]:
    calls: list[NormalizedCall] = []
    for index in range(count):
        label = distinct_label(index)
        calls.append(
            call_from(
                f"CALL_FIXTURE_{index:04d}",
                [
                    (1, f"通话{label}客服占位开场语句内容"),
                    (2, f"通话{label}客户占位回应语句内容"),
                    (1, f"通话{label}客服占位结束语句内容"),
                ],
            )
        )
    return calls


def _fingerprints(count: int, *, max_hamming: int = 0):
    return fingerprints(_calls(count), max_hamming=max_hamming)


def test_no_call_crosses_a_split_and_every_call_is_assigned() -> None:
    calls = _calls(60)
    manifest = assign_splits(fingerprints(calls), seed=11, near_duplicate_max_hamming=0)
    assert len(manifest.split_by_call) == 60
    assert set(manifest.split_by_call.values()) <= {
        SPLIT_TRAIN_F,
        SPLIT_TRAIN_R,
        SPLIT_DEV,
        SPLIT_TEST,
    }
    assert all(value for value in manifest.verification.model_dump().values())


def test_duplicate_calls_are_grouped_and_kept_together() -> None:
    records = dialogue([(1, "占位客服完全相同"), (2, "占位客户完全相同")])
    duplicate_a = call_from_records("CALL_FIXTURE_DUP_A", records)
    duplicate_b = call_from_records("CALL_FIXTURE_DUP_B", records)
    others = _calls(40)
    manifest = assign_splits(
        fingerprints([duplicate_a, duplicate_b, *others]),
        seed=3,
        near_duplicate_max_hamming=0,
    )
    assert (
        manifest.group_of_call[duplicate_a.call_id]
        == manifest.group_of_call[duplicate_b.call_id]
    )
    assert (
        manifest.split_by_call[duplicate_a.call_id]
        == manifest.split_by_call[duplicate_b.call_id]
    )


def test_near_duplicates_are_grouped() -> None:
    base = "这是用于检测近似重复通话的占位句子内容甲乙丙丁戊己庚辛壬癸"
    near_a = call_from_records(
        "CALL_FIXTURE_NEAR_A", dialogue([(1, base), (2, "占位客户回应内容")])
    )
    near_b = call_from_records(
        "CALL_FIXTURE_NEAR_B",
        dialogue([(1, base + "。"), (2, "占位客户回应内容")]),
    )
    groups = group_duplicate_calls(fingerprints([near_a, near_b]), max_hamming=3)
    assert groups[near_a.call_id] == groups[near_b.call_id]


def test_same_seed_is_reproducible() -> None:
    fingerprints_list = _fingerprints(80)
    first = assign_splits(fingerprints_list, seed=20261125, near_duplicate_max_hamming=0)
    second = assign_splits(fingerprints_list, seed=20261125, near_duplicate_max_hamming=0)
    assert first.split_by_call == second.split_by_call
    assert first.group_of_call == second.group_of_call


def test_different_seed_changes_the_assignment() -> None:
    fingerprints_list = _fingerprints(80)
    first = assign_splits(fingerprints_list, seed=1, near_duplicate_max_hamming=0)
    second = assign_splits(fingerprints_list, seed=2, near_duplicate_max_hamming=0)
    assert first.split_by_call != second.split_by_call


def test_allocation_matches_two_to_one_then_forty_forty_twenty() -> None:
    manifest = assign_splits(_fingerprints(300), seed=5, near_duplicate_max_hamming=0)
    total = manifest.eligible_calls_total
    assert total == 300
    test_share = manifest.calls_by_split[SPLIT_TEST] / total
    assert abs(test_share - 1 / 3) < 0.02
    assert abs(manifest.development_pool_percentage - 66.6667) < 2.0
    pool = total - manifest.calls_by_split[SPLIT_TEST]
    assert abs(manifest.calls_by_split[SPLIT_TRAIN_F] / pool - 0.40) < 0.03
    assert abs(manifest.calls_by_split[SPLIT_TRAIN_R] / pool - 0.40) < 0.03
    assert abs(manifest.calls_by_split[SPLIT_DEV] / pool - 0.20) < 0.03
    assert manifest.calls_by_split[SPLIT_TRAIN_F] + manifest.calls_by_split[SPLIT_TRAIN_R] == (
        pool - manifest.calls_by_split[SPLIT_DEV]
    )


def test_invalid_calls_are_excluded_and_counted() -> None:
    calls = _calls(20)
    broken = call_from_records(
        "CALL_FIXTURE_INVALID",
        [
            {
                "seq": 0,
                "start_ms": 0,
                "end_ms": 100,
                "channel_id": 1,
                "role": "UNKNOWN",
                "payload_type": "EVENT",
                "event": "DTMF",
                "text_masked": None,
                "is_truncated": False,
                "asr_flags": [],
                "redaction_flags": [],
            }
        ],
    )
    assert broken.data_status == "invalid"
    manifest = assign_splits(
        fingerprints([*calls, broken]), seed=9, near_duplicate_max_hamming=0
    )
    assert broken.call_id not in manifest.split_by_call
    assert manifest.excluded_invalid_calls == 1
    assert manifest.excluded_call_ids == [broken.call_id]
    assert manifest.eligible_calls_total == 20


def test_manifest_roundtrip_and_self_verification(tmp_path: Path) -> None:
    manifest = assign_splits(_fingerprints(30), seed=4, near_duplicate_max_hamming=0)
    path = save_split_manifest(tmp_path / "split.json", manifest)
    reloaded = load_split_manifest(path)
    assert reloaded.split_by_call == manifest.split_by_call
    assert verify_split_manifest(reloaded).ok


def test_tampering_with_a_group_is_detected() -> None:
    records = dialogue([(1, "占位客服完全相同"), (2, "占位客户完全相同")])
    duplicate_a = call_from_records("CALL_FIXTURE_DUP_A", records)
    duplicate_b = call_from_records("CALL_FIXTURE_DUP_B", records)
    manifest = assign_splits(
        fingerprints([duplicate_a, duplicate_b, *_calls(20)]),
        seed=6,
        near_duplicate_max_hamming=0,
    )
    original = manifest.split_by_call[duplicate_b.call_id]
    tampered = SPLIT_TEST if original != SPLIT_TEST else SPLIT_DEV
    manifest.split_by_call[duplicate_b.call_id] = tampered
    verification = verify_split_manifest(manifest)
    assert verification.no_group_crosses_splits is False
    assert verification.ok is False


def test_manifest_records_domain_stratification_is_unavailable() -> None:
    manifest = assign_splits(_fingerprints(20), seed=1, near_duplicate_max_hamming=0)
    assert manifest.stratification["by_business_domain"] is False
    assert "no reliable business-domain label" in manifest.stratification["reason"]
    assert manifest.duplicate_grouping["near_duplicate_max_hamming"] == 0
