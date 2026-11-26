"""Agent tests: structured-output parsing, configuration and failure handling."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pydantic import SecretStr

from cm_outbound_bench.decisions.agent import (
    AgentConfigError,
    OpenAICompatibleChatAgent,
    ResolvedAgentConfig,
    extract_json_object,
    load_agent_config,
)
from cm_outbound_bench.decisions.schema import AgentRequest
from cm_outbound_bench.providers.base import ProviderError
from cm_outbound_bench.providers.http import HttpResponse

REQUEST = AgentRequest(task_id="TASK_X", system_prompt="系统占位", user_prompt="用户占位")
VALID_OUTPUT = {
    "customer_response": "占位客户回应",
    "next_action": "占位下一步动作",
    "required_information": ["占位待确认信息"],
    "tool_intent": None,
    "uncertainty": None,
    "evidence_summary": "占位证据摘要",
}


class FakeTransport:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict, dict, float]] = []

    def post(self, url, headers, payload, timeout):  # noqa: ANN001
        self.calls.append((url, headers, payload, timeout))
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _completion(content: str, usage: dict | None = None) -> HttpResponse:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }
    return HttpResponse(status_code=200, body=json.dumps(body, ensure_ascii=False))


def _config(**overrides) -> ResolvedAgentConfig:
    values = {
        "label": "fake",
        "base_url": "https://api.example.invalid/v1",
        "model": "fake-model",
        "timeout": 5.0,
        "max_retries": 0,
        "temperature": 0.2,
        "max_output_tokens": 256,
        "api_key": SecretStr("sk-fake-secret-value"),
        "headers": {},
        "extra_params": {},
    }
    values.update(overrides)
    return ResolvedAgentConfig(**values)


def test_extract_json_object_handles_fences_and_prose() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('说明文字 {"a": {"b": 2}} 结束') == {"a": {"b": 2}}
    assert extract_json_object('{"a": "含有 } 花括号的字符串"}') == {
        "a": "含有 } 花括号的字符串"
    }
    with pytest.raises(ValueError):
        extract_json_object("没有 JSON")
    with pytest.raises(ValueError):
        extract_json_object('{"a": 1')


def test_successful_invocation_normalizes_output_and_usage() -> None:
    transport = FakeTransport([_completion(json.dumps(VALID_OUTPUT, ensure_ascii=False))])
    agent = OpenAICompatibleChatAgent(_config(), transport=transport)
    invocation = agent.decide(REQUEST)
    assert invocation.status == "success"
    assert invocation.output is not None
    assert invocation.output.next_action == "占位下一步动作"
    assert invocation.token_usage["total_tokens"] == 18
    assert invocation.attempts == 1
    url, headers, payload, timeout = transport.calls[0]
    assert url == "https://api.example.invalid/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-fake-secret-value"
    assert payload["model"] == "fake-model"
    assert payload["messages"][0]["role"] == "system"
    assert payload["max_tokens"] == 256
    assert timeout == 5.0


def test_missing_required_field_is_invalid_output() -> None:
    payload = dict(VALID_OUTPUT)
    payload.pop("evidence_summary")
    agent = OpenAICompatibleChatAgent(
        _config(), transport=FakeTransport([_completion(json.dumps(payload, ensure_ascii=False))])
    )
    invocation = agent.decide(REQUEST)
    assert invocation.status == "invalid_output"
    assert invocation.error_code == "missing_required_fields"
    assert "evidence_summary" in (invocation.error_message or "")


def test_non_null_tool_intent_is_rejected() -> None:
    payload = dict(VALID_OUTPUT, tool_intent={"tool": "invented"})
    agent = OpenAICompatibleChatAgent(
        _config(), transport=FakeTransport([_completion(json.dumps(payload, ensure_ascii=False))])
    )
    invocation = agent.decide(REQUEST)
    assert invocation.status == "invalid_output"
    assert invocation.error_code == "tool_intent_not_supported"


def test_unparseable_text_is_invalid_output() -> None:
    agent = OpenAICompatibleChatAgent(
        _config(), transport=FakeTransport([_completion("这不是 JSON")])
    )
    invocation = agent.decide(REQUEST)
    assert invocation.status == "invalid_output"
    assert invocation.error_code == "unparseable_output"


def test_retryable_status_is_retried_then_succeeds() -> None:
    transport = FakeTransport(
        [
            HttpResponse(status_code=429, body='{"error":"rate limited"}'),
            _completion(json.dumps(VALID_OUTPUT, ensure_ascii=False)),
        ]
    )
    agent = OpenAICompatibleChatAgent(
        _config(max_retries=1), transport=transport, sleep=lambda _seconds: None
    )
    invocation = agent.decide(REQUEST)
    assert invocation.status == "success"
    assert invocation.attempts == 2
    assert len(transport.calls) == 2


def test_exhausted_retries_become_provider_error() -> None:
    transport = FakeTransport([HttpResponse(status_code=503, body="{}")])
    agent = OpenAICompatibleChatAgent(
        _config(max_retries=0), transport=transport, sleep=lambda _seconds: None
    )
    invocation = agent.decide(REQUEST)
    assert invocation.status == "provider_error"
    assert invocation.http_status == 503
    assert "HTTP 503" in (invocation.error_message or "")


def test_error_body_never_contains_the_credential() -> None:
    transport = FakeTransport(
        [HttpResponse(status_code=401, body="bad key sk-fake-secret-value rejected")]
    )
    agent = OpenAICompatibleChatAgent(_config(), transport=transport)
    invocation = agent.decide(REQUEST)
    assert "sk-fake-secret-value" not in (invocation.error_message or "")
    assert "[REDACTED]" in (invocation.error_message or "")


def test_transport_failure_at_the_timeout_boundary_is_a_timeout() -> None:
    class SlowFailure(FakeTransport):
        def post(self, url, headers, payload, timeout):  # noqa: ANN001
            time.sleep(0.02)
            raise ProviderError("network_error", "endpoint unreachable")

    agent = OpenAICompatibleChatAgent(
        _config(timeout=0.01), transport=SlowFailure([]), sleep=lambda _seconds: None
    )
    invocation = agent.decide(REQUEST)
    assert invocation.status == "timeout"


def test_agent_config_is_loaded_from_toml_and_environment(tmp_path: Path) -> None:
    config_path = tmp_path / "baseline_agent.toml"
    config_path.write_text(
        "\n".join(
            [
                "[provider]",
                'label = "test-label"',
                'base_url = "https://api.example.invalid/v1"',
                'api_key_env = "TEST_PROVIDER_KEY"',
                'model = "toml-model"',
                "timeout = 30.0",
                "max_retries = 1",
                "",
                "[generation]",
                "temperature = 0.1",
                "max_output_tokens = 128",
            ]
        ),
        encoding="utf-8",
    )
    resolved = load_agent_config(
        config_path,
        environ={"TEST_PROVIDER_KEY": "sk-from-env"},
    )
    assert resolved.model == "toml-model"
    assert resolved.api_key.get_secret_value() == "sk-from-env"
    assert resolved.temperature == 0.1
    assert resolved.max_output_tokens == 128
    assert resolved.public_parameters == {"temperature": 0.1, "max_output_tokens": 128}

    overridden = load_agent_config(
        config_path,
        environ={
            "TEST_PROVIDER_KEY": "sk-from-env",
            "CM_BASELINE_MODEL": "env-model",
            "CM_BASELINE_TEMPERATURE": "0.7",
            "CM_BASELINE_MAX_RETRIES": "0",
        },
    )
    assert overridden.model == "env-model"
    assert overridden.temperature == 0.7
    assert overridden.max_retries == 0


def test_config_hash_and_public_parameters_exclude_the_secret(tmp_path: Path) -> None:
    config_path = tmp_path / "baseline_agent.toml"
    config_path.write_text(
        "\n".join(
            [
                "[provider]",
                'base_url = "https://api.example.invalid/v1"',
                'api_key_env = "TEST_PROVIDER_KEY"',
                'model = "toml-model"',
            ]
        ),
        encoding="utf-8",
    )
    resolved = load_agent_config(config_path, environ={"TEST_PROVIDER_KEY": "sk-hidden"})
    assert "sk-hidden" not in resolved.config_hash
    assert "sk-hidden" not in json.dumps(resolved.public_parameters)
    assert resolved.redacted_headers() == {}


def test_missing_api_key_is_a_configuration_error(tmp_path: Path) -> None:
    config_path = tmp_path / "baseline_agent.toml"
    config_path.write_text(
        "\n".join(
            [
                "[provider]",
                'base_url = "https://api.example.invalid/v1"',
                'api_key_env = "ABSENT_KEY"',
                'model = "toml-model"',
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(AgentConfigError) as caught:
        load_agent_config(config_path, environ={})
    assert "missing_api_key" in str(caught.value)
