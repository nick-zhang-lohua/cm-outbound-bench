"""Verify that a stored execution consumed exactly the agent-visible payload.

A trace stores ``input_hash`` but not the prompt text. This module closes that gap:
it rebuilds the prompt from the guarded public payload of the same task and proves
the hash matches, so a trace cannot silently have been produced from a prompt that
contained a call id, a cutoff position or any other private field.

It also scans the reconstructed prompt for unambiguous private tokens (the call id
and the segment ids). Conversation text is never compared here: real calls repeat
phrases, so text matching would report repetition as leakage.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import Field

from cm_outbound_bench.decisions.agent_prompt import build_agent_request, prompt_sha256
from cm_outbound_bench.decisions.schema import PublicDecisionTask
from cm_outbound_bench.decisions.store import TaskStore
from cm_outbound_bench.decisions.trace import TraceRecord
from cm_outbound_bench.domain.data_model import DomainModel


class TraceInputVerification(DomainModel):
    """Aggregate result of the trace-input verification."""

    traces_checked: int = 0
    tasks_missing: int = 0
    prompt_hash_mismatches: int = 0
    private_token_leaks: int = 0
    failing_run_ids: list[str] = Field(default_factory=list)

    @property
    def failures(self) -> int:
        return (
            self.tasks_missing
            + self.prompt_hash_mismatches
            + self.private_token_leaks
        )

    @property
    def ok(self) -> bool:
        return self.traces_checked > 0 and self.failures == 0


def verify_trace_inputs(
    traces: Sequence[TraceRecord],
    store: TaskStore,
) -> TraceInputVerification:
    """Recompute every prompt from the guarded payload and compare the hash."""
    result = TraceInputVerification()
    for trace in traces:
        result.traces_checked += 1
        failed = False
        try:
            payload = store.agent_visible_payload(trace.task_id)
            reference = store.require_reference(trace.task_id)
        except Exception:
            result.tasks_missing += 1
            failed = True
            _record(result, trace.run_id, failed)
            continue

        task = PublicDecisionTask.model_validate(payload)
        request = build_agent_request(task)
        if prompt_sha256(request) != trace.input_hash:
            result.prompt_hash_mismatches += 1
            failed = True

        prompt = request.system_prompt + "\n" + request.user_prompt
        tokens = [reference.call_id, reference.cutoff_segment_id]
        if reference.historical_next_action_segment_id:
            tokens.append(reference.historical_next_action_segment_id)
        if any(token and token in prompt for token in tokens):
            result.private_token_leaks += 1
            failed = True
        _record(result, trace.run_id, failed)
    return result


def _record(
    result: TraceInputVerification,
    run_id: str,
    failed: bool,
) -> None:
    if failed and len(result.failing_run_ids) < 50:
        result.failing_run_ids.append(run_id)


__all__ = ["TraceInputVerification", "verify_trace_inputs"]
