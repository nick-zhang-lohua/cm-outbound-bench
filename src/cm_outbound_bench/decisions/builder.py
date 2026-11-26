"""Construct decision-point tasks from real conversation prefixes.

A decision point is the position **after the last customer message of a
consecutive customer-message sequence, immediately before a subsequent historical
agent response**. In other words: a position ``i`` where speech turn ``i`` is from
the customer and speech turn ``i + 1`` is from the historical agent.

The task then contains speech turns ``0 .. i`` and nothing else. The historical
agent's next response is recorded only in the private reference, as an observed
reference, never as a gold label. That is the whole leakage boundary:

* the visible context is a prefix of the real call;
* every later customer turn, later agent turn and the final outcome stay private;
* no simulator, no generated customer turn, no synthesized transcript.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Sequence

from pydantic import Field

from cm_outbound_bench.decisions.schema import (
    TASK_INSTRUCTION,
    PrivateTaskReference,
    PublicDecisionTask,
    VisibleTurn,
    make_task_id,
)
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.models import NormalizedCall, Turn

BUILDER_VERSION = "1.0"

#: Reasons a call or a cutoff can be rejected. Recorded as aggregate counts.
REJECT_INVALID_CALL = "invalid_call"
REJECT_NO_CUSTOMER_TURN = "no_customer_turn"
REJECT_NO_AGENT_TURN = "no_agent_turn"
REJECT_NO_ELIGIBLE_CUTOFF = "no_eligible_cutoff"
REJECT_OTHER_SPEAKER_IN_PREFIX = "other_speaker_in_prefix"
REJECT_PREFIX_TOO_SHORT = "prefix_too_short"
REJECT_DUPLICATE_PREFIX = "duplicate_visible_prefix"


class BuilderConfig(DomainModel):
    """Task-construction policy. Fixed before any evaluation is inspected."""

    max_tasks_per_call: int = Field(default=3, ge=1, le=20)
    min_visible_turns: int = Field(default=2, ge=1)
    min_cutoff_gap: int = Field(
        default=4, ge=1, description="Minimum speech turns between two cutoffs."
    )
    selection: str = Field(default="even_spread", pattern="^(even_spread|earliest)$")


class BuildStats(DomainModel):
    """Aggregate construction counters. Contains no conversation text."""

    builder_version: str = BUILDER_VERSION
    calls_scanned: int = 0
    calls_with_tasks: int = 0
    calls_skipped: int = 0
    tasks_built: int = 0
    eligible_cutoffs_seen: int = 0
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    tasks_per_call_counts: dict[str, int] = Field(default_factory=dict)


class DecisionTaskBuilder:
    """Stateful, deterministic builder: one call in, zero or more tasks out."""

    def __init__(self, config: BuilderConfig | None = None) -> None:
        self._config = config or BuilderConfig()
        self._seen_prefixes: set[str] = set()
        self._stats = BuildStats()
        self._constructed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    @property
    def config(self) -> BuilderConfig:
        return self._config

    @property
    def stats(self) -> BuildStats:
        return self._stats

    def _reject(self, reason: str) -> None:
        counts = self._stats.rejection_counts
        counts[reason] = counts.get(reason, 0) + 1

    def add_call(
        self,
        call: NormalizedCall,
        *,
        split: str,
    ) -> list[tuple[PublicDecisionTask, PrivateTaskReference]]:
        """Build every eligible task for one call."""
        self._stats.calls_scanned += 1
        if call.data_status == "invalid":
            self._stats.calls_skipped += 1
            self._reject(REJECT_INVALID_CALL)
            return []

        speech = call.speech_turns
        if not any(turn.speaker_role == "customer" for turn in speech):
            self._stats.calls_skipped += 1
            self._reject(REJECT_NO_CUSTOMER_TURN)
            return []
        if not any(turn.speaker_role == "historical_agent" for turn in speech):
            self._stats.calls_skipped += 1
            self._reject(REJECT_NO_AGENT_TURN)
            return []

        cutoffs = self._eligible_cutoffs(speech)
        self._stats.eligible_cutoffs_seen += len(cutoffs)
        if not cutoffs:
            self._stats.calls_skipped += 1
            self._reject(REJECT_NO_ELIGIBLE_CUTOFF)
            return []

        built: list[tuple[PublicDecisionTask, PrivateTaskReference]] = []
        for cutoff in self._select_cutoffs(cutoffs):
            task = self._build_one(call, speech, cutoff, split=split)
            if task is not None:
                built.append(task)

        if built:
            self._stats.calls_with_tasks += 1
            self._stats.tasks_built += len(built)
            key = str(len(built))
            self._stats.tasks_per_call_counts[key] = (
                self._stats.tasks_per_call_counts.get(key, 0) + 1
            )
        else:
            self._stats.calls_skipped += 1
        return built

    def _eligible_cutoffs(self, speech: Sequence[Turn]) -> list[int]:
        """Return indices ``i`` where speech[i] is customer and speech[i+1] agent."""
        config = self._config
        cutoffs: list[int] = []
        for index in range(len(speech) - 1):
            current, following = speech[index], speech[index + 1]
            if current.speaker_role != "customer":
                continue
            if following.speaker_role != "historical_agent":
                continue
            if index + 1 < config.min_visible_turns:
                self._reject(REJECT_PREFIX_TOO_SHORT)
                continue
            prefix = speech[: index + 1]
            if any(turn.speaker_role == "other" for turn in prefix):
                self._reject(REJECT_OTHER_SPEAKER_IN_PREFIX)
                continue
            cutoffs.append(index)
        return cutoffs

    def _select_cutoffs(self, cutoffs: list[int]) -> list[int]:
        """Choose spread-out cutoffs, honouring the per-call cap and gap."""
        config = self._config
        count = min(config.max_tasks_per_call, len(cutoffs))
        if count <= 0:
            return []
        if config.selection == "earliest":
            candidates = cutoffs[:count]
        elif count == 1:
            # Prefer the richest context when only one cutoff is allowed.
            candidates = [cutoffs[-1]]
        else:
            span = len(cutoffs) - 1
            positions = sorted(
                {round(step * span / (count - 1)) for step in range(count)}
            )
            candidates = [cutoffs[position] for position in positions]

        selected: list[int] = []
        for candidate in candidates:
            if selected and candidate - selected[-1] < config.min_cutoff_gap:
                continue
            selected.append(candidate)
        return selected

    def _build_one(
        self,
        call: NormalizedCall,
        speech: Sequence[Turn],
        cutoff: int,
        *,
        split: str,
    ) -> tuple[PublicDecisionTask, PrivateTaskReference] | None:
        prefix = speech[: cutoff + 1]
        visible = [
            VisibleTurn(
                role="customer"
                if turn.speaker_role == "customer"
                else "historical_agent",
                content=turn.text,
            )
            for turn in prefix
        ]
        fingerprint = _prefix_fingerprint(visible)
        if fingerprint in self._seen_prefixes:
            self._reject(REJECT_DUPLICATE_PREFIX)
            return None
        self._seen_prefixes.add(fingerprint)

        cutoff_turn = prefix[-1]
        next_turn = speech[cutoff + 1]
        public = PublicDecisionTask(
            task_id=make_task_id(call.call_id, cutoff_turn.seq),
            business_domain=call.business_domain,
            visible_context=visible,
            agent_task={"instruction": TASK_INSTRUCTION},
        )
        reference = PrivateTaskReference(
            task_id=public.task_id,
            call_id=call.call_id,
            split=split,
            # ``turn_id`` is the 1-based position in the call timeline, so this is
            # the original cutoff position without a second lookup.
            cutoff_record_index=cutoff_turn.turn_id - 1,
            cutoff_seq=cutoff_turn.seq,
            cutoff_segment_id=cutoff_turn.source_segment_id,
            cutoff_start_ms=cutoff_turn.start_ms,
            cutoff_visible_turn_count=len(visible),
            hidden_speech_turn_count=len(speech) - len(prefix),
            historical_next_action_reference=next_turn.text,
            historical_next_action_segment_id=next_turn.source_segment_id,
            scoring_status="unscored",
            annotation_status="pending",
            verified_business_rules=[],
            expected_decisions=[],
            evidence_refs=[cutoff_turn.source_segment_id],
            data_quality_status=call.data_status,
            builder_version=BUILDER_VERSION,
            constructed_at=self._constructed_at,
        )
        return public, reference


def _prefix_fingerprint(visible: Sequence[VisibleTurn]) -> str:
    payload = json.dumps(
        [[turn.role, turn.content] for turn in visible],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "BUILDER_VERSION",
    "BuildStats",
    "BuilderConfig",
    "DecisionTaskBuilder",
    "REJECT_DUPLICATE_PREFIX",
    "REJECT_INVALID_CALL",
    "REJECT_NO_AGENT_TURN",
    "REJECT_NO_CUSTOMER_TURN",
    "REJECT_NO_ELIGIBLE_CUTOFF",
    "REJECT_OTHER_SPEAKER_IN_PREFIX",
    "REJECT_PREFIX_TOO_SHORT",
]
