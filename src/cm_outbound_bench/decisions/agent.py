"""Provider-independent agent for a single decision point.

The agent receives exactly two strings (system and user prompt), sends one
OpenAI-compatible chat-completions request, and returns one normalized
:class:`AgentInvocation`. It keeps no state between calls, so a fresh instance per
task is a genuine independent session.

Configuration resolution order for every setting:

1. a ``CM_BASELINE_*`` environment variable;
2. the TOML configuration file (``configs/baseline_agent.toml`` by default);
3. a safe default.

Credentials are held in ``SecretStr``, are read only from the environment, are
never written to a trace, and are redacted from any error text.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError

from cm_outbound_bench.decisions.schema import (
    AgentDecisionOutput,
    AgentInvocation,
    AgentRequest,
)
from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.providers.base import ProviderError, redact_secrets
from cm_outbound_bench.providers.http import HttpTransport, UrllibHttpTransport

AGENT_VERSION = "0.1.0"

CONFIG_ENV = "CM_BASELINE_AGENT_CONFIG"
API_BASE_ENV = "CM_BASELINE_API_BASE"
API_KEY_ENV = "CM_BASELINE_API_KEY"
MODEL_ENV = "CM_BASELINE_MODEL"
TIMEOUT_ENV = "CM_BASELINE_TIMEOUT"
MAX_RETRIES_ENV = "CM_BASELINE_MAX_RETRIES"
TEMPERATURE_ENV = "CM_BASELINE_TEMPERATURE"
MAX_OUTPUT_TOKENS_ENV = "CM_BASELINE_MAX_OUTPUT_TOKENS"
HEADER_ENVS_ENV = "CM_BASELINE_HEADER_ENVS"

DEFAULT_CONFIG_PATH = Path("configs/baseline_agent.toml")

#: HTTP statuses worth retrying: rate limiting, conflicts and transient 5xx.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
#: Base backoff in seconds, doubled per attempt and capped.
RETRY_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 8.0
#: Maximum length of an error message kept in a trace.
MAX_ERROR_CHARS = 300


class AgentConfigError(RuntimeError):
    """Raised when the agent configuration cannot be resolved safely."""


class ProviderSection(DomainModel):
    """One OpenAI-compatible endpoint description. Holds no credential."""

    label: str = Field(default="openai_compatible", min_length=1)
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(default=API_KEY_ENV, min_length=1)
    model: str = Field(min_length=1)
    timeout: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)
    headers_env: dict[str, str] = Field(default_factory=dict)
    extra_params: dict[str, Any] = Field(default_factory=dict)


class GenerationSection(DomainModel):
    """Sampling parameters recorded in the trace."""

    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=900, ge=1, le=32768)


class AgentConfigFile(DomainModel):
    provider: ProviderSection
    generation: GenerationSection = Field(default_factory=GenerationSection)


class ResolvedAgentConfig(DomainModel):
    """Runtime configuration with the credential resolved from the environment."""

    label: str
    base_url: str
    model: str
    timeout: float
    max_retries: int
    temperature: float
    max_output_tokens: int
    api_key: SecretStr
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    extra_params: dict[str, Any] = Field(default_factory=dict)

    @property
    def base_url_host(self) -> str:
        """Return the endpoint host only, which is safe to record."""
        return urlsplit(self.base_url).netloc

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def public_parameters(self) -> dict[str, Any]:
        """Return the generation parameters that may appear in a trace."""
        return {
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }

    @property
    def config_hash(self) -> str:
        """Hash of the public configuration. Never includes a secret."""
        payload = {
            "label": self.label,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "extra_params": self.extra_params,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def secret_values(self) -> list[str]:
        """Return the secret strings so callers can redact them from output."""
        values = [self.api_key.get_secret_value()]
        values.extend(secret.get_secret_value() for secret in self.headers.values())
        return [value for value in values if value]

    def redacted_headers(self) -> dict[str, str]:
        return {name: "[REDACTED]" for name in sorted(self.headers)}


def load_agent_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> ResolvedAgentConfig:
    """Load and resolve the agent configuration from TOML plus environment."""
    environment = os.environ if environ is None else environ
    config_path = _config_path(path, environment)
    config = _read_config(config_path)
    provider = config.provider
    generation = config.generation

    base_url = environment.get(API_BASE_ENV) or provider.base_url
    model = environment.get(MODEL_ENV) or provider.model
    timeout = _float_env(environment, TIMEOUT_ENV, provider.timeout)
    max_retries = _int_env(environment, MAX_RETRIES_ENV, provider.max_retries)
    temperature = _float_env(environment, TEMPERATURE_ENV, generation.temperature)
    max_output_tokens = _int_env(
        environment, MAX_OUTPUT_TOKENS_ENV, generation.max_output_tokens
    )

    api_key = environment.get(API_KEY_ENV)
    if api_key is None:
        key_env_name = provider.api_key_env
        api_key = environment.get(key_env_name)
        if api_key is None:
            raise AgentConfigError(
                "missing_api_key: set CM_BASELINE_API_KEY or the environment "
                f"variable named by api_key_env ({key_env_name})."
            )
    if not api_key.strip():
        raise AgentConfigError("missing_api_key: the resolved API key is empty.")

    headers = _resolve_headers(provider.headers_env, environment)

    try:
        return ResolvedAgentConfig(
            label=provider.label,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            api_key=SecretStr(api_key),
            headers=headers,
            extra_params=dict(provider.extra_params),
        )
    except ValidationError as error:
        raise AgentConfigError(
            f"invalid_agent_config: {error.error_count()} invalid setting(s)."
        ) from error


def _config_path(
    path: str | Path | None,
    environment: Mapping[str, str],
) -> Path:
    if path is not None:
        return Path(path).expanduser()
    configured = environment.get(CONFIG_ENV)
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CONFIG_PATH


def _read_config(path: Path) -> AgentConfigFile:
    if not path.is_file():
        raise AgentConfigError(
            f"config_not_found: agent configuration not found: {path}"
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        return AgentConfigFile.model_validate(raw)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError):
        raise AgentConfigError(
            "invalid_agent_config: the agent configuration file is invalid."
        ) from None


def _resolve_headers(
    headers_env: Mapping[str, str],
    environment: Mapping[str, str],
) -> dict[str, SecretStr]:
    mapping: dict[str, str] = dict(headers_env)
    override = environment.get(HEADER_ENVS_ENV)
    if override:
        try:
            parsed = json.loads(override)
        except json.JSONDecodeError as error:
            raise AgentConfigError(
                "invalid_header_config: CM_BASELINE_HEADER_ENVS is not JSON."
            ) from error
        if not isinstance(parsed, dict) or any(
            not isinstance(value, str) for value in parsed.values()
        ):
            raise AgentConfigError(
                "invalid_header_config: CM_BASELINE_HEADER_ENVS must map header "
                "names to environment variable names."
            )
        mapping.update(parsed)

    resolved: dict[str, SecretStr] = {}
    for header, env_name in mapping.items():
        value = environment.get(env_name)
        if value is None:
            raise AgentConfigError(
                f"missing_header_value: environment variable {env_name} must "
                f"contain header {header!r}."
            )
        resolved[header] = SecretStr(value)
    return resolved


def _float_env(
    environment: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    value = environment.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as error:
        raise AgentConfigError(f"invalid_agent_config: {name} is not a number.") from error


def _int_env(
    environment: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    value = environment.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as error:
        raise AgentConfigError(
            f"invalid_agent_config: {name} is not an integer."
        ) from error


class DecisionAgent(Protocol):
    """The only agent contract the runner depends on."""

    def decide(self, request: AgentRequest) -> AgentInvocation:
        """Return one normalized decision for one task."""


class ScriptedDecisionAgent:
    """Deterministic offline agent used only to smoke-test the pipeline.

    It is not a baseline result: it ignores the conversation and returns a fixed,
    clearly marked placeholder object. Its purpose is to exercise loading,
    validation, tracing and technical evaluation without a network call.
    """

    PLACEHOLDER_RESPONSE = "（脚本代理占位输出，仅用于管线自检，不是真实基线结果）"

    def __init__(self) -> None:
        self.sessions = 0

    def decide(self, request: AgentRequest) -> AgentInvocation:
        self.sessions += 1
        output = AgentDecisionOutput(
            customer_response=self.PLACEHOLDER_RESPONSE,
            next_action="scripted_smoke_test_only",
            required_information=[],
            tool_intent=None,
            uncertainty="scripted agent, no business decision was made",
            evidence_summary="scripted placeholder; no evidence was assessed",
        )
        return AgentInvocation(
            status="success",
            output=output,
            raw_text=output.model_dump_json(),
            latency_ms=0,
            attempts=1,
        )


class OpenAICompatibleChatAgent:
    """One request to one OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        config: ResolvedAgentConfig,
        *,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibHttpTransport()
        self._sleep = sleep

    @property
    def config(self) -> ResolvedAgentConfig:
        return self._config

    def decide(self, request: AgentRequest) -> AgentInvocation:
        payload = self._build_payload(request)
        headers = self._headers()
        started = time.monotonic()
        attempts = 0
        last_error: ProviderError | None = None
        last_http_status: int | None = None
        timed_out = False

        for attempt in range(self._config.max_retries + 1):
            attempts = attempt + 1
            attempt_started = time.monotonic()
            try:
                response = self._transport.post(
                    self._config.chat_completions_url,
                    headers,
                    payload,
                    self._config.timeout,
                )
            except ProviderError as error:
                if self._looks_like_timeout(attempt_started):
                    timed_out = True
                last_error = error
                if attempt < self._config.max_retries:
                    self._sleep(_backoff(attempt))
                    continue
                break

            last_http_status = response.status_code
            if response.status_code in RETRYABLE_STATUS and (
                attempt < self._config.max_retries
            ):
                last_error = ProviderError(
                    "retryable_http_status",
                    f"provider returned HTTP {response.status_code}",
                )
                self._sleep(_backoff(attempt))
                continue
            if response.status_code >= 400:
                last_error = ProviderError(
                    "http_error",
                    f"provider returned HTTP {response.status_code}: "
                    + _short_error(response.body, self._config.secret_values()),
                )
                break
            return self._parse_success(
                response.body,
                attempts=attempts,
                latency_ms=_elapsed_ms(started),
            )

        latency = _elapsed_ms(started)
        secret_free = _short_error(
            str(last_error) if last_error else "unknown provider error",
            self._config.secret_values(),
        )
        return AgentInvocation(
            status="timeout" if timed_out else "provider_error",
            error_code=last_error.code if last_error else "provider_error",
            error_message=secret_free,
            latency_ms=latency,
            attempts=attempts,
            http_status=last_http_status,
        )

    # -- request ---------------------------------------------------------

    def _build_payload(self, request: AgentRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_output_tokens,
            "stream": False,
        }
        for key, value in self._config.extra_params.items():
            if key not in {"model", "messages", "stream"}:
                payload[key] = value
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key.get_secret_value()}",
        }
        for name, secret in self._config.headers.items():
            headers[name] = secret.get_secret_value()
        return headers

    def _looks_like_timeout(self, attempt_started: float) -> bool:
        """Classify a transport failure as a timeout from the attempt duration.

        ``UrllibHttpTransport`` reports every transport failure as one
        ``network_error`` code, so the elapsed time is the signal available here:
        an attempt that ran for essentially the whole configured timeout is a
        timeout rather than an immediate connection failure.
        """
        elapsed = time.monotonic() - attempt_started
        return elapsed >= self._config.timeout * 0.9

    # -- response --------------------------------------------------------

    def _parse_success(
        self,
        body: str,
        *,
        attempts: int,
        latency_ms: int,
    ) -> AgentInvocation:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return AgentInvocation(
                status="provider_error",
                error_code="invalid_json_body",
                error_message="provider response body was not JSON",
                latency_ms=latency_ms,
                attempts=attempts,
            )
        if not isinstance(payload, dict):
            return AgentInvocation(
                status="provider_error",
                error_code="invalid_response_shape",
                error_message="provider response was not a JSON object",
                latency_ms=latency_ms,
                attempts=attempts,
            )

        usage = _usage(payload.get("usage"))
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return AgentInvocation(
                status="provider_error",
                error_code="empty_choices",
                error_message="provider response contained no choices",
                latency_ms=latency_ms,
                attempts=attempts,
                token_usage=usage,
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return AgentInvocation(
                status="invalid_output",
                error_code="empty_content",
                error_message="provider returned no text content",
                latency_ms=latency_ms,
                attempts=attempts,
                token_usage=usage,
            )
        return _parse_output(content, latency_ms=latency_ms, attempts=attempts, usage=usage)


def _parse_output(
    content: str,
    *,
    latency_ms: int,
    attempts: int,
    usage: dict[str, int],
) -> AgentInvocation:
    """Parse and validate the structured decision from model text."""
    try:
        payload = extract_json_object(content)
    except ValueError as error:
        return AgentInvocation(
            status="invalid_output",
            raw_text=content,
            error_code="unparseable_output",
            error_message=str(error),
            latency_ms=latency_ms,
            attempts=attempts,
            token_usage=usage,
        )

    missing = [
        field
        for field in ("customer_response", "next_action", "evidence_summary")
        if not isinstance(payload.get(field), str) or not payload.get(field, "").strip()
    ]
    if missing:
        return AgentInvocation(
            status="invalid_output",
            raw_text=content,
            error_code="missing_required_fields",
            error_message="missing or empty required fields: " + ", ".join(missing),
            latency_ms=latency_ms,
            attempts=attempts,
            token_usage=usage,
        )
    if payload.get("tool_intent") is not None:
        return AgentInvocation(
            status="invalid_output",
            raw_text=content,
            error_code="tool_intent_not_supported",
            error_message="tool_intent must be null: no business tool is available",
            latency_ms=latency_ms,
            attempts=attempts,
            token_usage=usage,
        )

    required_information = payload.get("required_information", [])
    if not isinstance(required_information, list) or any(
        not isinstance(item, str) for item in required_information
    ):
        return AgentInvocation(
            status="invalid_output",
            raw_text=content,
            error_code="invalid_required_information",
            error_message="required_information must be an array of strings",
            latency_ms=latency_ms,
            attempts=attempts,
            token_usage=usage,
        )
    uncertainty = payload.get("uncertainty")
    if uncertainty is not None and not isinstance(uncertainty, str):
        return AgentInvocation(
            status="invalid_output",
            raw_text=content,
            error_code="invalid_uncertainty",
            error_message="uncertainty must be a string or null",
            latency_ms=latency_ms,
            attempts=attempts,
            token_usage=usage,
        )

    try:
        output = AgentDecisionOutput(
            customer_response=payload["customer_response"],
            next_action=payload["next_action"],
            required_information=required_information,
            tool_intent=None,
            uncertainty=uncertainty,
            evidence_summary=payload["evidence_summary"],
        )
    except ValidationError as error:
        return AgentInvocation(
            status="invalid_output",
            raw_text=content,
            error_code="schema_violation",
            error_message=f"{error.error_count()} schema validation error(s)",
            latency_ms=latency_ms,
            attempts=attempts,
            token_usage=usage,
        )

    return AgentInvocation(
        status="success",
        output=output,
        raw_text=content,
        latency_ms=latency_ms,
        attempts=attempts,
        token_usage=usage,
    )


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from model text.

    Models sometimes wrap JSON in a Markdown fence or add a sentence before it. The
    scanner respects string quoting and escapes, so a brace inside a Chinese
    sentence cannot truncate the object.
    """
    candidate = text.strip()
    if not candidate:
        raise ValueError("empty model output")
    start = candidate.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        character = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(candidate[start : index + 1])
                except json.JSONDecodeError as error:
                    raise ValueError("model output contained invalid JSON") from error
                if not isinstance(payload, dict):
                    raise ValueError("model output JSON was not an object")
                return payload
    raise ValueError("model output contained an unterminated JSON object")


def _usage(raw: Any) -> dict[str, int]:
    """Normalize provider token usage. Unknown usage stays absent, never zero."""
    if not isinstance(raw, dict):
        return {}
    aliases = {
        "prompt_tokens": ("prompt_tokens", "input_tokens"),
        "completion_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
    }
    usage: dict[str, int] = {}
    for target, names in aliases.items():
        for name in names:
            value = raw.get(name)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            usage[target] = value
            break
    if "total_tokens" not in usage and {"prompt_tokens", "completion_tokens"} <= set(
        usage
    ):
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage


def _short_error(message: str, secrets: list[str]) -> str:
    """Redact secrets and bound the length of an error message."""
    redacted = redact_secrets(str(message), secrets)
    redacted = " ".join(redacted.split())
    if len(redacted) > MAX_ERROR_CHARS:
        redacted = redacted[:MAX_ERROR_CHARS] + "…"
    return redacted


def _backoff(attempt: int) -> float:
    return min(MAX_BACKOFF_SECONDS, RETRY_BACKOFF_SECONDS * (2**attempt))


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.monotonic() - started) * 1000)))


__all__ = [
    "AGENT_VERSION",
    "AgentConfigError",
    "AgentConfigFile",
    "DecisionAgent",
    "GenerationSection",
    "OpenAICompatibleChatAgent",
    "ProviderSection",
    "ResolvedAgentConfig",
    "ScriptedDecisionAgent",
    "extract_json_object",
    "load_agent_config",
]
