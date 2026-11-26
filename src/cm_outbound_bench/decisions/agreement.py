"""Inter-annotator agreement statistics for business decision annotation.

The module reports agreement **before** adjudication. Reporting only the final
consensus labels hides disagreement, so every statistic here is computed from the
independent submissions, and adjudication statistics are reported separately.

Implemented:

``raw agreement``
    Proportion of units on which every rater used the same label.
``Cohen's kappa``
    Two raters, one categorical label, chance-corrected.
``Krippendorff's alpha``
    Nominal alpha that tolerates more than two raters and missing labels.
``set agreement``
    Precision, recall and Jaccard for multi-label fields such as required steps,
    where the label is a set rather than one category.

Everything is pure standard-library arithmetic; a ``None`` return means the
statistic is undefined for the data (for example, one category only, or fewer than
two raters), never that agreement was zero.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Iterable, Sequence

from pydantic import Field

from cm_outbound_bench.domain.data_model import DomainModel

AGREEMENT_VERSION = "1.0"

#: Below this value an agreement statistic is flagged for guideline revision.
POOR_AGREEMENT_THRESHOLD = 0.60


class AgreementStatistics(DomainModel):
    """One agreement statistic with its own denominator and interpretation."""

    name: str
    value: float | None = None
    units: int = 0
    raters: int = 0
    categories: int = 0
    interpretation: str = ""
    note: str = ""


class SetAgreement(DomainModel):
    """Set-based agreement for a multi-label field."""

    field_name: str
    pair_count: int = 0
    mean_precision: float | None = None
    mean_recall: float | None = None
    mean_jaccard: float | None = None
    interpretation: str = ""


class DisagreementRow(DomainModel):
    """One task on which the independent raters disagreed."""

    task_id: str
    raters: int
    labels: dict[str, str] = Field(default_factory=dict)
    set_fields: dict[str, list[str]] = Field(default_factory=dict)
    category: str = "unspecified"


class AgreementReport(DomainModel):
    """Complete pre-adjudication agreement report."""

    agreement_version: str = AGREEMENT_VERSION
    guideline_version: str = ""
    tasks_considered: int = 0
    tasks_with_two_or_more_ratings: int = 0
    tasks_with_one_rating: int = 0
    tasks_with_disagreement: int = 0
    raters: list[str] = Field(default_factory=list)
    statistics: list[AgreementStatistics] = Field(default_factory=list)
    set_statistics: list[SetAgreement] = Field(default_factory=list)
    disagreements: list[DisagreementRow] = Field(default_factory=list)
    guideline_revision_recommended: bool = False
    notes: list[str] = Field(default_factory=list)

    def value_of(self, name: str) -> float | None:
        for statistic in self.statistics:
            if statistic.name == name:
                return statistic.value
        return None


def raw_agreement(ratings: Sequence[Sequence[str]]) -> float | None:
    """Proportion of units on which all raters agree. ``None`` when empty."""
    considered = [unit for unit in ratings if len(unit) >= 2]
    if not considered:
        return None
    agreed = sum(1 for unit in considered if len(set(unit)) == 1)
    return agreed / len(considered)


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa for exactly two raters.

    ``None`` when there are no pairs or when chance agreement is exactly 1.0 (every
    rating in one category), because kappa is then undefined rather than perfect.
    """
    if len(pairs) < 2:
        return None
    total = len(pairs)
    observed = sum(1 for left, right in pairs if left == right) / total
    left_counts = Counter(left for left, _ in pairs)
    right_counts = Counter(right for _, right in pairs)
    categories = set(left_counts) | set(right_counts)
    expected = sum(
        (left_counts.get(category, 0) / total)
        * (right_counts.get(category, 0) / total)
        for category in categories
    )
    if math.isclose(expected, 1.0):
        return None
    return (observed - expected) / (1.0 - expected)


def krippendorff_alpha_nominal(units: Iterable[Sequence[str]]) -> float | None:
    """Krippendorff's nominal alpha for any number of raters and missing labels.

    ``None`` when fewer than two units are usable or when expected disagreement is
    zero (one observed category only).
    """
    coincidence: dict[tuple[str, str], float] = defaultdict(float)
    usable = 0
    for unit in units:
        values = [value for value in unit if value is not None]
        if len(values) < 2:
            continue
        usable += 1
        weight = 1.0 / (len(values) - 1)
        for index, left in enumerate(values):
            for other_index, right in enumerate(values):
                if index == other_index:
                    continue
                coincidence[(left, right)] += weight
    if usable < 2:
        return None

    marginals: dict[str, float] = defaultdict(float)
    total = 0.0
    for (left, _right), value in coincidence.items():
        marginals[left] += value
        total += value
    if total <= 0:
        return None

    observed_disagreement = sum(
        value for (left, right), value in coincidence.items() if left != right
    ) / total
    expected_disagreement = (
        total * total - sum(marginal * marginal for marginal in marginals.values())
    ) / (total * (total - 1))
    if expected_disagreement <= 0:
        return None
    return 1.0 - (observed_disagreement / expected_disagreement)


def set_agreement(
    left: Sequence[str], right: Sequence[str]
) -> tuple[float, float, float] | None:
    """Return ``(precision, recall, jaccard)`` for two label sets.

    Precision and recall are reported from the first rater's point of view, so a
    reviewer can see whether an annotator over-labels (low precision) or
    under-labels (low recall). ``None`` when both sets are empty.
    """
    left_set = {item for item in left if item.strip()}
    right_set = {item for item in right if item.strip()}
    if not left_set and not right_set:
        return None
    intersection = len(left_set & right_set)
    precision = intersection / len(left_set) if left_set else 0.0
    recall = intersection / len(right_set) if right_set else 0.0
    union = len(left_set | right_set)
    jaccard = intersection / union if union else 0.0
    return precision, recall, jaccard


def _interpret(value: float | None, *, name: str) -> str:
    if value is None:
        return f"{name} is undefined for this data"
    if value < 0.0:
        return "worse than chance"
    if value < 0.20:
        return "slight agreement"
    if value < 0.40:
        return "fair agreement"
    if value < POOR_AGREEMENT_THRESHOLD:
        return "moderate agreement"
    if value < 0.80:
        return "substantial agreement"
    return "almost perfect agreement"


def build_agreement_report(
    submissions: Iterable[object],
    *,
    guideline_version: str = "",
    decision_field: str = "decision_identity",
) -> AgreementReport:
    """Compute the pre-adjudication agreement report from submissions.

    *submissions* is any iterable of objects exposing ``task_id``,
    ``annotator_id``, ``decision_identities()``, ``required_steps``,
    ``optional_steps``, ``task_answerability`` and ``submission_identity``. The
    concrete type is
    :class:`cm_outbound_bench.decisions.annotation.AnnotationSubmission`.
    """
    materialized = list(submissions)
    report = AgreementReport(guideline_version=guideline_version)
    if not materialized:
        report.notes.append("no submissions to compare")
        return report

    by_task: dict[str, list[object]] = defaultdict(list)
    raters: set[str] = set()
    for submission in materialized:
        by_task[submission.task_id].append(  # type: ignore[attr-defined]
            submission
        )
        raters.add(submission.annotator_id)  # type: ignore[attr-defined]
    report.raters = sorted(raters)
    report.tasks_considered = len(by_task)

    multi = {
        task_id: items for task_id, items in by_task.items() if len(items) >= 2
    }
    report.tasks_with_two_or_more_ratings = len(multi)
    report.tasks_with_one_rating = len(by_task) - len(multi)

    decision_units: list[list[str]] = []
    pairs: list[tuple[str, str]] = []
    disagreements: list[DisagreementRow] = []
    step_pairs: list[tuple[float, float, float]] = []

    for task_id, items in sorted(by_task.items()):
        identities = [
            _decision_label(item, decision_field) for item in items
        ]
        decisions_by_annotator = {
            item.annotator_id: _decision_label(item, decision_field)  # type: ignore[attr-defined]
            for item in items
        }
        if len(items) >= 2:
            decision_units.append(identities)
            if len(items) == 2:
                pairs.append((identities[0], identities[1]))
            step_sets = [
                set(item.required_steps)  # type: ignore[attr-defined]
                for item in items
            ]
            for left_index in range(len(step_sets) - 1):
                for right_index in range(left_index + 1, len(step_sets)):
                    result = set_agreement(
                        sorted(step_sets[left_index]), sorted(step_sets[right_index])
                    )
                    if result is not None:
                        step_pairs.append(result)
            if len(set(identities)) > 1 or _sets_differ(items):
                disagreements.append(
                    DisagreementRow(
                        task_id=task_id,
                        raters=len(items),
                        labels=decisions_by_annotator,
                        set_fields={
                            item.annotator_id: sorted(item.required_steps)  # type: ignore[attr-defined]
                            for item in items
                        },
                        category=_classify_disagreement(items, identities),
                    )
                )

    raw = raw_agreement(decision_units)
    kappa = cohen_kappa(pairs) if pairs else None
    alpha = krippendorff_alpha_nominal(decision_units)
    categories = len({label for unit in decision_units for label in unit})

    report.statistics = [
        AgreementStatistics(
            name="raw_agreement",
            value=round(raw, 4) if raw is not None else None,
            units=len(decision_units),
            raters=len(report.raters),
            categories=categories,
            interpretation=_interpret(raw, name="raw agreement"),
            note="proportion of doubly-annotated tasks with identical decisions",
        ),
        AgreementStatistics(
            name="cohen_kappa",
            value=round(kappa, 4) if kappa is not None else None,
            units=len(pairs),
            raters=2,
            categories=categories,
            interpretation=_interpret(kappa, name="Cohen's kappa"),
            note="available only for tasks with exactly two independent ratings",
        ),
        AgreementStatistics(
            name="krippendorff_alpha",
            value=round(alpha, 4) if alpha is not None else None,
            units=len(decision_units),
            raters=len(
                {item.annotator_id for items in multi.values() for item in items}
            ),
            categories=categories,
            interpretation=_interpret(alpha, name="Krippendorff's alpha"),
            note="nominal alpha; tolerates more than two raters and missing labels",
        ),
    ]

    if step_pairs:
        precision = sum(item[0] for item in step_pairs) / len(step_pairs)
        recall = sum(item[1] for item in step_pairs) / len(step_pairs)
        jaccard = sum(item[2] for item in step_pairs) / len(step_pairs)
        report.set_statistics.append(
            SetAgreement(
                field_name="required_steps",
                pair_count=len(step_pairs),
                mean_precision=round(precision, 4),
                mean_recall=round(recall, 4),
                mean_jaccard=round(jaccard, 4),
                interpretation=_interpret(jaccard, name="Jaccard agreement"),
            )
        )

    report.disagreements = disagreements
    report.tasks_with_disagreement = len(disagreements)

    values = [
        statistic.value
        for statistic in report.statistics
        if statistic.value is not None
    ]
    values.extend(
        item.mean_jaccard for item in report.set_statistics
        if item.mean_jaccard is not None
    )
    report.guideline_revision_recommended = any(
        value < POOR_AGREEMENT_THRESHOLD for value in values
    )
    if report.guideline_revision_recommended:
        report.notes.append(
            "at least one agreement statistic is below "
            f"{POOR_AGREEMENT_THRESHOLD}; identify the disagreeing annotation "
            "categories or scenarios and revise the guideline before freezing it"
        )
    if report.tasks_with_one_rating:
        report.notes.append(
            f"{report.tasks_with_one_rating} task(s) have a single rating and do "
            "not contribute to any agreement statistic"
        )
    report.notes.append(
        "Pre-adjudication agreement is reported separately from the final "
        "adjudicated labels; the consensus result is never presented as agreement."
    )
    return report


def _decision_label(submission: object, field: str) -> str:
    """Return the categorical label a submission contributes.

    For the decision field the label is the set of accepted decisions, rendered as a
    stable sorted string, so two annotators choosing *different acceptable
    alternatives* from the same approved set still disagree at label level unless
    they chose the same primary decision. ``task_answerability`` is used as the
    fallback when no decision was defined, which is exactly the case where the
    correct answer is "ask for more information".
    """
    if field == "decision_identity":
        identities = sorted(submission.decision_identities())  # type: ignore[attr-defined]
        if identities:
            return "|".join(identities)
        return f"answerability:{submission.task_answerability}"  # type: ignore[attr-defined]
    return str(getattr(submission, field, ""))


def _sets_differ(items: Sequence[object]) -> bool:
    sets = [set(item.required_steps) for item in items]  # type: ignore[attr-defined]
    return any(other != sets[0] for other in sets[1:])


def _classify_disagreement(items: Sequence[object], identities: Sequence[str]) -> str:
    answerabilities = {item.task_answerability for item in items}  # type: ignore[attr-defined]
    if len(answerabilities) > 1:
        return "answerability"
    if len(set(identities)) > 1:
        return "decision_family"
    step_sets = [set(item.required_steps) for item in items]  # type: ignore[attr-defined]
    first = step_sets[0]
    if any(not first <= other for other in step_sets[1:]):
        return "required_step_missing"
    if any(not other <= first for other in step_sets[1:]):
        return "required_step_extra"
    return "guideline_ambiguity"


__all__ = [
    "AGREEMENT_VERSION",
    "POOR_AGREEMENT_THRESHOLD",
    "AgreementReport",
    "AgreementStatistics",
    "DisagreementRow",
    "SetAgreement",
    "build_agreement_report",
    "cohen_kappa",
    "krippendorff_alpha_nominal",
    "raw_agreement",
    "set_agreement",
]
