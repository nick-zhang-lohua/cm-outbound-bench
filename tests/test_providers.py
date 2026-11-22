import json
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from cm_outbound_bench.domain.tools import CmOutboundTools
from cm_outbound_bench.providers import (
    ProviderError,
    ProviderMessage,
    ProviderProfile,
    ProviderResponse,
    ProviderToolCall,
    ResolvedProviderConfig,
    create_provider_client,
    default_provider_config_path,
    load_provider_profile,
    register_provider_factory,
    resolve_provider_profile,
)
from cm_outbound_bench.providers.http import HttpResponse
from cm_outbound_bench.providers.openai_compatible import (
    OpenAICompatibleClient,
)

API_KEY = "sk-test-secret-value"
HEADER_VALUE = "relay-tenant-secret"


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> HttpResponse:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": payload,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("Unexpected provider request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _settings(**overrides: Any) -> ResolvedProviderConfig:
    values: dict[str, Any] = {
        "api_protocol": "openai_compatible",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "timeout": 30,
        "max_retries": 0,
        "tool_mode": "function_calling",
        "api_key": SecretStr(API_KEY),
        "headers": {},
        "extra_params": {},
    }
    values.update(overrides)
    return ResolvedProviderConfig(**values)


def _client(
    transport: FakeTransport,
    **overrides: Any,
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        _settings(**overrides),
        transport,
        sleep=lambda _: None,
    )


def _completion(
    message: dict[str, Any],
    usage: dict[str, Any] | None = None,
) -> HttpResponse:
    body: dict[str, Any] = {"choices": [{"message": message, "finish_reason": "stop"}]}
    if usage is not None:
        body["usage"] = usage
    return HttpResponse(200, json.dumps(body))


def _tool_call(
    name: str,
    arguments: dict[str, Any],
    call_id: str = "call-1",
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


def _tools(database) -> list:
    return CmOutboundTools(database).list_tools()


def _profile_toml(path: Path) -> Path:
    path.write_text(
        """
[providers.deepseek]
api_protocol = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
model = "deepseek-chat"
timeout = 45
max_retries = 2
tool_mode = "function_calling"

[providers.deepseek.extra_params]
temperature = 0.2

[providers.relay]
api_protocol = "openai_compatible"
base_url = "https://relay.example.test/openai/v1/"
api_key_env = "RELAY_API_KEY"
model = "relay-model"
timeout = 90
max_retries = 1

[providers.relay.headers_env]
"X-Organization" = "RELAY_ORG"

[providers.relay.extra_params]
temperature = 0.1
""",
        encoding="utf-8",
    )
    return path


def test_deepseek_style_profile_and_custom_relay_configuration(
    tmp_path: Path,
):
    config_path = _profile_toml(tmp_path / "providers.toml")
    profile = load_provider_profile(config_path, "deepseek")
    resolved = resolve_provider_profile(
        profile,
        environ={"DEEPSEEK_API_KEY": API_KEY},
    )

    assert resolved.api_protocol == "openai_compatible"
    assert resolved.chat_completions_url == (
        "https://api.deepseek.com/v1/chat/completions"
    )
    assert resolved.model == "deepseek-chat"
    assert resolved.api_key.get_secret_value() == API_KEY
    assert resolved.timeout == 45
    assert resolved.max_retries == 2

    relay = load_provider_profile(config_path, "relay")
    relay_runtime = resolve_provider_profile(
        relay,
        environ={"RELAY_API_KEY": "relay-key", "RELAY_ORG": HEADER_VALUE},
    )
    assert relay_runtime.chat_completions_url == (
        "https://relay.example.test/openai/v1/chat/completions"
    )
    assert relay_runtime.headers["X-Organization"].get_secret_value() == HEADER_VALUE
    assert relay_runtime.extra_params == {"temperature": 0.1}


def test_full_chat_completions_endpoint_is_preserved():
    resolved = _settings(
        base_url="https://relay.example.test/v1/chat/completions",
    )
    assert resolved.chat_completions_url == (
        "https://relay.example.test/v1/chat/completions"
    )


def test_default_provider_config_path_uses_environment(tmp_path: Path):
    configured = tmp_path / "team-providers.toml"
    assert (
        default_provider_config_path({"CM_OUTBOUND_PROVIDER_CONFIG": str(configured)})
        == configured
    )
    assert default_provider_config_path({}) == Path("providers.toml")


def test_provider_profile_validation_and_unknown_profile(tmp_path: Path):
    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        """
[providers.bad]
base_url = "https://user:pass@example.test/v1?token=x"
api_key_env = "BAD_KEY"
model = "m"
""",
        encoding="utf-8",
    )
    with pytest.raises(ProviderError) as invalid:
        load_provider_profile(config_path, "bad")
    assert invalid.value.code == "invalid_provider_config"

    good_path = _profile_toml(tmp_path / "good.toml")
    with pytest.raises(ProviderError) as missing:
        load_provider_profile(good_path, "missing")
    assert missing.value.code == "unknown_provider"


def test_missing_api_key_and_header_environment_variables(tmp_path: Path):
    profile = ProviderProfile(
        base_url="https://relay.example.test/v1",
        api_key_env="RELAY_KEY",
        model="m",
        headers_env={"X-Org": "RELAY_ORG"},
    )
    with pytest.raises(ProviderError) as missing_key:
        resolve_provider_profile(profile, environ={})
    assert missing_key.value.code == "missing_api_key"
    assert API_KEY not in str(missing_key.value)

    with pytest.raises(ProviderError) as missing_header:
        resolve_provider_profile(
            profile,
            environ={"RELAY_KEY": API_KEY},
        )
    assert missing_header.value.code == "missing_header_value"

    with pytest.raises(ProviderError) as bad_header:
        resolve_provider_profile(
            profile,
            environ={"RELAY_KEY": API_KEY, "RELAY_ORG": "bad\nvalue"},
        )
    assert bad_header.value.code == "invalid_header_value"


def test_resolved_configuration_repr_redacts_secrets():
    resolved = _settings(
        headers={"X-Org": SecretStr(HEADER_VALUE)},
    )
    rendered = repr(resolved)
    assert API_KEY not in rendered
    assert HEADER_VALUE not in rendered


def test_custom_base_url_headers_and_extra_params(database):
    transport = FakeTransport([_completion({"role": "assistant", "content": "您好。"})])
    client = _client(
        transport,
        base_url="https://gateway.example.test/custom/v1",
        headers={"X-Org": SecretStr(HEADER_VALUE)},
        extra_params={"temperature": 0.15},
    )
    client.complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )

    request = transport.requests[0]
    assert request["url"] == ("https://gateway.example.test/custom/v1/chat/completions")
    assert request["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert request["headers"]["X-Org"] == HEADER_VALUE
    assert request["payload"]["temperature"] == 0.15
    assert request["payload"]["model"] == "deepseek-chat"


def test_ordinary_text_response_and_usage(database):
    usage = {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    transport = FakeTransport(
        [
            _completion(
                {"role": "assistant", "content": "您好，我是客服。"},
                usage=usage,
            )
        ]
    )
    result = _client(transport).complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )

    assert result.assistant_message == "您好，我是客服。"
    assert result.tool_calls == []
    assert result.finish_requested is False
    assert result.provider_usage == usage
    tool_names = {
        item["function"]["name"] for item in transport.requests[0]["payload"]["tools"]
    }
    assert "get_offer_details" in tool_names
    assert "finish_conversation" in tool_names


def test_native_tool_call_normalization(database):
    transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "tool_calls": [
                        _tool_call(
                            "get_offer_details",
                            {"offer_id": "OFFER_DATA_001"},
                            "provider-call-7",
                        )
                    ],
                }
            )
        ]
    )
    result = _client(transport).complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )

    assert result.assistant_message is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.call_id == "provider-call-7"
    assert call.tool_name == "get_offer_details"
    assert call.arguments == {"offer_id": "OFFER_DATA_001"}


def test_finish_tool_request_normalization(database):
    transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "tool_calls": [_tool_call("finish_conversation", {}, "finish-1")],
                }
            )
        ]
    )
    result = _client(transport).complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )
    assert result.finish_requested is True
    assert result.tool_calls == []


def test_text_with_single_business_tool_call_normalization(database):
    usage = {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}
    transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": "Let me check your offer details.",
                    "tool_calls": [
                        _tool_call(
                            "get_offer_details",
                            {"offer_id": "OFFER_DATA_001"},
                            "provider-mixed-7",
                        )
                    ],
                },
                usage=usage,
            )
        ]
    )
    result = _client(transport).complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )

    assert result.assistant_message is None
    assert result.finish_requested is False
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.call_id == "provider-mixed-7"
    assert call.tool_name == "get_offer_details"
    assert call.arguments == {"offer_id": "OFFER_DATA_001"}
    assert result.provider_usage == usage


def test_text_with_finish_tool_call_normalization(database):
    transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": "All done, ending the call.",
                    "tool_calls": [
                        _tool_call("finish_conversation", {}, "finish-mixed-1")
                    ],
                }
            )
        ]
    )
    result = _client(transport).complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )

    assert result.finish_requested is True
    assert result.assistant_message is None
    assert result.tool_calls == []


def test_text_does_not_mask_unknown_tool_or_invalid_arguments(database):
    tools = _tools(database)
    messages = [ProviderMessage(role="system", content="system")]

    unknown = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": "Let me run that.",
                    "tool_calls": [_tool_call("run_shell", {})],
                }
            )
        ]
    )
    with pytest.raises(ProviderError) as unknown_error:
        _client(unknown).complete(messages=messages, tools=tools)
    assert unknown_error.value.code == "unknown_tool"

    invalid = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": "Let me check.",
                    "tool_calls": [_tool_call("get_offer_details", {})],
                }
            )
        ]
    )
    with pytest.raises(ProviderError) as invalid_error:
        _client(invalid).complete(messages=messages, tools=tools)
    assert invalid_error.value.code == "invalid_tool_arguments"


def test_rejects_multiple_unknown_and_malformed_tool_calls(database):
    tools = _tools(database)
    multiple = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": "Let me check both.",
                    "tool_calls": [
                        _tool_call(
                            "get_offer_details",
                            {"offer_id": "OFFER_DATA_001"},
                            "one",
                        ),
                        _tool_call(
                            "get_offer_details",
                            {"offer_id": "OFFER_DATA_001"},
                            "two",
                        ),
                    ],
                }
            )
        ]
    )
    with pytest.raises(ProviderError) as multiple_error:
        _client(multiple).complete(
            messages=[ProviderMessage(role="system", content="system")],
            tools=tools,
        )
    assert multiple_error.value.code == "multiple_tool_calls"

    unknown = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "tool_calls": [_tool_call("run_shell", {})],
                }
            )
        ]
    )
    with pytest.raises(ProviderError) as unknown_error:
        _client(unknown).complete(
            messages=[ProviderMessage(role="system", content="system")],
            tools=tools,
        )
    assert unknown_error.value.code == "unknown_tool"

    malformed = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "bad",
                            "type": "function",
                            "function": {
                                "name": "get_offer_details",
                                "arguments": "{not json",
                            },
                        }
                    ],
                }
            )
        ]
    )
    with pytest.raises(ProviderError) as malformed_error:
        _client(malformed).complete(
            messages=[ProviderMessage(role="system", content="system")],
            tools=tools,
        )
    assert malformed_error.value.code == "invalid_tool_arguments"


def test_invalid_provider_response_shapes(database):
    tools = _tools(database)
    messages = [ProviderMessage(role="system", content="system")]
    for body in (
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"role": "assistant", "content": ""}}]},
    ):
        transport = FakeTransport([HttpResponse(200, json.dumps(body))])
        with pytest.raises(ProviderError) as error:
            _client(transport).complete(messages=messages, tools=tools)
        assert error.value.code == "invalid_provider_response"

    transport = FakeTransport([HttpResponse(200, "not-json")])
    with pytest.raises(ProviderError) as error:
        _client(transport).complete(messages=messages, tools=tools)
    assert error.value.code == "invalid_provider_response"


def test_json_structured_action_fallback(database):
    tools = _tools(database)
    transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": (
                        '```json\n{"action":"CALL_TOOL",'
                        '"tool_name":"get_offer_details",'
                        '"arguments":{"offer_id":"OFFER_DATA_001"}}\n```'
                    ),
                }
            )
        ]
    )
    result = _client(transport, tool_mode="json").complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=tools,
    )

    assert result.tool_calls[0].tool_name == "get_offer_details"
    assert result.tool_calls[0].arguments == {"offer_id": "OFFER_DATA_001"}
    request = transport.requests[0]
    assert "tools" not in request["payload"]
    assert "JSON ACTION MODE" in request["payload"]["messages"][0]["content"]


def test_json_say_and_finish_actions(database):
    tools = _tools(database)
    messages = [ProviderMessage(role="system", content="system")]

    say_transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": '{"action":"SAY","message":"您好。"}',
                }
            )
        ]
    )
    say = _client(say_transport, tool_mode="json").complete(
        messages=messages,
        tools=tools,
    )
    assert say.assistant_message == "您好。"
    assert say.tool_calls == []

    finish_transport = FakeTransport(
        [_completion({"role": "assistant", "content": '{"action":"FINISH"}'})]
    )
    finished = _client(finish_transport, tool_mode="json").complete(
        messages=messages,
        tools=tools,
    )
    assert finished.finish_requested is True
    assert finished.assistant_message is None


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (
            '{"action":"SAY","message":"您好"}',
            None,
        ),
        (
            '{"action":"FINISH"}',
            None,
        ),
        (
            '{"action":"CALL_TOOL","tool_name":"get_offer_details",'
            '"arguments":{"offer_id":"OFFER_DATA_001"}}',
            None,
        ),
        (
            '{"action":"DELETE_EVERYTHING"}',
            "invalid_provider_response",
        ),
        (
            '{"action":"SAY","message":"hi","extra":true}',
            "invalid_provider_response",
        ),
        (
            '{"action":"CALL_TOOL","tool_name":"get_offer_details","arguments":{}}',
            "invalid_tool_arguments",
        ),
        (
            "not a JSON action",
            "invalid_provider_response",
        ),
    ],
)
def test_json_action_validation(database, content, expected_code):
    transport = FakeTransport([_completion({"role": "assistant", "content": content})])
    client = _client(transport, tool_mode="json")
    try:
        result = client.complete(
            messages=[ProviderMessage(role="system", content="system")],
            tools=_tools(database),
        )
    except ProviderError as error:
        assert expected_code == error.code
    else:
        assert expected_code is None
        assert (
            result.assistant_message is not None
            or result.tool_calls
            or result.finish_requested
        )


def test_json_mode_rejects_native_tool_calls(database):
    transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "tool_calls": [
                        _tool_call(
                            "get_offer_details",
                            {"offer_id": "OFFER_DATA_001"},
                        )
                    ],
                }
            )
        ]
    )
    with pytest.raises(ProviderError) as error:
        _client(transport, tool_mode="json").complete(
            messages=[ProviderMessage(role="system", content="system")],
            tools=_tools(database),
        )
    assert error.value.code == "invalid_provider_response"


def test_tool_call_history_reconstruction_native(database):
    transport = FakeTransport([_completion({"role": "assistant", "content": "Done."})])
    messages = [
        ProviderMessage(role="system", content="system"),
        ProviderMessage(
            role="assistant",
            tool_calls=[
                ProviderToolCall(
                    call_id="call-history-1",
                    tool_name="get_offer_details",
                    arguments={"offer_id": "OFFER_DATA_001"},
                )
            ],
        ),
        ProviderMessage(
            role="tool",
            content='{"ok": true, "data": {}}',
            tool_call_id="call-history-1",
            name="get_offer_details",
        ),
    ]
    _client(transport).complete(messages=messages, tools=_tools(database))

    payload_messages = transport.requests[0]["payload"]["messages"]
    assistant = payload_messages[1]
    tool_result = payload_messages[2]
    assert assistant["tool_calls"][0]["id"] == "call-history-1"
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call-history-1"
    assert json.loads(tool_result["content"])["ok"] is True


def test_tool_call_history_reconstruction_json(database):
    transport = FakeTransport(
        [
            _completion(
                {
                    "role": "assistant",
                    "content": '{"action":"SAY","message":"好的"}',
                }
            )
        ]
    )
    messages = [
        ProviderMessage(role="system", content="system"),
        ProviderMessage(
            role="assistant",
            tool_calls=[
                ProviderToolCall(
                    call_id="json-history-1",
                    tool_name="get_offer_details",
                    arguments={"offer_id": "OFFER_DATA_001"},
                )
            ],
        ),
        ProviderMessage(
            role="tool",
            content='{"ok": true, "data": {}}',
            tool_call_id="json-history-1",
            name="get_offer_details",
        ),
    ]
    _client(transport, tool_mode="json").complete(
        messages=messages,
        tools=_tools(database),
    )

    payload_messages = transport.requests[0]["payload"]["messages"]
    assert json.loads(payload_messages[1]["content"])["action"] == "CALL_TOOL"
    result_envelope = json.loads(payload_messages[2]["content"])
    assert result_envelope["type"] == "tool_result"
    assert result_envelope["call_id"] == "json-history-1"
    assert result_envelope["tool_name"] == "get_offer_details"


def test_retries_then_succeeds(database):
    sleeps: list[float] = []
    transport = FakeTransport(
        [
            HttpResponse(500, '{"error":{"message":"temporary"}}'),
            _completion({"role": "assistant", "content": "ok"}),
        ]
    )
    client = OpenAICompatibleClient(
        _settings(max_retries=1),
        transport,
        sleep=sleeps.append,
    )
    result = client.complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )

    assert result.assistant_message == "ok"
    assert len(transport.requests) == 2
    assert sleeps == [0.5]


def test_provider_failures_are_safe_and_redacted(database, caplog):
    transport = FakeTransport(
        [
            HttpResponse(
                401,
                json.dumps(
                    {
                        "error": {
                            "message": (
                                f"invalid key {API_KEY} and header {HEADER_VALUE}"
                            )
                        }
                    }
                ),
            )
        ]
    )
    client = _client(
        transport,
        headers={"X-Org": SecretStr(HEADER_VALUE)},
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ProviderError) as error:
            client.complete(
                messages=[ProviderMessage(role="system", content="system")],
                tools=_tools(database),
            )

    assert error.value.code == "provider_authentication_error"
    rendered = str(error.value)
    assert API_KEY not in rendered
    assert HEADER_VALUE not in rendered
    assert "[REDACTED]" in rendered
    assert API_KEY not in caplog.text
    assert HEADER_VALUE not in caplog.text


def test_max_retries_exhausted(database):
    transport = FakeTransport(
        [
            HttpResponse(503, '{"error":{"message":"down"}}'),
            HttpResponse(503, '{"error":{"message":"down"}}'),
        ]
    )
    client = OpenAICompatibleClient(
        _settings(max_retries=1),
        transport,
        sleep=lambda _: None,
    )
    with pytest.raises(ProviderError) as error:
        client.complete(
            messages=[ProviderMessage(role="system", content="system")],
            tools=_tools(database),
        )
    assert error.value.code == "provider_http_error"
    assert len(transport.requests) == 2


def test_rate_limit_429_is_retried_then_succeeds(database):
    """HTTP 429 is a retryable rate-limit response, not a fatal error."""
    sleeps: list[float] = []
    transport = FakeTransport(
        [
            HttpResponse(429, '{"error":{"message":"rate limited"}}'),
            _completion({"role": "assistant", "content": "recovered"}),
        ]
    )
    client = OpenAICompatibleClient(
        _settings(max_retries=1),
        transport,
        sleep=sleeps.append,
    )
    result = client.complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )
    assert result.assistant_message == "recovered"
    assert len(transport.requests) == 2
    assert sleeps == [0.5]


def test_rate_limit_429_is_safe_after_exhausting_retries(database, caplog):
    transport = FakeTransport(
        [
            HttpResponse(429, '{"error":{"message":"rate limited"}}'),
            HttpResponse(429, '{"error":{"message":"rate limited"}}'),
        ]
    )
    client = OpenAICompatibleClient(
        _settings(max_retries=1),
        transport,
        sleep=lambda _: None,
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ProviderError) as error:
            client.complete(
                messages=[ProviderMessage(role="system", content="system")],
                tools=_tools(database),
            )
    assert error.value.code == "provider_rate_limited"
    assert API_KEY not in str(error.value)
    assert len(transport.requests) == 2


def test_network_failure_is_retried_and_safe(database):
    transport = FakeTransport(
        [
            ProviderError(
                "network_error", "The provider endpoint could not be reached."
            ),
            _completion({"role": "assistant", "content": "recovered"}),
        ]
    )
    client = OpenAICompatibleClient(
        _settings(max_retries=1),
        transport,
        sleep=lambda _: None,
    )
    result = client.complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=_tools(database),
    )
    assert result.assistant_message == "recovered"
    assert len(transport.requests) == 2


def test_missing_provider_config_file(tmp_path: Path):
    with pytest.raises(ProviderError) as error:
        load_provider_profile(tmp_path / "absent.toml", "any")
    assert error.value.code == "provider_config_not_found"


def test_provider_config_rejects_literal_secrets(tmp_path: Path):
    config_path = tmp_path / "literal.toml"
    config_path.write_text(
        """
[providers.unsafe]
base_url = "https://example.test/v1"
api_key_env = "SAFE_KEY"
model = "m"
api_key = "sk-should-not-be-here"
""",
        encoding="utf-8",
    )
    with pytest.raises(ProviderError) as error:
        load_provider_profile(config_path, "unsafe")
    assert error.value.code == "invalid_provider_config"
    assert "sk-should-not-be-here" not in str(error.value)


def test_invalid_api_key_control_characters():
    profile = ProviderProfile(
        base_url="https://example.test/v1",
        api_key_env="KEY",
        model="m",
    )
    with pytest.raises(ProviderError) as error:
        resolve_provider_profile(
            profile,
            environ={"KEY": "bad\nkey"},
        )
    assert error.value.code == "invalid_api_key"


def test_native_adapter_registration():
    protocol = "test-native-protocol"

    class NativeClient:
        def complete(self, *, messages, tools):
            return ProviderResponse(assistant_message="native response")

    register_provider_factory(protocol, lambda _config: NativeClient())
    config = _settings(api_protocol=protocol)
    client = create_provider_client(config)
    result = client.complete(
        messages=[ProviderMessage(role="system", content="system")],
        tools=[],
    )
    assert result.assistant_message == "native response"


def test_unknown_provider_protocol():
    with pytest.raises(ProviderError) as error:
        create_provider_client(_settings(api_protocol="not-registered"))
    assert error.value.code == "unknown_provider_protocol"


def test_extra_params_cannot_override_reserved_fields():
    with pytest.raises(ValueError):
        ProviderProfile(
            base_url="https://example.test/v1",
            api_key_env="KEY",
            model="m",
            extra_params={"messages": []},
        )
