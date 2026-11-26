"""Data-quality validation and duplicate grouping for reconstructed calls.

The report is aggregate by design: it counts records, flags and statuses and never
prints conversation content. That keeps the validation output safe to paste into a
report or a paper while still being fully reproducible from the corpus.

A "valid" call is one whose structure can be trusted: contiguous record sequence,
non-negative timing, ``end_ms >= start_ms`` and at least one speech record. A
"suspect" call is structurally sound but carries a timing anomaly. An "invalid"
call is excluded from the split, and the exclusion is counted in the report.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence

from pydantic import Field

from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.fingerprint import (
    form_groups,
    near_duplicate_pairs,
)
from cm_outbound_bench.transcripts.models import NormalizedCall

VALIDATION_VERSION = "1.0"

#: A call can only support a decision-point task when both sides actually spoke.
TASK_ELIGIBLE_STATUSES = frozenset({"valid", "suspect"})


class QualityIssue(DomainModel):
    """One aggregate data-quality finding."""

    code: str
    severity: str
    count: int = Field(ge=0)
    detail: str = ""


class CallFingerprint(DomainModel):
    """The compact fields needed for splitting, without any conversation text."""

    call_id: str
    data_status: str
    content_sha256: str
    text_simhash: str
    speech_record_count: int
    duration_ms: int
    has_customer_speech: bool
    has_agent_speech: bool
    business_domain: str = "unknown"


class CorpusValidationReport(DomainModel):
    """Aggregate corpus counts. Contains no transcript text."""

    validation_version: str = VALIDATION_VERSION
    generated_at: str = ""
    corpus_version: str = ""
    calls_total: int = 0
    data_status_counts: dict[str, int] = Field(default_factory=dict)
    records_total: int = 0
    speech_records_total: int = 0
    event_records_total: int = 0
    channel_record_counts: dict[str, int] = Field(default_factory=dict)
    flag_counts: dict[str, int] = Field(default_factory=dict)
    calls_with_both_speakers: int = 0
    calls_task_eligible: int = 0
    duplicate_groups: int = 0
    calls_in_duplicate_groups: int = 0
    largest_duplicate_group: int = 0
    exact_duplicate_calls: int = 0
    near_duplicate_max_hamming: int = 3
    duration_ms_p50: int = 0
    duration_ms_p90: int = 0
    duration_ms_max: int = 0
    issues: list[QualityIssue] = Field(default_factory=list)


class CorpusValidator:
    """Accumulate corpus statistics and fingerprints in one streaming pass."""

    def __init__(self, *, near_duplicate_max_hamming: int = 3) -> None:
        self._max_hamming = near_duplicate_max_hamming
        self._status_counts: dict[str, int] = {}
        self._channel_counts: dict[str, int] = {}
        self._flag_counts: dict[str, int] = {}
        self._fingerprints: list[CallFingerprint] = []
        self._content_hashes: dict[str, int] = {}
        self._records = 0
        self._speech = 0
        self._events = 0
        self._both = 0
        self._durations: list[int] = []
        self._corpus_version = ""
        self._calls = 0

    def add(self, call: NormalizedCall) -> None:
        self._calls += 1
        self._status_counts[call.data_status] = (
            self._status_counts.get(call.data_status, 0) + 1
        )
        self._records += call.record_count
        self._speech += call.speech_record_count
        self._events += call.event_record_count
        for channel, count in call.channel_counts.items():
            self._channel_counts[channel] = self._channel_counts.get(channel, 0) + count
        for flag in call.quality_flags:
            self._flag_counts[flag] = self._flag_counts.get(flag, 0) + 1
        self._durations.append(call.duration_ms)
        has_customer = "no_customer_speech" not in call.quality_flags
        has_agent = "no_agent_speech" not in call.quality_flags
        if has_customer and has_agent:
            self._both += 1
        self._fingerprints.append(
            CallFingerprint(
                call_id=call.call_id,
                data_status=call.data_status,
                content_sha256=call.content_sha256,
                text_simhash=call.text_simhash,
                speech_record_count=call.speech_record_count,
                duration_ms=call.duration_ms,
                has_customer_speech=has_customer,
                has_agent_speech=has_agent,
                business_domain=call.business_domain,
            )
        )
        self._content_hashes[call.content_sha256] = (
            self._content_hashes.get(call.content_sha256, 0) + 1
        )
        if not self._corpus_version:
            self._corpus_version = call.source.corpus

    @property
    def calls_seen(self) -> int:
        return self._calls

    @property
    def fingerprints(self) -> tuple[CallFingerprint, ...]:
        return tuple(self._fingerprints)

    def duplicate_groups(self) -> dict[str, str]:
        """Return ``call_id -> duplicate group id`` for all seen calls."""
        return group_duplicate_calls(
            self._fingerprints, max_hamming=self._max_hamming
        )

    def finish(self) -> CorpusValidationReport:
        groups = self.duplicate_groups()
        group_sizes: dict[str, int] = {}
        for group_id in groups.values():
            group_sizes[group_id] = group_sizes.get(group_id, 0) + 1
        multi_member_groups = [size for size in group_sizes.values() if size > 1]
        exact_duplicate_calls = sum(
            count for count in self._content_hashes.values() if count > 1
        )
        eligible = sum(
            1
            for fingerprint in self._fingerprints
            if fingerprint.data_status in TASK_ELIGIBLE_STATUSES
            and fingerprint.has_customer_speech
            and fingerprint.has_agent_speech
        )
        durations = sorted(self._durations)

        report = CorpusValidationReport(
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            corpus_version=self._corpus_version,
            calls_total=self._calls,
            data_status_counts=dict(sorted(self._status_counts.items())),
            records_total=self._records,
            speech_records_total=self._speech,
            event_records_total=self._events,
            channel_record_counts=dict(sorted(self._channel_counts.items())),
            flag_counts=dict(sorted(self._flag_counts.items())),
            calls_with_both_speakers=self._both,
            calls_task_eligible=eligible,
            duplicate_groups=len(multi_member_groups),
            calls_in_duplicate_groups=sum(multi_member_groups),
            largest_duplicate_group=max(multi_member_groups, default=0),
            exact_duplicate_calls=exact_duplicate_calls,
            near_duplicate_max_hamming=self._max_hamming,
            duration_ms_p50=_quantile(durations, 0.50),
            duration_ms_p90=_quantile(durations, 0.90),
            duration_ms_max=durations[-1] if durations else 0,
        )
        report.issues = _issues_for(report)
        return report


def group_duplicate_calls(
    fingerprints: Sequence[CallFingerprint],
    *,
    max_hamming: int = 3,
) -> dict[str, str]:
    """Group exact and highly similar calls.

    Exact duplicates are grouped by content hash. Near duplicates are grouped by
    SimHash Hamming distance, and transitive closure keeps a chain of near
    duplicates in a single group so no member can be split off.
    """
    identifiers = [fingerprint.call_id for fingerprint in fingerprints]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate call_id in fingerprints")

    pairs: set[tuple[str, str]] = set()

    by_content: dict[str, list[str]] = {}
    for fingerprint in fingerprints:
        by_content.setdefault(fingerprint.content_sha256, []).append(fingerprint.call_id)
    for members in by_content.values():
        ordered = sorted(members)
        for index in range(len(ordered) - 1):
            pairs.add((ordered[index], ordered[index + 1]))

    signatures: dict[str, int] = {}
    for fingerprint in fingerprints:
        value = int(fingerprint.text_simhash, 16)
        if value:
            signatures[fingerprint.call_id] = value
    pairs.update(near_duplicate_pairs(signatures, max_hamming=max_hamming))

    return form_groups(identifiers, sorted(pairs))


def validate_calls(
    calls: Iterable[NormalizedCall],
    *,
    near_duplicate_max_hamming: int = 3,
) -> tuple[CorpusValidationReport, dict[str, str]]:
    """Validate a materialized collection of calls and group duplicates."""
    validator = CorpusValidator(near_duplicate_max_hamming=near_duplicate_max_hamming)
    for call in calls:
        validator.add(call)
    report = validator.finish()
    return report, validator.duplicate_groups()


def iter_records(calls: Iterable[NormalizedCall]) -> Iterator[tuple[str, str]]:
    """Yield ``(call_id, status)`` pairs; a small helper for CLI summaries."""
    for call in calls:
        yield call.call_id, call.data_status


def _quantile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, max(0, int(round(fraction * (len(sorted_values) - 1)))))
    return sorted_values[index]


def _issues_for(report: CorpusValidationReport) -> list[QualityIssue]:
    issues: list[QualityIssue] = [
        QualityIssue(
            code="speaker_roles_from_channel_mapping",
            severity="info",
            count=report.calls_total,
            detail=(
                "Speaker roles are assigned by the research protocol's original "
                "channel mapping (1=historical agent, 2=customer). The corpus "
                "itself stores role=UNKNOWN and does not encode the mapping."
            ),
        ),
        QualityIssue(
            code="no_call_metadata_in_source",
            severity="info",
            count=report.calls_total,
            detail=(
                "The source has no caller/called number, customer id, agent id, "
                "natural timestamp or call_id. Cross-call client linkage is "
                "therefore unavailable and no customer grouping is applied."
            ),
        ),
        QualityIssue(
            code="no_business_domain_labels",
            severity="info",
            count=report.calls_total,
            detail=(
                "The source carries no business-domain label. Every business "
                "domain is recorded as unknown; the split is not domain-stratified."
            ),
        ),
        QualityIssue(
            code="event_records_preserved",
            severity="info",
            count=report.event_records_total,
            detail=(
                "Non-speech events are preserved on the timeline and excluded "
                "from visible conversation context."
            ),
        ),
    ]
    for code, count in sorted(report.flag_counts.items()):
        severity = "warning" if code in {
            "timing_reset",
            "timing_inversion",
            "duration_mismatch",
        } else "info"
        issues.append(QualityIssue(code=code, severity=severity, count=count))
    invalid = report.data_status_counts.get("invalid", 0)
    if invalid:
        issues.append(
            QualityIssue(
                code="invalid_calls_excluded",
                severity="warning",
                count=invalid,
                detail="Structurally invalid calls are excluded from the split.",
            )
        )
    if report.calls_in_duplicate_groups:
        issues.append(
            QualityIssue(
                code="duplicate_groups_kept_together",
                severity="info",
                count=report.calls_in_duplicate_groups,
                detail=(
                    "Exact and near-duplicate calls are grouped and assigned to a "
                    "single split."
                ),
            )
        )
    return issues


__all__ = [
    "VALIDATION_VERSION",
    "CallFingerprint",
    "CorpusValidationReport",
    "CorpusValidator",
    "QualityIssue",
    "TASK_ELIGIBLE_STATUSES",
    "group_duplicate_calls",
    "iter_records",
    "validate_calls",
]
