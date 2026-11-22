"""Shared single-loop runner for every benchmark domain.

One loop drives every episode: the legacy five marketing tasks, the D02
TXT-driven slice and the D01/D03/D04/D05/D06 domain tasks all execute here. A
domain supplies a task view, a simulator, a toolkit factory and an optional tool
allow-list; it never supplies its own loop.

Two different terminations are distinguished:

``customer_conversation_closed``
    The simulated customer has ended the conversation. No further simulator turn
    is requested, but the agent keeps the turn. It may still send a closing
    message — for example the acknowledgement owed to a customer who has just
    declined — and perform permitted post-conversation business operations
    (enrollment, a simulated confirmation) before finishing. Those messages and
    calls are counted in ``post_closure_actions`` and bounded by
    ``EpisodeSpec.max_post_closure_actions``.

``episode_completed`` / ``stop_reason``
    The benchmark episode itself stopped, for a structured, recorded reason.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import Field

from cm_outbound_bench.agent import (
    Agent,
    AgentContext,
    CallToolAction,
    ConversationMessage,
    FinishAction,
    SayAction,
)
from cm_outbound_bench.domain.data_model import (
    ActionConsentRecord,
    CmOutboundDB,
    ConsentRecord,
    DomainModel,
)
from cm_outbound_bench.environment import (
    CmOutboundEnvironment,
    ToolkitFactory,
    ToolTraceEntry,
)
from cm_outbound_bench.simulator import MarketingUserSimulator
from cm_outbound_bench.tasks import AgentTaskView, MarketingTask

AgentFactory = Callable[[], Agent]

#: Default hard limit on the number of agent actions allowed after the simulated
#: customer closed the conversation. It exists so a chatty agent cannot loop
#: forever once the customer has stopped listening.
DEFAULT_MAX_POST_CLOSURE_ACTIONS = 6

#: The ``RunAction.outcome`` recorded for an agent ``say`` that the Runner
#: accepted after the simulated customer closed the conversation. The literal is
#: defined here, next to the record that writes it, so the evaluator can identify
#: those turns from structured action metadata instead of prose.
POST_CONVERSATION_MESSAGE_OUTCOME = "post_conversation_message"

#: Default wall-clock budget for one episode, checked between agent actions.
DEFAULT_DEADLINE_SECONDS = 120.0


class StopReason(str, Enum):
    """Why an episode stopped.

    ``AGENT_FINISHED`` is the only cooperative stop. Every other member is a
    structured, non-crashing termination.
    """

    AGENT_FINISHED = "agent_finished"
    STEP_LIMIT = "step_limit"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    TURN_BUDGET_EXCEEDED = "turn_budget_exceeded"
    TOOL_ERROR_BUDGET_EXCEEDED = "tool_error_budget_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    POST_CLOSURE_LIMIT = "post_closure_limit"
    INVALID_ACTION = "invalid_action"
    PROTOCOL_ERROR = "protocol_error"


class EpisodeError(DomainModel):
    """A safe, structured description of an abnormal episode stop."""

    code: str
    error_type: str = ""
    stage: str = ""
    message: str = ""


class ConsentEvent(DomainModel):
    step: int = Field(ge=1)
    consent_id: str


class ActionConsentEvent(DomainModel):
    step: int = Field(ge=1)
    consent_id: str
    action: str
    target_ref: str = ""


class RunAction(DomainModel):
    """One normalized agent action, in execution order.

    Recorded so that a finished episode can be replayed without asking a
    provider for a fresh response.
    """

    step: int = Field(ge=1)
    action_type: Literal["say", "call_tool", "finish"]
    message: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None
    finish_reason: str = ""
    outcome: str = ""


class SimulatorEvent(DomainModel):
    """One simulated customer turn, recorded in order."""

    step: int = Field(ge=1)
    step_id: str = ""
    message: str = ""
    intent: str = ""
    consent_granted: bool = False
    consent_action: str | None = None
    consent_target: str = ""
    ends_conversation: bool = False


class RunResult(DomainModel):
    task_id: str
    stop_reason: StopReason
    steps_executed: int = Field(ge=0)
    conversation: list[ConversationMessage]
    tool_trace: list[ToolTraceEntry]
    consent_events: list[ConsentEvent]
    final_db: CmOutboundDB
    action_consent_events: list[ActionConsentEvent] = Field(default_factory=list)
    initial_state_id: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    actions: list[RunAction] = Field(default_factory=list)
    simulator_events: list[SimulatorEvent] = Field(default_factory=list)
    customer_conversation_closed: bool = False
    post_closure_actions: int = Field(default=0, ge=0)
    error: EpisodeError | None = None
    agent_usage: dict[str, int] = Field(default_factory=dict)

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_trace)

    @property
    def conversation_turn_count(self) -> int:
        return len(self.conversation)

    @property
    def episode_completed(self) -> bool:
        """True only for a cooperative, completed episode."""
        return self.stop_reason is StopReason.AGENT_FINISHED

    def tool_names(self) -> list[str]:
        return [entry.tool_name for entry in self.tool_trace]

    def successful_tool_names(self) -> list[str]:
        return [entry.tool_name for entry in self.tool_trace if entry.result.ok]


@dataclass
class EpisodeSpec:
    """Everything one episode needs, assembled by a domain adapter."""

    task_id: str
    agent_view: AgentTaskView
    simulator: Any
    consent_phone_number_id: str
    consent_offer_id: str | None = None
    consent_allowed: bool = True
    allowed_tools: frozenset[str] | None = None
    initial_state_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    max_turns: int | None = None
    max_tool_calls: int | None = None
    max_tool_errors: int | None = None
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS
    max_post_closure_actions: int = DEFAULT_MAX_POST_CLOSURE_ACTIONS


class Runner:
    """Execute one episode with one fresh agent and one isolated DB copy."""

    def __init__(
        self,
        *,
        initial_db: CmOutboundDB,
        policy: str,
        agent_factory: AgentFactory,
        max_steps: int = 24,
        toolkit_factory: ToolkitFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self._initial_db = initial_db
        self._policy = policy
        self._agent_factory = agent_factory
        self._max_steps = max_steps
        self._toolkit_factory = toolkit_factory
        self._clock = clock
        self.agent: Agent | None = None

    def run(self, spec: EpisodeSpec) -> RunResult:
        env = CmOutboundEnvironment(
            self._initial_db,
            self._policy,
            toolkit_factory=self._toolkit_factory,
            allowed_tools=spec.allowed_tools,
        )
        agent = self._agent_factory()
        self.agent = agent
        visible_tools = self._visible_tools(env, spec.allowed_tools)

        conversation: list[ConversationMessage] = []
        consent_events: list[ConsentEvent] = []
        action_consent_events: list[ActionConsentEvent] = []
        actions: list[RunAction] = []
        simulator_events: list[SimulatorEvent] = []
        stop_reason = StopReason.STEP_LIMIT
        error: EpisodeError | None = None
        steps_executed = 0
        closed = False
        post_closure_actions = 0
        started = self._clock()

        for step in range(1, self._max_steps + 1):
            elapsed = self._clock() - started
            if elapsed > spec.deadline_seconds:
                stop_reason = StopReason.DEADLINE_EXCEEDED
                error = EpisodeError(
                    code="episode_deadline_exceeded",
                    error_type="TimeoutError",
                    stage="agent_step",
                    message=(
                        "The episode exceeded its wall-clock budget of "
                        f"{spec.deadline_seconds:.1f}s before the agent could act."
                    ),
                )
                break

            context = AgentContext(
                task=spec.agent_view,
                policy=env.policy,
                tools=list(visible_tools),
                messages=[message.model_copy(deep=True) for message in conversation],
                tool_results=[entry.model_copy(deep=True) for entry in env.tool_trace],
                step=step,
            )
            action = agent.act(context)
            steps_executed = step

            if isinstance(action, FinishAction):
                actions.append(
                    RunAction(
                        step=step,
                        action_type="finish",
                        finish_reason=action.reason,
                        outcome="episode_finished",
                    )
                )
                stop_reason = StopReason.AGENT_FINISHED
                break

            if isinstance(action, SayAction):
                conversation.append(
                    ConversationMessage(
                        step=step, role="agent", content=action.message
                    )
                )
                if closed:
                    post_closure_actions += 1
                    actions.append(
                        RunAction(
                            step=step,
                            action_type="say",
                            message=action.message,
                            outcome=POST_CONVERSATION_MESSAGE_OUTCOME,
                        )
                    )
                    if post_closure_actions > spec.max_post_closure_actions:
                        stop_reason = StopReason.POST_CLOSURE_LIMIT
                        error = EpisodeError(
                            code="post_closure_action_limit",
                            error_type="LoopGuard",
                            stage="agent_step",
                            message=(
                                "The agent kept acting after the customer closed "
                                "the conversation and exceeded the post-call "
                                f"action limit of {spec.max_post_closure_actions}."
                            ),
                        )
                        break
                    continue

                reply = spec.simulator.respond(action.message)
                actions.append(
                    RunAction(
                        step=step,
                        action_type="say",
                        message=action.message,
                        outcome="simulated_reply",
                    )
                )
                self._handle_consent(
                    reply,
                    spec,
                    env,
                    step=step,
                    consent_events=consent_events,
                    action_consent_events=action_consent_events,
                )
                reply_text = self._reply_text(reply)
                conversation.append(
                    ConversationMessage(
                        step=step, role="user", content=reply_text
                    )
                )
                simulator_events.append(
                    self._simulator_event(reply, step=step, message=reply_text)
                )
                if bool(getattr(reply, "ends_conversation", False)):
                    closed = True
                if spec.max_turns is not None and len(conversation) > spec.max_turns:
                    stop_reason = StopReason.TURN_BUDGET_EXCEEDED
                    error = EpisodeError(
                        code="max_turns_exceeded",
                        error_type="BudgetExceeded",
                        stage="simulator",
                        message=(
                            "The conversation exceeded the enforced turn budget of "
                            f"{spec.max_turns} messages."
                        ),
                    )
                    break
                continue

            if isinstance(action, CallToolAction):
                if spec.max_tool_calls is not None and (
                    len(env.tool_trace) >= spec.max_tool_calls
                ):
                    actions.append(
                        RunAction(
                            step=step,
                            action_type="call_tool",
                            tool_name=action.tool_name,
                            arguments=dict(action.arguments),
                            call_id=action.call_id,
                            outcome="tool_budget_exceeded",
                        )
                    )
                    stop_reason = StopReason.TOOL_BUDGET_EXCEEDED
                    error = EpisodeError(
                        code="max_tool_calls_exceeded",
                        error_type="BudgetExceeded",
                        stage="tool_dispatch",
                        message=(
                            "The episode reached the enforced tool-call budget of "
                            f"{spec.max_tool_calls} calls."
                        ),
                    )
                    break
                blocked = spec.allowed_tools is not None and (
                    action.tool_name not in spec.allowed_tools
                )
                if blocked:
                    env.record_blocked_tool(
                        action.tool_name,
                        action.arguments,
                        step,
                        call_id=action.call_id,
                    )
                else:
                    env.execute_tool(
                        action.tool_name,
                        action.arguments,
                        step,
                        call_id=action.call_id,
                    )
                if closed:
                    post_closure_actions += 1
                actions.append(
                    RunAction(
                        step=step,
                        action_type="call_tool",
                        tool_name=action.tool_name,
                        arguments=dict(action.arguments),
                        call_id=action.call_id,
                        outcome=(
                            "blocked_tool"
                            if blocked
                            else (
                                "executed_post_conversation"
                                if closed
                                else "executed"
                            )
                        ),
                    )
                )
                if spec.max_tool_errors is not None:
                    failures = sum(
                        1 for entry in env.tool_trace if not entry.result.ok
                    )
                    if failures > spec.max_tool_errors:
                        stop_reason = StopReason.TOOL_ERROR_BUDGET_EXCEEDED
                        error = EpisodeError(
                            code="max_tool_errors_exceeded",
                            error_type="BudgetExceeded",
                            stage="tool_dispatch",
                            message=(
                                "The episode exceeded the configured tool-error "
                                f"budget of {spec.max_tool_errors}."
                            ),
                        )
                        break
                continue

            # An unsupported action must never crash the harness or a batch.
            actions.append(
                RunAction(
                    step=step,
                    action_type="finish",
                    finish_reason="unsupported_action",
                    outcome="unsupported_action",
                )
            )
            stop_reason = StopReason.INVALID_ACTION
            error = EpisodeError(
                code="unsupported_agent_action",
                error_type=type(action).__name__,
                stage="agent_step",
                message=(
                    "The agent returned an action this runner does not support: "
                    f"{type(action).__name__}. Expected SayAction, CallToolAction "
                    "or FinishAction."
                ),
            )
            break

        usage: dict[str, int] = {}
        usage_snapshot = getattr(agent, "usage_snapshot", None)
        if callable(usage_snapshot):
            try:
                usage = {
                    str(key): int(value)
                    for key, value in dict(usage_snapshot()).items()
                }
            except (TypeError, ValueError):  # pragma: no cover - defensive
                usage = {}

        return RunResult(
            task_id=spec.task_id,
            stop_reason=stop_reason,
            steps_executed=steps_executed,
            conversation=conversation,
            tool_trace=list(env.tool_trace),
            consent_events=consent_events,
            final_db=env.db.deep_copy(),
            action_consent_events=action_consent_events,
            initial_state_id=spec.initial_state_id,
            allowed_tools=sorted(spec.allowed_tools or ()),
            actions=actions,
            simulator_events=simulator_events,
            customer_conversation_closed=closed,
            post_closure_actions=post_closure_actions,
            error=error,
            agent_usage=usage,
        )

    @staticmethod
    def _visible_tools(env: CmOutboundEnvironment, allowed: frozenset[str] | None):
        definitions = env.list_tools()
        if allowed is None:
            return definitions
        return [item for item in definitions if item.name in allowed]

    @staticmethod
    def _reply_text(reply: Any) -> str:
        content = getattr(reply, "message", None)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Simulator returned an empty customer message")
        return content

    @staticmethod
    def _simulator_event(reply: Any, *, step: int, message: str) -> SimulatorEvent:
        action_kind = getattr(reply, "consent_action", None)
        action_name = (
            action_kind.value if isinstance(action_kind, Enum) else action_kind
        )
        intent = getattr(reply, "intent", "")
        intent_value = intent.value if isinstance(intent, Enum) else str(intent)
        return SimulatorEvent(
            step=step,
            step_id=str(getattr(reply, "step_id", "") or ""),
            message=message,
            intent=intent_value,
            consent_granted=bool(getattr(reply, "consent_granted", False)),
            consent_action=action_name,
            consent_target=str(getattr(reply, "consent_target", "") or ""),
            ends_conversation=bool(getattr(reply, "ends_conversation", False)),
        )

    @staticmethod
    def _handle_consent(
        reply: Any,
        spec: EpisodeSpec,
        env: CmOutboundEnvironment,
        *,
        step: int,
        consent_events: list[ConsentEvent],
        action_consent_events: list[ActionConsentEvent],
    ) -> None:
        if not getattr(reply, "consent_granted", False):
            return
        if not spec.consent_allowed:
            raise RuntimeError(
                "Simulator emitted consent for a scenario that does not "
                "permit consent"
            )

        action_kind = getattr(reply, "consent_action", None)
        action_name = (
            action_kind.value if isinstance(action_kind, Enum) else action_kind
        )

        if action_name in (None, "OFFER_ENROLLMENT"):
            if spec.consent_offer_id is None:
                raise RuntimeError(
                    "Simulator granted offer consent for a task without an offer"
                )
            record: ConsentRecord | None = env.record_simulator_consent(
                spec.consent_phone_number_id, spec.consent_offer_id
            )
            if record is None:
                raise RuntimeError("Simulator consent could not be recorded")
            if all(event.consent_id != record.consent_id for event in consent_events):
                consent_events.append(
                    ConsentEvent(step=step, consent_id=record.consent_id)
                )
            return

        target = getattr(reply, "consent_target", "") or ""
        action_record: ActionConsentRecord | None = env.record_action_consent(
            spec.consent_phone_number_id, str(action_name), target
        )
        if action_record is None:
            raise RuntimeError("Simulator action consent could not be recorded")
        if all(
            event.consent_id != action_record.consent_id
            for event in action_consent_events
        ):
            action_consent_events.append(
                ActionConsentEvent(
                    step=step,
                    consent_id=action_record.consent_id,
                    action=action_record.action,
                    target_ref=action_record.target_ref,
                )
            )


class RecordedActionAgent:
    """Replays a recorded normalized action sequence without a provider.

    Used by ``benchmark replay``: the recorded actions replace fresh model
    responses so the tool-execution and simulator path can be reproduced
    deterministically.
    """

    def __init__(self, actions: list[RunAction]) -> None:
        self._actions = actions
        self._index = 0

    def act(self, context: AgentContext):
        if self._index >= len(self._actions):
            return FinishAction(reason="recorded actions exhausted")
        recorded = self._actions[self._index]
        self._index += 1
        if recorded.action_type == "say":
            return SayAction(message=recorded.message or "…")
        if recorded.action_type == "call_tool":
            return CallToolAction(
                tool_name=recorded.tool_name,
                arguments=dict(recorded.arguments),
                call_id=recorded.call_id,
            )
        return FinishAction(reason=recorded.finish_reason)


class MarketingRunner:
    """Legacy adapter: runs one manually authored marketing task.

    Kept as the public entry point for the original five tasks and their tests.
    It builds an :class:`EpisodeSpec` and delegates to the shared :class:`Runner`
    so there is exactly one conversation loop in the project.
    """

    def __init__(
        self,
        *,
        initial_db: CmOutboundDB,
        policy: str,
        agent_factory: AgentFactory,
        max_steps: int = 24,
    ) -> None:
        self._runner = Runner(
            initial_db=initial_db,
            policy=policy,
            agent_factory=agent_factory,
            max_steps=max_steps,
        )

    def run(self, task: MarketingTask) -> RunResult:
        spec = EpisodeSpec(
            task_id=task.task_id,
            agent_view=task.agent_view(),
            simulator=MarketingUserSimulator(task.simulator),
            consent_phone_number_id=task.phone_number_id,
            consent_offer_id=task.offer_id,
            consent_allowed=task.simulator.may_give_consent,
        )
        return self._runner.run(spec)


__all__ = [
    "ActionConsentEvent",
    "AgentFactory",
    "ConsentEvent",
    "DEFAULT_DEADLINE_SECONDS",
    "DEFAULT_MAX_POST_CLOSURE_ACTIONS",
    "EpisodeError",
    "EpisodeSpec",
    "MarketingRunner",
    "POST_CONVERSATION_MESSAGE_OUTCOME",
    "RecordedActionAgent",
    "RunAction",
    "RunResult",
    "Runner",
    "SimulatorEvent",
    "StopReason",
]
