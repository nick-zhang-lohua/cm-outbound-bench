"""Provider-independent request/response models and extension contract."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.domain.tools import ToolDefinition

FINISH_TOOL_NAME = "finish_conversation"
FINISH_TOOL = ToolDefinition(
    name=FINISH_TOOL_NAME,
    description=(
        "End the outbound call. Use this only after the conversation is "
        "complete, the customer has been told the actual outcome of any "
        "completed business action, and no further user message is needed."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)


class ProviderError(Exception):
    """A safe, provider-agnostic error that never contains credentials."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ProviderToolCall(DomainModel):
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProviderMessage(DomainModel):
    """A normalized message sent to a provider adapter."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ProviderToolCall] = Field(default_factory=list, max_length=1)
    tool_call_id: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def validate_message_shape(self) -> "ProviderMessage":
        if self.role == "tool":
            if not self.tool_call_id:
                raise ValueError("Tool messages require tool_call_id")
            if self.tool_calls:
                raise ValueError("Tool messages cannot contain tool calls")
            if not self.content:
                raise ValueError("Tool messages require content")
            return self

        if self.tool_call_id is not None:
            raise ValueError("Only tool messages may set tool_call_id")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("Only assistant messages may contain tool calls")
        if self.role == "assistant":
            if not self.content and not self.tool_calls:
                raise ValueError("Assistant messages require content or tool calls")
        elif not self.content:
            raise ValueError(f"{self.role.capitalize()} messages require content")
        return self


class ProviderResponse(DomainModel):
    """One normalized provider decision: text, one tool call, or finish."""

    assistant_message: str | None = None
    tool_calls: list[ProviderToolCall] = Field(default_factory=list, max_length=1)
    finish_requested: bool = False
    provider_usage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_single_action(self) -> "ProviderResponse":
        action_count = (
            int(self.assistant_message is not None)
            + len(self.tool_calls)
            + int(self.finish_requested)
        )
        if action_count != 1:
            raise ValueError("A provider response must contain exactly one action")
        if self.assistant_message is not None and not self.assistant_message.strip():
            raise ValueError("assistant_message cannot be empty")
        if self.finish_requested and self.tool_calls:
            raise ValueError("Finish responses cannot contain tool calls")
        return self


class ProviderClient(Protocol):
    """Minimal contract every native or generic provider adapter implements.

    Implementations must raise ``ProviderError`` with a safe message and must
    never include credentials in errors or normalized responses.
    """

    def complete(
        self,
        *,
        messages: list[ProviderMessage],
        tools: list[ToolDefinition],
    ) -> ProviderResponse:
        """Send a normalized request and return one normalized decision."""


def redact_secrets(text: str, secrets: list[str]) -> str:
    """Remove known secrets from text before it reaches logs or exceptions."""
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def validate_tool_call(
    definitions: list[ToolDefinition],
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolDefinition:
    """Validate one model-selected business tool call before it reaches Runner."""
    definition = next(
        (item for item in definitions if item.name == tool_name),
        None,
    )
    if definition is None:
        raise ProviderError(
            "unknown_tool",
            f"Tool {tool_name!r} is not available to the agent.",
        )
    if not isinstance(arguments, dict):
        raise ProviderError(
            "invalid_tool_arguments",
            f"Arguments for tool {tool_name!r} must be a JSON object.",
        )

    schema = definition.parameters
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    missing = [name for name in required if name not in arguments]
    if missing:
        raise ProviderError(
            "invalid_tool_arguments",
            f"Tool {tool_name!r} is missing required arguments: {', '.join(missing)}.",
        )

    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            raise ProviderError(
                "invalid_tool_arguments",
                f"Tool {tool_name!r} received unexpected arguments: "
                f"{', '.join(unexpected)}.",
            )

    for name, value in arguments.items():
        property_schema = properties.get(name)
        if property_schema is not None and not _matches_schema_type(
            value, property_schema.get("type")
        ):
            raise ProviderError(
                "invalid_tool_arguments",
                f"Argument {name!r} for tool {tool_name!r} has an invalid type.",
            )
    return definition


def _matches_schema_type(value: Any, expected: Any) -> bool:
    if expected is None:
        return True
    expected_types = expected if isinstance(expected, list) else [expected]
    return any(_matches_one_type(value, item) for item in expected_types)


def _matches_one_type(value: Any, expected: str) -> bool:
    match expected:
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "object":
            return isinstance(value, dict)
        case "array":
            return isinstance(value, list)
        case "null":
            return value is None
        case _:
            return True
