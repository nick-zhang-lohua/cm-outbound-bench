"""Persisted, versioned episode traces.

A trace is the detailed, ordered source of truth for one episode. It is written
next to the run manifest and is referenced from it by path and SHA-256.

The trace is written atomically:

1. serialize the trace to canonical JSON;
2. validate the serialized artifact by re-parsing it and recomputing its hash;
3. scan the artifact for credential-shaped content (fail closed);
4. write a temporary file in the destination directory;
5. ``os.replace`` the temporary file onto the final path;
6. only then does the caller write a manifest that references it.

The SHA-256 recorded here is an **integrity check**, not a cryptographic
signature: it detects accidental or uncoordinated modification. It does not stop
a determined party from rewriting a trace *and* its hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field

from cm_outbound_bench.agent import ConversationMessage
from cm_outbound_bench.benchmark.state import hash_database
from cm_outbound_bench.domain.data_model import CmOutboundDB, DomainModel
from cm_outbound_bench.environment import ToolTraceEntry
from cm_outbound_bench.runner import RunAction, RunResult, SimulatorEvent

TRACE_SCHEMA_VERSION = "1.0"
TRACE_FILENAME = "trace.json"

#: Object keys that must never appear in a persisted trace.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "secret",
        "password",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "text_masked",
        "raw_transcript",
        "transcript_text",
        "source_file",
        "source_path",
        "hmac_key",
    }
)

#: Value shapes that look like real credentials rather than synthetic fixtures.
#: Only *content* patterns are listed here: the synthetic ``TEST_IDENTITY_*``
#: tokens and ``SIM_LINE_*`` numbers are deliberate public fixtures.
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{16,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"(?i)\bapi[_-]?key\s*[=:]\s*\S{8,}"),
    re.compile(r"(?i)\bauthorization\s*:\s*\S{8,}"),
)


class TraceError(Exception):
    """Base class for trace problems."""


class TraceSchemaError(TraceError):
    """The trace cannot be parsed or uses an incompatible schema version."""


class TraceIntegrityError(TraceError):
    """The trace content does not match its recorded hash or final state hash."""


class TraceSafetyError(TraceError):
    """The trace contains content that must never be persisted."""


# ---------------------------------------------------------------------------
# Trace schema
# ---------------------------------------------------------------------------


class TraceIntegrity(DomainModel):
    trace_hash: str = ""
    schema_version: str = TRACE_SCHEMA_VERSION
    created_at: str = ""


class TraceAgent(DomainModel):
    agent_type: str = ""
    provider_protocol: str = ""
    provider_name: str = ""
    model_name: str = ""
    prompt_hash: str = ""
    prompt_version: str = ""
    policy_hash: str = ""
    sampling: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    seed_status: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)


class TraceStop(DomainModel):
    stop_reason: str
    steps_executed: int = 0
    customer_conversation_closed: bool = False
    post_closure_actions: int = 0
    episode_completed: bool = False
    error_code: str = ""
    error_type: str = ""
    error_stage: str = ""
    error_message: str = ""


class TraceFinalState(DomainModel):
    final_state_hash: str
    database: CmOutboundDB
    completion_status: str = ""


class TraceEvaluationSnapshot(DomainModel):
    success: bool
    summary: str = ""
    partial_completion: float = 0.0
    policy_violations: list[str] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    dimensions: dict[str, dict[str, int]] = Field(default_factory=dict)
    answer_fact_results: list[dict[str, Any]] = Field(default_factory=list)
    evaluator_version: str = ""
    evaluator_hash: str = ""


class EpisodeTrace(DomainModel):
    """The complete, ordered record of one benchmark episode."""

    # identity
    trace_schema_version: str = TRACE_SCHEMA_VERSION
    run_id: str = ""
    episode_id: str = ""
    task_id: str
    domain_id: str
    scenario_id: str = ""
    task_version: str = ""
    benchmark_version: str = ""
    scenario_status: str = "CANDIDATE"
    rule_provenance_status: str = ""

    # initial state
    initial_state_id: str = ""
    initial_state_hash: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)

    # agent / provider
    agent: TraceAgent = Field(default_factory=TraceAgent)

    # ordered execution
    actions: list[RunAction] = Field(default_factory=list)
    conversation: list[ConversationMessage] = Field(default_factory=list)
    simulator_events: list[SimulatorEvent] = Field(default_factory=list)
    tool_calls: list[ToolTraceEntry] = Field(default_factory=list)
    consent_events: list[dict[str, Any]] = Field(default_factory=list)
    action_consent_events: list[dict[str, Any]] = Field(default_factory=list)

    # termination
    stop: TraceStop

    # final state
    final: TraceFinalState

    # recorded evaluation
    evaluation: TraceEvaluationSnapshot

    # integrity
    integrity: TraceIntegrity = Field(default_factory=TraceIntegrity)

    # -- hashing -----------------------------------------------------------

    def compute_hash(self) -> str:
        """Return the SHA-256 over the trace payload without its own hash."""
        payload = self.model_dump(
            mode="json",
            exclude={"integrity": {"trace_hash"}},
        )
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def serialized_hash(self) -> str:
        """Return the SHA-256 of the exact bytes written to disk."""
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def to_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ) + "\n"

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    # -- reconstruction ----------------------------------------------------

    def to_run_result(self) -> RunResult:
        """Rebuild the in-memory run result for deterministic re-evaluation.

        The rebuilt result is checked against the recorded final state hash so a
        corrupted snapshot cannot be silently evaluated.
        """
        recomputed = hash_database(self.final.database)
        if self.final.final_state_hash and recomputed != self.final.final_state_hash:
            raise TraceIntegrityError(
                "The trace's final database snapshot does not match its recorded "
                f"final_state_hash ({recomputed} != {self.final.final_state_hash})."
            )
        from cm_outbound_bench.runner import (
            ActionConsentEvent,
            ConsentEvent,
            EpisodeError,
            StopReason,
        )

        try:
            stop_reason = StopReason(self.stop.stop_reason)
        except ValueError as error:
            raise TraceSchemaError(
                f"Unknown stop reason {self.stop.stop_reason!r} in trace"
            ) from error
        error = None
        if self.stop.error_code:
            error = EpisodeError(
                code=self.stop.error_code,
                error_type=self.stop.error_type,
                stage=self.stop.error_stage,
                message=self.stop.error_message,
            )
        return RunResult(
            task_id=self.task_id,
            stop_reason=stop_reason,
            steps_executed=self.stop.steps_executed,
            conversation=[message.model_copy(deep=True) for message in self.conversation],
            tool_trace=[entry.model_copy(deep=True) for entry in self.tool_calls],
            consent_events=[
                ConsentEvent.model_validate(event) for event in self.consent_events
            ],
            final_db=self.final.database.deep_copy(),
            action_consent_events=[
                ActionConsentEvent.model_validate(event)
                for event in self.action_consent_events
            ],
            initial_state_id=self.initial_state_id,
            allowed_tools=list(self.allowed_tools),
            actions=[action.model_copy(deep=True) for action in self.actions],
            simulator_events=[
                event.model_copy(deep=True) for event in self.simulator_events
            ],
            customer_conversation_closed=self.stop.customer_conversation_closed,
            post_closure_actions=self.stop.post_closure_actions,
            error=error,
            agent_usage=dict(self.agent.token_usage),
        )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_trace(
    *,
    run_id: str,
    episode_id: str,
    task: Any,
    run: RunResult,
    evaluation: Any,
    benchmark_version: str,
    agent: TraceAgent,
    created_at: str | None = None,
) -> EpisodeTrace:
    """Assemble a trace from an executed episode."""
    from cm_outbound_bench.evaluator import evaluator_hash

    final_hash = hash_database(run.final_db)
    trace = EpisodeTrace(
        run_id=run_id,
        episode_id=episode_id,
        task_id=task.task_id,
        domain_id=task.domain_id,
        scenario_id=task.scenario_id,
        task_version=task.task_version,
        benchmark_version=benchmark_version,
        scenario_status=task.scenario_status,
        rule_provenance_status=task.rule_provenance_status,
        initial_state_id=run.initial_state_id or task.initial_state_id,
        initial_state_hash=_initial_state_hash(task),
        allowed_tools=list(run.allowed_tools or task.allowed_tools),
        required_tools=list(task.required_tools),
        agent=agent.model_copy(deep=True),
        actions=[action.model_copy(deep=True) for action in run.actions],
        conversation=[message.model_copy(deep=True) for message in run.conversation],
        simulator_events=[
            event.model_copy(deep=True) for event in run.simulator_events
        ],
        tool_calls=[entry.model_copy(deep=True) for entry in run.tool_trace],
        consent_events=[
            event.model_dump(mode="json") for event in run.consent_events
        ],
        action_consent_events=[
            event.model_dump(mode="json") for event in run.action_consent_events
        ],
        stop=TraceStop(
            stop_reason=run.stop_reason.value,
            steps_executed=run.steps_executed,
            customer_conversation_closed=run.customer_conversation_closed,
            post_closure_actions=run.post_closure_actions,
            episode_completed=run.episode_completed,
            error_code=run.error.code if run.error is not None else "",
            error_type=run.error.error_type if run.error is not None else "",
            error_stage=run.error.stage if run.error is not None else "",
            error_message=run.error.message if run.error is not None else "",
        ),
        final=TraceFinalState(
            final_state_hash=final_hash,
            database=run.final_db.deep_copy(),
            completion_status=(
                "completed" if run.episode_completed else "incomplete"
            ),
        ),
        evaluation=TraceEvaluationSnapshot(
            success=evaluation.success,
            summary=evaluation.summary,
            partial_completion=evaluation.partial_completion,
            policy_violations=list(evaluation.policy_violations),
            failed_checks=evaluation.failed_checks,
            dimensions=dict(evaluation.dimensions),
            answer_fact_results=list(evaluation.answer_fact_results),
            evaluator_version=evaluation.evaluator_version,
            evaluator_hash=evaluator_hash(),
        ),
        integrity=TraceIntegrity(created_at=created_at or _utc_now()),
    )
    return trace.model_copy(
        update={"integrity": trace.integrity.model_copy(
            update={"trace_hash": trace.compute_hash()}
        )}
    )


def _initial_state_hash(task: Any) -> str:
    from cm_outbound_bench.benchmark.execution import load_initial_state

    try:
        return load_initial_state(task).state_hash
    except Exception:  # noqa: BLE001 - a missing state is reported by validation
        return ""


# ---------------------------------------------------------------------------
# Safety scanning
# ---------------------------------------------------------------------------


def _scan(node: Any, path: str = "$") -> list[str]:
    problems: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            if key_text.lower() in FORBIDDEN_KEYS:
                problems.append(f"{path}.{key_text} uses a forbidden key name")
            problems.extend(_scan(value, f"{path}.{key_text}"))
        return problems
    if isinstance(node, list):
        for index, value in enumerate(node):
            problems.extend(_scan(value, f"{path}[{index}]"))
        return problems
    if isinstance(node, str):
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(node):
                problems.append(
                    f"{path} contains a credential-shaped value "
                    f"(matched {pattern.pattern!r})"
                )
                break
    return problems


def assert_trace_is_safe(trace: EpisodeTrace | dict[str, Any]) -> None:
    """Fail closed when a trace contains content that must not be persisted."""
    payload = trace.model_dump(mode="json") if isinstance(trace, EpisodeTrace) else trace
    problems = _scan(payload)
    if problems:
        raise TraceSafetyError(
            "Refusing to persist an unsafe trace: " + "; ".join(problems[:5])
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_trace(
    trace: EpisodeTrace,
    directory: str | Path,
    *,
    filename: str = TRACE_FILENAME,
) -> tuple[Path, str]:
    """Atomically write a trace and return ``(path, file_sha256)``."""
    assert_trace_is_safe(trace)
    expected_hash = trace.compute_hash()
    if trace.integrity.trace_hash and trace.integrity.trace_hash != expected_hash:
        raise TraceIntegrityError(
            "The trace hash does not match its own payload; refusing to write it."
        )
    trace = trace.model_copy(
        update={"integrity": trace.integrity.model_copy(
            update={"trace_hash": expected_hash}
        )}
    )

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename

    payload = trace.to_bytes()
    # Validate the serialized artifact before publishing it.
    try:
        reparsed = EpisodeTrace.model_validate_json(payload.decode("utf-8"))
    except Exception as error:  # noqa: BLE001 - reported as a trace error
        raise TraceSchemaError(
            f"The serialized trace could not be re-parsed: {error}"
        ) from error
    if reparsed.compute_hash() != expected_hash:
        raise TraceIntegrityError(
            "The serialized trace does not reproduce its recorded hash."
        )

    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=directory,
        prefix=".trace-",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target, hashlib.sha256(payload).hexdigest()


def load_trace(path: str | Path) -> EpisodeTrace:
    """Load a trace, validating its schema version and integrity hash."""
    trace_path = Path(path)
    try:
        payload = trace_path.read_text(encoding="utf-8")
    except OSError as error:
        raise TraceSchemaError(f"Trace file could not be read: {error}") from error
    try:
        trace = EpisodeTrace.model_validate_json(payload)
    except Exception as error:  # noqa: BLE001 - reported as a trace error
        raise TraceSchemaError(f"Trace file is not a valid trace: {error}") from error

    if trace.trace_schema_version != TRACE_SCHEMA_VERSION:
        raise TraceSchemaError(
            f"Unsupported trace schema version {trace.trace_schema_version!r}; "
            f"this build reads {TRACE_SCHEMA_VERSION!r}."
        )
    recorded = trace.integrity.trace_hash
    if not recorded:
        raise TraceIntegrityError("The trace does not record a content hash.")
    recomputed = trace.compute_hash()
    if recomputed != recorded:
        raise TraceIntegrityError(
            "The trace content does not match its recorded hash "
            f"({recomputed} != {recorded})."
        )
    if trace.final.final_state_hash:
        actual = hash_database(trace.final.database)
        if actual != trace.final.final_state_hash:
            raise TraceIntegrityError(
                "The trace's stored final state does not match its recorded "
                f"final_state_hash ({actual} != {trace.final.final_state_hash})."
            )
    assert_trace_is_safe(trace)
    return trace


def trace_file_sha256(path: str | Path) -> str:
    """Return the SHA-256 of the trace file bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "EpisodeTrace",
    "FORBIDDEN_KEYS",
    "TRACE_FILENAME",
    "TRACE_SCHEMA_VERSION",
    "TraceAgent",
    "TraceError",
    "TraceEvaluationSnapshot",
    "TraceFinalState",
    "TraceIntegrity",
    "TraceIntegrityError",
    "TraceSafetyError",
    "TraceSchemaError",
    "TraceStop",
    "assert_trace_is_safe",
    "build_trace",
    "load_trace",
    "trace_file_sha256",
    "write_trace",
]
