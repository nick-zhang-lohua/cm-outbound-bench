"""OpenAI Chat Completions adapter with native tools and JSON fallback."""

from __future__ import annotations

import itertools
import json
import re
import time
from collections.abc import Callable
from typing import Any

from cm_outbound_bench.domain.tools import ToolDefinition
from cm_outbound_bench.providers.base import (
    FINISH_TOOL,
    FINISH_TOOL_NAME,
    ProviderClient,
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    ProviderToolCall,
    redact_secrets,
    validate_tool_call,
)
from cm_outbound_bench.providers.config import ResolvedProviderConfig
from cm_outbound_bench.providers.http import (
    HttpResponse,
    HttpTransport,
    UrllibHttpTransport,
)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_RETRYABLE_STATUS = {408, 429}


class OpenAICompatibleClient:
    """Works with arbitrary OpenAI-compatible Chat Completions endpoints."""

    def __init__(
        self,
        config: ResolvedProviderConfig,
        transport: HttpTransport,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._transport = transport
        self._sleep = sleep
        self._call_counter = itertools.count(1)
        key = config.api_key.get_secret_value()
        header_values = [
            header.get_secret_value() for header in config.headers.values()
        ]
        self._secrets = [key, *header_values]

    def complete(
        self,
        *,
        messages: list[ProviderMessage],
        tools: list[ToolDefinition],
    ) -> ProviderResponse:
        if not messages:
            raise ProviderError(
                "invalid_provider_request",
                "At least one provider message is required.",
            )
        payload = self._build_payload(messages, tools)
        data = self._post(payload)
        return self._normalize_response(data, tools)

    def _build_payload(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolDefinition],
    ) -> dict[str, Any]:
        if self._config.tool_mode == "function_calling":
            payload: dict[str, Any] = {
                "model": self._config.model,
                "messages": [self._native_message(item) for item in messages],
                "tools": [self._tool_payload(item) for item in [*tools, FINISH_TOOL]],
            }
        else:
            json_messages = self._with_json_instructions(messages, tools)
            payload = {
                "model": self._config.model,
                "messages": [self._json_message(item) for item in json_messages],
            }

        overlap = set(self._config.extra_params) & {
            "model",
            "messages",
            "tools",
            "stream",
        }
        if overlap:
            raise ProviderError(
                "invalid_provider_request",
                "Extra parameters cannot override reserved request fields.",
            )
        payload.update(self._config.extra_params)
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": (f"Bearer {self._config.api_key.get_secret_value()}"),
        }
        for name, value in self._config.headers.items():
            headers[name] = value.get_secret_value()
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        attempts = self._config.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self._transport.post(
                    self._config.chat_completions_url,
                    self._headers(),
                    payload,
                    self._config.timeout,
                )
            except ProviderError as error:
                if error.code == "network_error" and attempt + 1 < attempts:
                    self._sleep(0.5 * (2**attempt))
                    continue
                raise

            if 200 <= response.status_code < 300:
                return self._parse_json_body(response)
            if (
                response.status_code in _RETRYABLE_STATUS or response.status_code >= 500
            ) and attempt + 1 < attempts:
                self._sleep(0.5 * (2**attempt))
                continue
            raise self._http_error(response)

        raise ProviderError(
            "provider_http_error",
            "The provider request failed after all retries.",
        )

    @staticmethod
    def _parse_json_body(response: HttpResponse) -> dict[str, Any]:
        try:
            data = json.loads(response.body)
        except json.JSONDecodeError:
            raise ProviderError(
                "invalid_provider_response",
                "The provider returned a non-JSON response.",
            ) from None
        if not isinstance(data, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The provider response must be a JSON object.",
            )
        return data

    def _http_error(self, response: HttpResponse) -> ProviderError:
        if response.status_code in {401, 403}:
            code = "provider_authentication_error"
        elif response.status_code == 429:
            code = "provider_rate_limited"
        else:
            code = "provider_http_error"
        message = f"Provider request failed with HTTP {response.status_code}."
        detail = self._error_detail(response.body)
        if detail:
            message = f"{message} {detail}"
        return ProviderError(code, message)

    def _error_detail(self, body: str) -> str:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return ""
        if not isinstance(data, dict):
            return ""
        error = data.get("error")
        if isinstance(error, dict):
            detail = error.get("message")
        elif isinstance(error, str):
            detail = error
        else:
            detail = data.get("message")
        if not isinstance(detail, str) or not detail.strip():
            return ""
        safe = redact_secrets(detail.strip(), self._secrets)
        return safe[:240]

    def _normalize_response(
        self,
        data: dict[str, Any],
        tools: list[ToolDefinition],
    ) -> ProviderResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                "invalid_provider_response",
                "The provider response did not contain a completion choice.",
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The provider completion choice is malformed.",
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The provider completion message is malformed.",
            )
        usage = data.get("usage")
        provider_usage = usage if isinstance(usage, dict) else {}

        if self._config.tool_mode == "json":
            return self._normalize_json_response(
                message,
                tools,
                provider_usage,
            )
        return self._normalize_native_response(
            message,
            tools,
            provider_usage,
        )

    def _normalize_native_response(
        self,
        message: dict[str, Any],
        tools: list[ToolDefinition],
        provider_usage: dict[str, Any],
    ) -> ProviderResponse:
        content = self._content_text(message.get("content"))
        raw_calls = message.get("tool_calls") or []
        legacy_call = message.get("function_call")
        if legacy_call is not None:
            if raw_calls:
                raise ProviderError(
                    "multiple_tool_calls",
                    "The provider returned both tool_calls and function_call.",
                )
            raw_calls = [{"function": legacy_call}]
        if not isinstance(raw_calls, list):
            raise ProviderError(
                "invalid_provider_response",
                "The provider tool call payload is malformed.",
            )
        if len(raw_calls) > 1:
            raise ProviderError(
                "multiple_tool_calls",
                "The provider returned more than one tool call.",
            )
        if not raw_calls:
            if not content.strip():
                raise ProviderError(
                    "invalid_provider_response",
                    "The provider returned neither text nor a tool call.",
                )
            return ProviderResponse(
                assistant_message=content,
                provider_usage=provider_usage,
            )

        # Assistant commentary may accompany exactly one tool call. The
        # benchmark still treats the response as that single tool-call action;
        # the commentary is optional and is not emitted as a separate SayAction.
        call = self._native_tool_call(raw_calls[0], tools)
        if call.tool_name == FINISH_TOOL_NAME:
            if call.arguments:
                raise ProviderError(
                    "invalid_provider_response",
                    "The finish tool does not accept arguments.",
                )
            return ProviderResponse(
                finish_requested=True,
                provider_usage=provider_usage,
            )
        return ProviderResponse(
            tool_calls=[call],
            provider_usage=provider_usage,
        )

    def _native_tool_call(
        self,
        raw_call: Any,
        tools: list[ToolDefinition],
    ) -> ProviderToolCall:
        if not isinstance(raw_call, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The provider tool call is malformed.",
            )
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The provider tool call did not contain a function.",
            )
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ProviderError(
                "invalid_provider_response",
                "The provider tool call did not contain a tool name.",
            )
        arguments = self._parse_arguments(function.get("arguments", "{}"))
        call_id = raw_call.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = self._generated_call_id()
        if name != FINISH_TOOL_NAME:
            validate_tool_call(tools, name, arguments)
        return ProviderToolCall(
            call_id=call_id,
            tool_name=name,
            arguments=arguments,
        )

    @staticmethod
    def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if raw_arguments is None:
            return {}
        if not isinstance(raw_arguments, str):
            raise ProviderError(
                "invalid_tool_arguments",
                "Tool arguments must be a JSON object or string.",
            )
        if not raw_arguments.strip():
            return {}
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            raise ProviderError(
                "invalid_tool_arguments",
                "Tool arguments were not valid JSON.",
            ) from None
        if not isinstance(arguments, dict):
            raise ProviderError(
                "invalid_tool_arguments",
                "Tool arguments must be a JSON object.",
            )
        return arguments

    def _normalize_json_response(
        self,
        message: dict[str, Any],
        tools: list[ToolDefinition],
        provider_usage: dict[str, Any],
    ) -> ProviderResponse:
        if message.get("tool_calls") or message.get("function_call"):
            raise ProviderError(
                "invalid_provider_response",
                "Native tool calls are not allowed in JSON action mode.",
            )
        content = self._content_text(message.get("content"))
        if not content.strip():
            raise ProviderError(
                "invalid_provider_response",
                "The provider returned an empty JSON action response.",
            )
        action = self._parse_json_action(content)
        action_type = action.get("action")
        if action_type == "SAY":
            _require_keys(action, {"action", "message"})
            text = action.get("message")
            if not isinstance(text, str) or not text.strip():
                raise ProviderError(
                    "invalid_provider_response",
                    "JSON SAY actions require a non-empty message.",
                )
            return ProviderResponse(
                assistant_message=text,
                provider_usage=provider_usage,
            )
        if action_type == "CALL_TOOL":
            _require_keys(action, {"action", "tool_name", "arguments"})
            tool_name = action.get("tool_name")
            arguments = action.get("arguments")
            if not isinstance(tool_name, str) or not tool_name:
                raise ProviderError(
                    "invalid_provider_response",
                    "JSON CALL_TOOL actions require a tool_name.",
                )
            if not isinstance(arguments, dict):
                raise ProviderError(
                    "invalid_tool_arguments",
                    "JSON CALL_TOOL arguments must be an object.",
                )
            validate_tool_call(tools, tool_name, arguments)
            return ProviderResponse(
                tool_calls=[
                    ProviderToolCall(
                        call_id=self._generated_call_id(),
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                ],
                provider_usage=provider_usage,
            )
        if action_type == "FINISH":
            _require_keys(action, {"action", "reason"}, {"reason"})
            return ProviderResponse(
                finish_requested=True,
                provider_usage=provider_usage,
            )
        raise ProviderError(
            "invalid_provider_response",
            "JSON action must be one of SAY, CALL_TOOL or FINISH.",
        )

    @staticmethod
    def _parse_json_action(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        fence = _JSON_FENCE.fullmatch(cleaned)
        if fence:
            cleaned = fence.group(1)
        try:
            action = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ProviderError(
                "invalid_provider_response",
                "The provider JSON action was not valid JSON.",
            ) from None
        if not isinstance(action, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The provider JSON action must be an object.",
            )
        return action

    def _with_json_instructions(
        self,
        messages: list[ProviderMessage],
        tools: list[ToolDefinition],
    ) -> list[ProviderMessage]:
        instructions = self._json_instructions(tools)
        copied = [item.model_copy(deep=True) for item in messages]
        if copied and copied[0].role == "system":
            copied[0] = ProviderMessage(
                role="system",
                content=f"{copied[0].content}\n\n{instructions}",
            )
        else:
            copied.insert(0, ProviderMessage(role="system", content=instructions))
        return copied

    @staticmethod
    def _json_instructions(tools: list[ToolDefinition]) -> str:
        tool_specs = [
            {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters,
            }
            for item in tools
        ]
        return (
            "JSON ACTION MODE\n"
            "Return exactly one JSON object and no markdown or prose. "
            "The object must use one of these shapes:\n"
            '{"action":"SAY","message":"..."}\n'
            '{"action":"CALL_TOOL","tool_name":"...","arguments":{}}\n'
            '{"action":"FINISH"}\n'
            "Call at most one business tool. Never invent tool results. "
            "Tool-result turns are user messages containing an object with "
            "type='tool_result'; they are not customer speech.\n"
            "Available business tools:\n"
            f"{json.dumps(tool_specs, ensure_ascii=False)}"
        )

    @staticmethod
    def _native_message(message: ProviderMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        if message.role == "assistant" and message.tool_calls:
            payload: dict[str, Any] = {"role": "assistant"}
            if message.content:
                payload["content"] = message.content
            payload["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.tool_name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
            return payload
        return {"role": message.role, "content": message.content}

    def _json_message(self, message: ProviderMessage) -> dict[str, Any]:
        if message.role == "tool":
            try:
                result = json.loads(message.content)
            except json.JSONDecodeError:
                result = message.content
            envelope = {
                "type": "tool_result",
                "call_id": message.tool_call_id,
                "tool_name": message.name,
                "result": result,
            }
            return {
                "role": "user",
                "content": json.dumps(envelope, ensure_ascii=False),
            }
        if message.role == "assistant" and message.tool_calls:
            call = message.tool_calls[0]
            return {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "action": "CALL_TOOL",
                        "tool_name": call.tool_name,
                        "arguments": call.arguments,
                    },
                    ensure_ascii=False,
                ),
            }
        return {"role": message.role, "content": message.content}

    @staticmethod
    def _tool_payload(definition: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.parameters,
            },
        }

    @staticmethod
    def _content_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    def _generated_call_id(self) -> str:
        return f"call-{next(self._call_counter)}"


def build_openai_compatible_client(
    config: ResolvedProviderConfig,
) -> ProviderClient:
    """Factory registered for the built-in generic Chat Completions adapter."""
    return OpenAICompatibleClient(config, UrllibHttpTransport())


def _require_keys(
    action: dict[str, Any],
    allowed: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    unexpected = sorted(set(action) - allowed)
    if unexpected:
        raise ProviderError(
            "invalid_provider_response",
            f"The JSON action contains unsupported fields: {', '.join(unexpected)}.",
        )
    required = allowed - optional
    missing = sorted(name for name in required if name not in action)
    if missing:
        raise ProviderError(
            "invalid_provider_response",
            f"The JSON action is missing required fields: {', '.join(missing)}.",
        )
