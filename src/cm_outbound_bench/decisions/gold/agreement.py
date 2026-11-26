"""Semantic comparison of the two V4 Pro annotation passes.

The comparison works on the structured decision fields, not on textual equality.
It classifies each task as ``agreement``, ``acceptable_alternatives``,
``minor_disagreement``, ``major_disagreement`` or ``both_unscored``, and records
per-dimension agreement so the pilot analysis can see *what* differs.

This is an intra-model consistency check between two evaluations by the same
model. It is never presented as human inter-annotator reliability.
"""

from __future__ import annotations

import re
from typing import Sequence

from cm_outbound_bench.decisions.gold.models import (
    COMPARISON_ACCEPTABLE_ALTERNATIVES,
    COMPARISON_AGREEMENT,
    COMPARISON_BOTH_UNSCORED,
    COMPARISON_MAJOR,
    COMPARISON_MINOR,
    PassAnnotation,
    PassComparison,
)

#: Below this combined similarity two action statements are considered different
#: decisions, not different wordings. The measure is the max of char-3-gram
#: Jaccard and the longest-common-prefix ratio, so long Chinese sentences that
#: share their core but differ in a suffix stay recognised as one decision.
_DECISION_MATCH_THRESHOLD = 0.7
_DECISION_MINOR_THRESHOLD = 0.35

#: Below this Jaccard similarity a set field is considered materially different.
_SET_MATCH_THRESHOLD = 0.5

_CATEGORY_COMPATIBLE = {
    frozenset({"clarify", "verify_information"}),
    frozenset({"confirm", "verify_information"}),
    frozenset({"acknowledge", "close_conversation"}),
    frozenset({"explain", "answer"}),
    frozenset({"defer_pending_information", "clarify"}),
}

_CONFIDENCE_LEVEL = {"low": 0, "medium": 1, "high": 2}


def _normalize(text: str) -> str:
    return re.sub(r"\W+", "", text.casefold())


def _longest_common_prefix(left: str, right: str) -> int:
    length = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        length += 1
    return length


def _char_ngrams(text: str, size: int = 3) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def action_similarity(left: str, right: str) -> float:
    """Combined similarity of two action statements, normalized.

    Exact equality scores 1.0, containment 0.9, otherwise the maximum of the
    char-3-gram Jaccard similarity and the longest-common-prefix ratio. The
    prefix component keeps long Chinese sentences comparable when they share
    their core action but differ in a trailing detail.
    """
    left_text = _normalize(left)
    right_text = _normalize(right)
    if not left_text or not right_text:
        return 0.0
    if left_text == right_text:
        return 1.0
    if left_text in right_text or right_text in left_text:
        return 0.9
    left_set = _char_ngrams(left_text)
    right_set = _char_ngrams(right_text)
    union = len(left_set | right_set)
    jaccard = len(left_set & right_set) / union if union else 0.0
    prefix_ratio = _longest_common_prefix(left_text, right_text) / max(
        len(left_text), len(right_text)
    )
    return max(jaccard, prefix_ratio)


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float | None:
    left_set = {_normalize(item) for item in left if _normalize(item)}
    right_set = {_normalize(item) for item in right if _normalize(item)}
    if not left_set and not right_set:
        return None
    union = len(left_set | right_set)
    if not union:
        return None
    return len(left_set & right_set) / union


def _mutually_accepts(left: PassAnnotation, right: PassAnnotation) -> bool:
    """True when each pass lists the other pass's primary as an alternative."""
    left_primary = left.primary_expected_decision
    right_primary = right.primary_expected_decision
    if left_primary is None or right_primary is None:
        return False
    left_accepts_right = any(
        action_similarity(alt.action, right_primary.action) >= _DECISION_MATCH_THRESHOLD
        or alt.action == right_primary.action
        for alt in left.acceptable_alternative_decisions
    )
    right_accepts_left = any(
        action_similarity(alt.action, left_primary.action) >= _DECISION_MATCH_THRESHOLD
        or alt.action == left_primary.action
        for alt in right.acceptable_alternative_decisions
    )
    return left_accepts_right and right_accepts_left


def _prohibited_conflict(left: PassAnnotation, right: PassAnnotation) -> bool:
    """True only when one pass prohibits what the other pass requires.

    A mere difference in the generic constraint wording is not a conflict:
    both passes independently list "do not fabricate policy" style constraints,
    and those lists rarely match textually.
    """
    left_prohibited = {_normalize(item) for item in left.prohibited_steps}
    right_prohibited = {_normalize(item) for item in right.prohibited_steps}
    left_required = {
        _normalize(item)
        for item in [*left.required_steps, *left.required_information]
    }
    right_required = {
        _normalize(item)
        for item in [*right.required_steps, *right.required_information]
    }

    def _overlaps(prohibited: set[str], required: set[str]) -> bool:
        for item in prohibited:
            if not item:
                continue
            for needed in required:
                if not needed:
                    continue
                if item in needed or needed in item:
                    return True
        return False

    return _overlaps(left_prohibited, right_required) or _overlaps(
        right_prohibited, left_required
    )


def _categories_compatible(left: str, right: str) -> bool:
    if left == right:
        return True
    return frozenset({left, right}) in _CATEGORY_COMPATIBLE


def compare_passes(left: PassAnnotation, right: PassAnnotation) -> PassComparison:
    """Compare two independent passes for the same task."""
    if left.task_id != right.task_id:
        raise ValueError("cannot compare passes of different tasks")

    answerability_match = left.task_answerability == right.task_answerability
    scoreability_match = left.scoreable == right.scoreable
    both_unscored = (not left.scoreable) and (not right.scoreable)

    primary_left = left.primary_expected_decision
    primary_right = right.primary_expected_decision

    similarity = 0.0
    mutual_acceptance = False
    if primary_left is not None and primary_right is not None:
        similarity = action_similarity(primary_left.action, primary_right.action)
        mutual_acceptance = _mutually_accepts(left, right)

    info_jaccard = _jaccard(left.required_information, right.required_information)
    steps_jaccard = _jaccard(left.required_steps, right.required_steps)
    prohibited_jaccard = _jaccard(left.prohibited_steps, right.prohibited_steps)
    external_match = (
        left.external_policy_dependency == right.external_policy_dependency
    )
    confidence_match = left.confidence == right.confidence

    left_evidence = {item.visible_turn for item in left.evidence_refs}
    right_evidence = {item.visible_turn for item in right.evidence_refs}
    evidence_overlap = (
        (len(left_evidence & right_evidence) / len(left_evidence | right_evidence))
        if (left_evidence | right_evidence)
        else None
    )

    notes: list[str] = []
    if both_unscored:
        classification = COMPARISON_BOTH_UNSCORED
    elif not scoreability_match:
        classification = COMPARISON_MAJOR
        notes.append("the passes disagree on whether the task can be scored")
    elif not left.scoreable:
        classification = COMPARISON_BOTH_UNSCORED
    elif primary_left is None or primary_right is None:
        classification = COMPARISON_MAJOR
        notes.append("one pass established a decision and the other did not")
    elif not answerability_match:
        classification = COMPARISON_MAJOR
        notes.append(
            f"answerability differs: {left.task_answerability} vs "
            f"{right.task_answerability}"
        )
    elif mutual_acceptance:
        classification = COMPARISON_ACCEPTABLE_ALTERNATIVES
        notes.append("the passes accept each other's decisions as alternatives")
    elif primary_left.category == primary_right.category:
        if similarity >= _DECISION_MATCH_THRESHOLD and _steps_close(steps_jaccard):
            classification = COMPARISON_AGREEMENT
        else:
            classification = COMPARISON_MINOR
            notes.append("same decision family, different wording or step detail")
    elif _categories_compatible(primary_left.category, primary_right.category):
        classification = (
            COMPARISON_ACCEPTABLE_ALTERNATIVES
            if similarity >= _DECISION_MINOR_THRESHOLD
            else COMPARISON_MINOR
        )
        notes.append("compatible decision families")
    elif similarity >= _DECISION_MATCH_THRESHOLD:
        classification = COMPARISON_MINOR
        notes.append(
            "essentially the same action labelled with different categories"
        )
    else:
        classification = COMPARISON_MAJOR

    if (
        classification != COMPARISON_MAJOR
        and _prohibited_conflict(left, right)
    ):
        classification = COMPARISON_MAJOR
        notes.append(
            "one pass prohibits a step the other pass requires; real conflict"
        )
    if (
        classification == COMPARISON_AGREEMENT
        and info_jaccard is not None
        and info_jaccard < _SET_MATCH_THRESHOLD
    ):
        classification = COMPARISON_MINOR
        notes.append("required information sets differ")

    return PassComparison(
        task_id=left.task_id,
        classification=classification,
        answerability_match=answerability_match,
        scoreability_match=scoreability_match,
        category_match=(
            primary_left is not None
            and primary_right is not None
            and primary_left.category == primary_right.category
        )
        or (primary_left is None and primary_right is None),
        decision_similarity=round(similarity, 4),
        required_information_jaccard=(
            round(info_jaccard, 4) if info_jaccard is not None else None
        ),
        required_steps_jaccard=(
            round(steps_jaccard, 4) if steps_jaccard is not None else None
        ),
        prohibited_steps_jaccard=(
            round(prohibited_jaccard, 4) if prohibited_jaccard is not None else None
        ),
        external_policy_match=external_match,
        confidence_match=confidence_match,
        evidence_overlap=(
            round(evidence_overlap, 4) if evidence_overlap is not None else None
        ),
        notes=notes,
    )


def _steps_close(jaccard: float | None) -> bool:
    return jaccard is None or jaccard >= _SET_MATCH_THRESHOLD


__all__ = [
    "COMPARISON_ACCEPTABLE_ALTERNATIVES",
    "COMPARISON_AGREEMENT",
    "COMPARISON_BOTH_UNSCORED",
    "COMPARISON_MAJOR",
    "COMPARISON_MINOR",
    "PassComparison",
    "action_similarity",
    "compare_passes",
]
