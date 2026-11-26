"""Independent audit that a public task is exactly a real conversation prefix.

``check`` validates artifacts structurally. This module validates them *against
the reconstructed call*: for every task it rebuilds the speech prefix up to the
recorded cutoff and proves five things:

1. the cutoff record exists and is a speech record;
2. the visible context equals the real speech prefix, turn for turn;
3. the visible context stops exactly at the cutoff turn;
4. the historical next action reference equals the next real speech turn;
5. no call identifier, cutoff segment id or hidden segment id appears in the
   agent-visible payload.

Failure of any check is a leakage defect, so the audit returns explicit counts
instead of a boolean.

Deliberately **not** implemented: an n-gram overlap test between hidden turns and
the visible prefix. Real outbound calls repeat themselves ("您好在的", "不需要"),
so such a test reports coincidental repetition as leakage. Prefix equality (check
2) plus a cutoff-tail check (check 3) prove the same property without false
positives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import Field

from cm_outbound_bench.decisions.schema import PrivateTaskReference, PublicDecisionTask
from cm_outbound_bench.decisions.store import iter_public_tasks, iter_references
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.transcripts.models import NormalizedCall
from cm_outbound_bench.transcripts.store import iter_calls


class PrefixAudit(DomainModel):
    """Aggregate result of the prefix audit. Contains no conversation text."""

    tasks_checked: int = 0
    calls_checked: int = 0
    missing_calls: int = 0
    cutoff_not_speech: int = 0
    cutoff_seq_mismatch: int = 0
    visible_prefix_mismatch: int = 0
    visible_length_mismatch: int = 0
    visible_tail_mismatch: int = 0
    next_action_mismatch: int = 0
    hidden_segment_leak: int = 0
    call_id_leak: int = 0
    segment_id_leak: int = 0
    split_mismatch: int = 0
    failure_task_ids: list[str] = Field(default_factory=list)

    @property
    def failures(self) -> int:
        return (
            self.missing_calls
            + self.cutoff_not_speech
            + self.cutoff_seq_mismatch
            + self.visible_prefix_mismatch
            + self.visible_length_mismatch
            + self.visible_tail_mismatch
            + self.next_action_mismatch
            + self.hidden_segment_leak
            + self.call_id_leak
            + self.segment_id_leak
            + self.split_mismatch
        )

    @property
    def ok(self) -> bool:
        return self.tasks_checked > 0 and self.failures == 0


def audit_task_prefixes(
    public_path: str | Path,
    reference_path: str | Path,
    calls_path: str | Path,
    *,
    split_by_call: dict[str, str] | None = None,
) -> PrefixAudit:
    """Audit every task against the reconstructed call it came from."""
    audit = PrefixAudit()
    by_call: dict[str, list[tuple[PublicDecisionTask, PrivateTaskReference]]] = {}
    for public, reference in zip(
        iter_public_tasks(public_path), iter_references(reference_path), strict=True
    ):
        if public.task_id != reference.task_id:
            raise ValueError("public and private artifacts are misaligned")
        by_call.setdefault(reference.call_id, []).append((public, reference))

    seen_calls: set[str] = set()
    for call in iter_calls(calls_path):
        pending = by_call.get(call.call_id)
        if not pending:
            continue
        seen_calls.add(call.call_id)
        audit.calls_checked += 1
        for public, reference in pending:
            audit.tasks_checked += 1
            _audit_one(audit, call, public, reference, split_by_call)

    audit.missing_calls = sum(1 for call_id in by_call if call_id not in seen_calls)
    return audit


def _audit_one(
    audit: PrefixAudit,
    call: NormalizedCall,
    public: PublicDecisionTask,
    reference: PrivateTaskReference,
    split_by_call: dict[str, str] | None,
) -> None:
    failed = False
    turns = call.turns
    index = reference.cutoff_record_index
    if index < 0 or index >= len(turns):
        audit.cutoff_not_speech += 1
        failed = True
        _record_failure(audit, public.task_id, failed)
        return

    cutoff_turn = turns[index]
    if cutoff_turn.record_type != "speech" or cutoff_turn.seq != reference.cutoff_seq:
        audit.cutoff_seq_mismatch += 1
        failed = True

    prefix = [
        turn
        for turn in turns[: index + 1]
        if turn.record_type == "speech" and turn.is_speech
    ]
    expected_roles = ["customer" if turn.speaker_role == "customer" else "historical_agent" for turn in prefix]
    actual_roles = [turn.role for turn in public.visible_context]
    actual_contents = [turn.content for turn in public.visible_context]
    if actual_roles != expected_roles or actual_contents != [
        turn.text for turn in prefix
    ]:
        audit.visible_prefix_mismatch += 1
        failed = True
    if len(public.visible_context) != reference.cutoff_visible_turn_count:
        audit.visible_length_mismatch += 1
        failed = True
    if public.visible_context[-1].content != cutoff_turn.text:
        audit.visible_tail_mismatch += 1
        failed = True

    following = [
        turn
        for turn in turns[index + 1 :]
        if turn.record_type == "speech" and turn.is_speech
    ]
    if following:
        next_turn = following[0]
        if (
            reference.historical_next_action_reference != next_turn.text
            or reference.historical_next_action_segment_id
            != next_turn.source_segment_id
        ):
            audit.next_action_mismatch += 1
            failed = True

    public_payload = str(public.public_payload())
    for turn in following:
        if turn.source_segment_id in public_payload:
            audit.hidden_segment_leak += 1
            failed = True
            break
    if call.call_id in public_payload:
        audit.call_id_leak += 1
        failed = True
    if reference.cutoff_segment_id in public_payload:
        audit.segment_id_leak += 1
        failed = True

    if split_by_call is not None and split_by_call.get(call.call_id) != reference.split:
        audit.split_mismatch += 1
        failed = True

    _record_failure(audit, public.task_id, failed)


def _record_failure(audit: PrefixAudit, task_id: str, failed: bool) -> None:
    if failed and len(audit.failure_task_ids) < 50:
        audit.failure_task_ids.append(task_id)


def iter_audit_calls(calls_path: str | Path) -> Iterable[NormalizedCall]:
    """Yield calls for the audit. Kept separate so tests can substitute fixtures."""
    yield from iter_calls(calls_path)


__all__ = [
    "PrefixAudit",
    "audit_task_prefixes",
    "iter_audit_calls",
]
