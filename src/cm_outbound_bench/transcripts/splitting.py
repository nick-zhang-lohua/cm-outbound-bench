"""Call-level grouped dataset splitting with a reproducible manifest.

Rules implemented here:

* the unit of splitting is a complete **call**, never an individual turn;
* a duplicate or highly similar group of calls is assigned as one unit, so no
  member of a group can cross a split boundary;
* allocation is greedy against the configured targets, in a deterministic order
  derived from the seed, so the same corpus and seed always produce the same
  manifest;
* the manifest stores the per-call and per-group assignment plus a verification
  block, so a later stage can re-check the split without the corpus.

Targets: Test is 33.33% of valid calls; the remaining 66.67% is the development
pool, split 40% Train-F, 40% Train-R, 20% Dev. No domain label exists in the
corpus, so the split is **not** domain-stratified and the manifest says so
explicitly instead of claiming a stratification it cannot perform.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from pydantic import Field, model_validator

from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.validation import (
    CallFingerprint,
    group_duplicate_calls,
)

SPLIT_MANIFEST_VERSION = "1.0"
SPLIT_ALGORITHM = "grouped_greedy_relative_deficit_v1"

SPLIT_TRAIN_F = "train_f"
SPLIT_TRAIN_R = "train_r"
SPLIT_DEV = "dev"
SPLIT_TEST = "test"

DEVELOPMENT_SPLITS: tuple[str, ...] = (SPLIT_TRAIN_F, SPLIT_TRAIN_R, SPLIT_DEV)
ALL_SPLITS: tuple[str, ...] = (*DEVELOPMENT_SPLITS, SPLIT_TEST)


class SplitTargets(DomainModel):
    """Configured allocation. Shares of the development pool must sum to 1."""

    test_ratio: float = Field(default=1.0 / 3.0, gt=0.0, lt=1.0)
    train_f_share_of_pool: float = Field(default=0.40, gt=0.0, lt=1.0)
    train_r_share_of_pool: float = Field(default=0.40, gt=0.0, lt=1.0)
    dev_share_of_pool: float = Field(default=0.20, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def validate_pool_shares(self) -> "SplitTargets":
        total = (
            self.train_f_share_of_pool
            + self.train_r_share_of_pool
            + self.dev_share_of_pool
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError("development-pool shares must sum to 1.0")
        return self

    def target_calls(self, total: int) -> dict[str, float]:
        test = total * self.test_ratio
        pool = total - test
        return {
            SPLIT_TRAIN_F: pool * self.train_f_share_of_pool,
            SPLIT_TRAIN_R: pool * self.train_r_share_of_pool,
            SPLIT_DEV: pool * self.dev_share_of_pool,
            SPLIT_TEST: test,
        }


class SplitVerification(DomainModel):
    """Self-contained checks re-runnable from the manifest alone."""

    every_call_assigned_once: bool = False
    every_group_accounted_for: bool = False
    no_group_crosses_splits: bool = False
    assignment_matches_fingerprints: bool = True
    test_isolated_from_development: bool = False
    counts_match_assignment: bool = False
    is_reproducible: bool = True

    @property
    def ok(self) -> bool:
        return (
            self.every_call_assigned_once
            and self.every_group_accounted_for
            and self.no_group_crosses_splits
            and self.assignment_matches_fingerprints
            and self.test_isolated_from_development
            and self.counts_match_assignment
            and self.is_reproducible
        )

    def failures(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value is False]


class SplitManifest(DomainModel):
    """Reproducible record of a call-level split."""

    manifest_version: str = SPLIT_MANIFEST_VERSION
    algorithm: str = SPLIT_ALGORITHM
    generated_at: str = ""
    dataset_version: str = ""
    seed: int = 0
    targets: SplitTargets = Field(default_factory=SplitTargets)
    eligible_calls_total: int = 0
    excluded_invalid_calls: int = 0
    excluded_call_ids: list[str] = Field(default_factory=list)
    groups_total: int = 0
    singleton_groups: int = 0
    calls_in_duplicate_groups: int = 0
    near_duplicate_max_hamming: int = 3
    calls_by_split: dict[str, int] = Field(default_factory=dict)
    groups_by_split: dict[str, int] = Field(default_factory=dict)
    target_calls_by_split: dict[str, float] = Field(default_factory=dict)
    actual_percentage: dict[str, float] = Field(default_factory=dict)
    deviation_percentage_points: dict[str, float] = Field(default_factory=dict)
    development_pool_percentage: float = 0.0
    test_percentage: float = 0.0
    stratification: dict[str, object] = Field(default_factory=dict)
    duplicate_grouping: dict[str, object] = Field(default_factory=dict)
    input_fingerprint_sha256: str = ""
    split_by_call: dict[str, str] = Field(default_factory=dict)
    group_of_call: dict[str, str] = Field(default_factory=dict)
    verification: SplitVerification = Field(default_factory=SplitVerification)

    def calls_for(self, split: str) -> list[str]:
        """Return the sorted call ids assigned to one split."""
        return sorted(
            call_id
            for call_id, assigned in self.split_by_call.items()
            if assigned == split
        )


class SplitError(RuntimeError):
    """Raised when a split cannot be produced or verified."""


@dataclass(frozen=True)
class _Assignment:
    split_by_call: dict[str, str]
    split_by_group: dict[str, str]
    group_of_call: dict[str, str]
    group_sizes: dict[str, int]
    excluded_call_ids: list[str]


def _compute_assignment(
    fingerprints: Sequence[CallFingerprint],
    *,
    seed: int,
    targets: SplitTargets,
    near_duplicate_max_hamming: int,
) -> _Assignment:
    """Deterministic grouped allocation. Pure: no manifest, no recursion."""
    excluded = sorted(
        fingerprint.call_id
        for fingerprint in fingerprints
        if fingerprint.data_status == "invalid"
    )
    eligible = [
        fingerprint
        for fingerprint in fingerprints
        if fingerprint.data_status != "invalid"
    ]
    if not eligible:
        raise SplitError("no valid calls to split")

    group_of_call = group_duplicate_calls(
        eligible, max_hamming=near_duplicate_max_hamming
    )
    group_sizes: dict[str, int] = {}
    for group_id in group_of_call.values():
        group_sizes[group_id] = group_sizes.get(group_id, 0) + 1

    ordered_groups = sorted(
        group_sizes,
        key=lambda group_id: hashlib.sha256(
            f"{seed}:{group_id}".encode("utf-8")
        ).hexdigest(),
    )

    total = len(eligible)
    target_calls = targets.target_calls(total)
    pool_target = total - target_calls[SPLIT_TEST]

    stage_one = _allocate(
        [(group_id, group_sizes[group_id]) for group_id in ordered_groups],
        {SPLIT_TEST: target_calls[SPLIT_TEST], "pool": pool_target},
        order=(SPLIT_TEST, "pool"),
    )
    stage_two = _allocate(
        [
            (group_id, group_sizes[group_id])
            for group_id in ordered_groups
            if stage_one[group_id] == "pool"
        ],
        {
            SPLIT_TRAIN_F: pool_target * targets.train_f_share_of_pool,
            SPLIT_TRAIN_R: pool_target * targets.train_r_share_of_pool,
            SPLIT_DEV: pool_target * targets.dev_share_of_pool,
        },
        order=(SPLIT_TRAIN_F, SPLIT_TRAIN_R, SPLIT_DEV),
    )

    split_by_group = {
        group_id: (
            SPLIT_TEST if stage_one[group_id] == SPLIT_TEST else stage_two[group_id]
        )
        for group_id in ordered_groups
    }
    split_by_call = {
        fingerprint.call_id: split_by_group[group_of_call[fingerprint.call_id]]
        for fingerprint in eligible
    }
    return _Assignment(
        split_by_call=dict(sorted(split_by_call.items())),
        split_by_group=split_by_group,
        group_of_call=group_of_call,
        group_sizes=group_sizes,
        excluded_call_ids=excluded,
    )


def assign_splits(
    fingerprints: Iterable[CallFingerprint],
    *,
    seed: int,
    targets: SplitTargets | None = None,
    near_duplicate_max_hamming: int = 3,
    dataset_version: str = "",
) -> SplitManifest:
    """Split eligible calls into Train-F / Train-R / Dev / Test, grouped by call."""
    policy = targets or SplitTargets()
    materialized = list(fingerprints)
    if not materialized:
        raise SplitError("no calls to split")

    assignment = _compute_assignment(
        materialized,
        seed=seed,
        targets=policy,
        near_duplicate_max_hamming=near_duplicate_max_hamming,
    )
    total = len(assignment.split_by_call)
    target_calls = policy.target_calls(total)

    calls_by_split = {split: 0 for split in ALL_SPLITS}
    groups_by_split = {split: 0 for split in ALL_SPLITS}
    for split in assignment.split_by_call.values():
        calls_by_split[split] += 1
    for split in assignment.split_by_group.values():
        groups_by_split[split] += 1

    actual_percentage = {
        split: calls_by_split[split] / total * 100.0 for split in ALL_SPLITS
    }
    deviation = {
        split: actual_percentage[split] - target_calls[split] / total * 100.0
        for split in ALL_SPLITS
    }

    manifest = SplitManifest(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dataset_version=dataset_version,
        seed=seed,
        targets=policy,
        eligible_calls_total=total,
        excluded_invalid_calls=len(assignment.excluded_call_ids),
        excluded_call_ids=assignment.excluded_call_ids,
        groups_total=len(assignment.group_sizes),
        singleton_groups=sum(
            1 for size in assignment.group_sizes.values() if size == 1
        ),
        calls_in_duplicate_groups=sum(
            size for size in assignment.group_sizes.values() if size > 1
        ),
        near_duplicate_max_hamming=near_duplicate_max_hamming,
        calls_by_split=calls_by_split,
        groups_by_split=groups_by_split,
        target_calls_by_split={
            split: round(target_calls[split], 3) for split in ALL_SPLITS
        },
        actual_percentage={k: round(v, 4) for k, v in actual_percentage.items()},
        deviation_percentage_points={k: round(v, 4) for k, v in deviation.items()},
        development_pool_percentage=round(
            (total - calls_by_split[SPLIT_TEST]) / total * 100.0, 4
        ),
        test_percentage=round(actual_percentage[SPLIT_TEST], 4),
        stratification={
            "by_business_domain": False,
            "reason": (
                "The corpus carries no reliable business-domain label, so no "
                "domain stratification is claimed or performed."
            ),
        },
        duplicate_grouping={
            "exact_duplicate_method": "sha256 of the ordered record content",
            "near_duplicate_method": (
                "64-bit SimHash over CJK character bigrams, 4-band candidate "
                "filter, Hamming confirmation"
            ),
            "near_duplicate_max_hamming": near_duplicate_max_hamming,
        },
        input_fingerprint_sha256=_fingerprint_digest(
            [
                fingerprint
                for fingerprint in materialized
                if fingerprint.data_status != "invalid"
            ]
        ),
        split_by_call=assignment.split_by_call,
        group_of_call=dict(sorted(assignment.group_of_call.items())),
    )
    manifest.verification = verify_split_manifest(
        manifest,
        fingerprints=materialized,
        groups=assignment.group_of_call,
        replay=assignment.split_by_call,
    )
    return manifest


def verify_split_manifest(
    manifest: SplitManifest,
    *,
    fingerprints: Sequence[CallFingerprint] | None = None,
    groups: dict[str, str] | None = None,
    replay: dict[str, str] | None = None,
) -> SplitVerification:
    """Verify a manifest; with fingerprints, also prove it is reproducible."""
    split_by_call = manifest.split_by_call
    group_of_call = manifest.group_of_call

    every_call_assigned_once = bool(split_by_call) and all(
        split in ALL_SPLITS for split in split_by_call.values()
    ) and len(split_by_call) == manifest.eligible_calls_total

    group_splits: dict[str, set[str]] = {}
    for call_id, split in split_by_call.items():
        group_id = group_of_call.get(call_id)
        if group_id is None:
            every_call_assigned_once = False
            continue
        group_splits.setdefault(group_id, set()).add(split)

    every_group_accounted_for = (
        bool(group_splits)
        and set(group_splits) == set(group_of_call.values())
        and len(group_splits) == manifest.groups_total
    )
    no_group_crosses = bool(group_splits) and all(
        len(splits) == 1 for splits in group_splits.values()
    )

    test_groups = {group for group, splits in group_splits.items() if splits == {SPLIT_TEST}}
    development_groups = {
        group
        for group, splits in group_splits.items()
        if splits and splits <= set(DEVELOPMENT_SPLITS)
    }
    test_isolated = not (test_groups & development_groups) and len(
        test_groups
    ) + len(development_groups) == len(group_splits)

    recomputed = {split: 0 for split in ALL_SPLITS}
    for split in split_by_call.values():
        if split in recomputed:
            recomputed[split] += 1
    counts_match = all(
        manifest.calls_by_split.get(split, 0) == count
        for split, count in recomputed.items()
    )

    assignment_matches = True
    is_reproducible = True
    if fingerprints is not None:
        expected_calls = {
            fingerprint.call_id
            for fingerprint in fingerprints
            if fingerprint.data_status != "invalid"
        }
        assignment_matches = expected_calls == set(split_by_call)
        if groups is not None and assignment_matches:
            assignment_matches = all(
                group_of_call.get(fingerprint.call_id) == groups.get(fingerprint.call_id)
                for fingerprint in fingerprints
                if fingerprint.data_status != "invalid"
            )
        if assignment_matches:
            effective_replay = replay
            if effective_replay is None:
                effective_replay = _compute_assignment(
                    list(fingerprints),
                    seed=manifest.seed,
                    targets=manifest.targets,
                    near_duplicate_max_hamming=manifest.near_duplicate_max_hamming,
                ).split_by_call
            is_reproducible = effective_replay == split_by_call

    return SplitVerification(
        every_call_assigned_once=every_call_assigned_once,
        every_group_accounted_for=every_group_accounted_for,
        no_group_crosses_splits=no_group_crosses,
        assignment_matches_fingerprints=assignment_matches,
        test_isolated_from_development=test_isolated,
        counts_match_assignment=counts_match,
        is_reproducible=is_reproducible,
    )


def save_split_manifest(path: str | Path, manifest: SplitManifest) -> Path:
    """Write the split manifest as stable JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return target


def load_split_manifest(path: str | Path) -> SplitManifest:
    """Read a split manifest, failing loudly on malformed input."""
    target = Path(path)
    if not target.is_file():
        raise SplitError(f"split manifest not found: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SplitError(f"split manifest is not valid JSON: {target}") from error
    return SplitManifest.model_validate(payload)


def _allocate(
    groups: list[tuple[str, int]],
    targets: dict[str, float],
    *,
    order: tuple[str, ...],
) -> dict[str, str]:
    """Assign groups greedily to the bucket with the largest relative deficit."""
    assigned = {bucket: 0 for bucket in targets}
    result: dict[str, str] = {}
    for group_id, size in groups:
        best_bucket = None
        best_score = -math.inf
        for bucket in order:
            target = targets[bucket]
            if target <= 0:
                continue
            score = (target - assigned[bucket]) / target
            if score > best_score + 1e-12:
                best_score = score
                best_bucket = bucket
        if best_bucket is None:
            raise SplitError("no split bucket has a positive target")
        result[group_id] = best_bucket
        assigned[best_bucket] += size
    return result


def _fingerprint_digest(fingerprints: Sequence[CallFingerprint]) -> str:
    lines = sorted(
        f"{fingerprint.call_id}:{fingerprint.content_sha256}:{fingerprint.text_simhash}"
        for fingerprint in fingerprints
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


__all__ = [
    "ALL_SPLITS",
    "DEVELOPMENT_SPLITS",
    "SPLIT_ALGORITHM",
    "SPLIT_DEV",
    "SPLIT_MANIFEST_VERSION",
    "SPLIT_TEST",
    "SPLIT_TRAIN_F",
    "SPLIT_TRAIN_R",
    "SplitError",
    "SplitManifest",
    "SplitTargets",
    "SplitVerification",
    "assign_splits",
    "load_split_manifest",
    "save_split_manifest",
    "verify_split_manifest",
]
