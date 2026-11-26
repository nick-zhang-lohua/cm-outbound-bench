"""Stateless DeepSeek V4 Pro chat-completions client for the judge.

Every call is independent: no session state, no conversation history, no cached
context. The client enforces the no-substitution rule (the resolved model must be
the verified ``deepseek-v4-pro`` identifier) and records the provider-returned
request id, model identifier, usage and a response hash in the returned record.
Credentials are never stored and error text is redacted before it is persisted.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from cm_outbound_bench.decisions.gold.config import JudgeConfig, JudgeConfigError
from cm_outbound_bench.providers.base import ProviderError, redact_secrets
from cm_outbound_bench.providers.http import HttpResponse, UrllibHttpTransport

_RETRYABLE_STATUS = {408, 429}


@dataclass(frozen=True)
class JudgeResponse:
    """One normalized, secret-free API response."""

    content: str
    model_reported: str
    api_request_id: str
    token_usage: dict[str, int] = field(default_factory=dict)
    response_hash: str = ""
    retry_count: int = 0
    http_status: int = 200


class JudgeClient:
    """Dedicated Ground Truth Generator / Evaluation Judge client."""

    def __init__(
        self,
        config: JudgeConfig,
        *,
        api_key: str | None = None,
        headers: Mapping[str, str] | None = None,
        transport: Any | None = None,
    ) -> None:
        self._config = config
        self._model = config.model_checked()
        self._transport = transport if transport is not None else UrllibHttpTransport()
        self._api_key = api_key
        self._headers = dict(headers or {})
        self._secrets: list[str] = []

    @property
    def model(self) -> str:
        return self._model

    def with_credentials(self, api_key: str, headers: Mapping[str, str]) -> "JudgeClient":
        """Return a copy carrying credentials. The original never holds them."""
        return JudgeClient(
            self._config,
            api_key=api_key,
            headers=headers,
            transport=self._transport,
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        seed: int | None = None,
    ) -> JudgeResponse:
        """Send one stateless JSON-mode request and return the normalized record."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (
                self._config.temperature if temperature is None else temperature
            ),
            "max_tokens": (
                self._config.max_output_tokens
                if max_output_tokens is None
                else max_output_tokens
            ),
        }
        effective_seed = self._config.seed if seed is None else seed
        if effective_seed is not None:
            payload["seed"] = effective_seed
        payload.update(self._config.extra_params)

        attempts = self._config.retry.max_attempts
        last_error: ProviderError | None = None
        for attempt in range(attempts):
            if attempt:
                delay = self._config.retry.backoff_base_seconds * (
                    self._config.retry.backoff_factor ** (attempt - 1)
                )
                time.sleep(delay)
            try:
                response = self._transport.post(
                    self._chat_url(),
                    self._request_headers(),
                    payload,
                    self._config.timeout,
                )
            except ProviderError as error:
                last_error = error
                continue
            if 200 <= response.status_code < 300:
                return self._normalize(response, retry_count=attempt)
            if (
                response.status_code in _RETRYABLE_STATUS
                or response.status_code >= 500
            ):
                last_error = self._http_error(response)
                continue
            raise self._http_error(response)
        assert last_error is not None
        raise last_error

    def _chat_url(self) -> str:
        base = self._config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _request_headers(self) -> dict[str, str]:
        if self._api_key is None:
            raise JudgeConfigError(
                "judge client has no API key; call with_credentials() first"
            )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        headers.update(self._headers)
        self._secrets = [self._api_key, *self._headers.values()]
        return headers

    def _http_error(self, response: HttpResponse) -> ProviderError:
        if response.status_code in {401, 403}:
            code = "provider_authentication_error"
        elif response.status_code == 429:
            code = "provider_rate_limited"
        else:
            code = "provider_http_error"
        message = f"Judge request failed with HTTP {response.status_code}."
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
        return redact_secrets(detail.strip(), self._secrets)[:240]

    def _normalize(self, response: HttpResponse, *, retry_count: int) -> JudgeResponse:
        try:
            data = json.loads(response.body)
        except json.JSONDecodeError:
            raise ProviderError(
                "invalid_provider_response",
                "The judge endpoint returned a non-JSON response.",
            ) from None
        if not isinstance(data, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The judge response must be a JSON object.",
            )
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                "invalid_provider_response",
                "The judge response did not contain a completion choice.",
            )
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ProviderError(
                "invalid_provider_response",
                "The judge completion message is malformed.",
            )
        content = _content_text(message.get("content"))
        if not content.strip():
            raise ProviderError(
                "invalid_provider_response",
                "The judge returned an empty completion.",
            )
        usage = data.get("usage")
        provider_usage = usage if isinstance(usage, dict) else {}
        reported_model = data.get("model")
        return JudgeResponse(
            content=content,
            model_reported=reported_model if isinstance(reported_model, str) else "",
            api_request_id=data.get("id") if isinstance(data.get("id"), str) else "",
            token_usage={
                "prompt_tokens": int(provider_usage.get("prompt_tokens") or 0),
                "completion_tokens": int(
                    provider_usage.get("completion_tokens") or 0
                ),
                "total_tokens": int(provider_usage.get("total_tokens") or 0),
            },
            response_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            retry_count=retry_count,
            http_status=response.status_code,
        )


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


def redact_message(message: str, secrets: Sequence[str]) -> str:
    return redact_secrets(message, list(secrets))[:300]


__all__ = [
    "JudgeClient",
    "JudgeResponse",
    "redact_message",
]
