"""Task-scoped environment shared by every benchmark domain.

The environment owns one isolated database copy, the policy text shown to the
agent, the composed tool surface and the execution trace. It never exposes
simulator-private or evaluator-private state.

The environment also knows the episode's permitted tool set. The runner is
responsible for agent action orchestration and applies its own allow-list check
first; the environment check is a second, independent layer so that a direct
``execute_tool`` call cannot bypass the task's permissions.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from pydantic import Field

from cm_outbound_bench.domain.data_model import (
    ActionConsentRecord,
    CmOutboundDB,
    ConsentRecord,
    DomainModel,
)
from cm_outbound_bench.domain.tools import (
    CmOutboundTools,
    ToolDefinition,
    ToolResult,
)


class ToolTraceEntry(DomainModel):
    step: int = Field(ge=1, description="Agent step that requested the tool.")
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    call_id: str | None = Field(
        default=None,
        description="Provider tool-call ID when the action came from an LLM.",
    )


class Toolkit(Protocol):
    """The tool surface the environment requires."""

    def list_tools(self) -> list[ToolDefinition]:
        """Return every tool definition available to the agent."""

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute one tool call."""

    def record_simulator_consent(
        self, phone_number_id: str, offer_id: str
    ) -> ConsentRecord | None:
        """Record runner-verified offer consent."""

    def record_action_consent(
        self, phone_number_id: str, action: str, target_ref: str
    ) -> ActionConsentRecord | None:
        """Record runner-verified consent for a non-offer action."""


ToolkitFactory = Callable[[CmOutboundDB], Toolkit]


def default_toolkit_factory(db: CmOutboundDB) -> Toolkit:
    """Return the original marketing toolkit (legacy behaviour preserved)."""
    return CmOutboundTools(db)


class CmOutboundEnvironment:
    """Holds one task's DB copy, policy, tools and execution trace."""

    def __init__(
        self,
        initial_db: CmOutboundDB,
        policy: str,
        toolkit_factory: ToolkitFactory | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        self.policy = policy
        self.allowed_tools: frozenset[str] | None = (
            frozenset(allowed_tools) if allowed_tools is not None else None
        )
        self._toolkit_factory = toolkit_factory or default_toolkit_factory
        self._initial_db = initial_db.deep_copy()
        self.db = initial_db.deep_copy()
        self.tools = self._toolkit_factory(self.db)
        self.tool_trace: list[ToolTraceEntry] = []

    def list_tools(self) -> list[ToolDefinition]:
        return self.tools.list_tools()

    def is_tool_allowed(self, name: str) -> bool:
        """Return whether the episode permits *name*."""
        return self.allowed_tools is None or name in self.allowed_tools

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        step: int,
        call_id: str | None = None,
    ) -> ToolResult:
        """Execute one tool call and record it in the task trace.

        A call outside the episode's allow-list is rejected, recorded and has no
        state effect, exactly like the runner's own check.
        """
        if not self.is_tool_allowed(name):
            return self.record_blocked_tool(
                name, arguments, step, call_id=call_id
            )
        result = self.tools.execute(name, arguments)
        # Store and return independent copies: an agent mutating the result it
        # received must not be able to rewrite the recorded trace.
        recorded = result.model_copy(deep=True)
        self.tool_trace.append(
            ToolTraceEntry(
                step=step,
                tool_name=name,
                arguments=dict(arguments),
                result=recorded,
                call_id=call_id,
            )
        )
        return recorded.model_copy(deep=True)

    def record_blocked_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        step: int,
        call_id: str | None = None,
    ) -> ToolResult:
        """Record an attempt to call a tool outside the task allow-list.

        The call is never executed, but the attempt stays in the trace so the
        evaluator can see and penalise it.
        """
        result = ToolResult.failure(
            "tool_not_allowed",
            f"Tool {name!r} is not available for this task.",
        )
        self.tool_trace.append(
            ToolTraceEntry(
                step=step,
                tool_name=name,
                arguments=dict(arguments),
                result=result,
                call_id=call_id,
            )
        )
        return result.model_copy(deep=True)

    def record_simulator_consent(
        self,
        phone_number_id: str,
        offer_id: str,
    ) -> ConsentRecord | None:
        """Record explicit simulator consent; never exposed as an agent tool."""
        recorder = getattr(self.tools, "record_simulator_consent", None)
        if recorder is None:
            return None
        return recorder(phone_number_id, offer_id)

    def record_action_consent(
        self,
        phone_number_id: str,
        action: str,
        target_ref: str,
    ) -> ActionConsentRecord | None:
        """Record explicit simulator consent for a non-offer action."""
        recorder = getattr(self.tools, "record_action_consent", None)
        if recorder is None:
            return None
        return recorder(phone_number_id, action, target_ref)

    def reset(self) -> None:
        """Restore the task's initial DB and clear verification and trace state."""
        self.db = self._initial_db.deep_copy()
        self.tools = self._toolkit_factory(self.db)
        self.tool_trace = []


__all__ = [
    "CmOutboundEnvironment",
    "ToolTraceEntry",
    "Toolkit",
    "ToolkitFactory",
    "default_toolkit_factory",
]
