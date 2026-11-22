"""Provider profile loading and secure environment resolution."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator

from cm_outbound_bench.domain.data_model import DomainModel
from cm_outbound_bench.providers.base import ProviderError

_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_REQUEST_PARAMS = {
    "model",
    "messages",
    "tools",
    "stream",
}

#: Generation parameters that may be recorded in a run manifest. Everything else
#: in ``extra_params`` stays local because it has not been reviewed as safe.
_PUBLIC_PARAMETER_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "seed",
        "stop",
        "frequency_penalty",
        "presence_penalty",
        "response_format",
        "tool_choice",
        "reasoning_effort",
    }
)


class ProviderProfile(DomainModel):
    """One user-defined provider label loaded from the local TOML file."""

    api_protocol: str = Field(
        default="openai_compatible",
        description="Registered adapter protocol, not a vendor name.",
    )
    base_url: str = Field(
        description="OpenAI-compatible API base URL or full chat-completions URL."
    )
    api_key_env: str = Field(
        description="Environment variable that contains the API key."
    )
    model: str = Field(min_length=1)
    timeout: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)
    tool_mode: Literal["function_calling", "json"] = Field(
        default="function_calling",
        description="Native function calling or a JSON structured-action fallback.",
    )
    headers_env: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP header name to environment-variable name.",
    )
    extra_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional provider-specific JSON request parameters.",
    )

    @field_validator("api_protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("api_protocol must not be empty")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _checked_base_url(value)

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_env(cls, value: str) -> str:
        if not _ENV_NAME.fullmatch(value):
            raise ValueError("api_key_env must be a valid environment variable name")
        return value

    @field_validator("headers_env")
    @classmethod
    def validate_headers_env(cls, value: dict[str, str]) -> dict[str, str]:
        for header, env_name in value.items():
            if not _HEADER_NAME.fullmatch(header):
                raise ValueError("headers_env contains an invalid header name")
            if not _ENV_NAME.fullmatch(env_name):
                raise ValueError("headers_env contains an invalid environment name")
        return value

    @field_validator("extra_params")
    @classmethod
    def validate_extra_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        reserved = sorted(set(value) & _RESERVED_REQUEST_PARAMS)
        if reserved:
            raise ValueError(
                "extra_params cannot override reserved request fields: "
                f"{', '.join(reserved)}"
            )
        return value


class ProviderProfileFile(DomainModel):
    providers: dict[str, ProviderProfile]

    @model_validator(mode="after")
    def validate_profiles(self) -> "ProviderProfileFile":
        if not self.providers:
            raise ValueError("at least one provider profile is required")
        return self


class ResolvedProviderConfig(DomainModel):
    """Runtime configuration with secrets resolved from the environment."""

    api_protocol: str = Field(min_length=1)
    base_url: str
    model: str = Field(min_length=1)
    timeout: float = Field(gt=0, le=600)
    max_retries: int = Field(ge=0, le=10)
    tool_mode: Literal["function_calling", "json"]
    api_key: SecretStr
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    extra_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _checked_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if not secret.strip():
            raise ValueError("api_key must not be empty")
        if _has_header_control_characters(secret):
            raise ValueError("api_key contains invalid control characters")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        for header, secret in value.items():
            if not _HEADER_NAME.fullmatch(header):
                raise ValueError("headers contains an invalid header name")
            if _has_header_control_characters(secret.get_secret_value()):
                raise ValueError("headers contains an invalid header value")
        return value

    @property
    def chat_completions_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def public_parameters(self) -> dict[str, Any]:
        """Return non-secret sampling parameters safe to record in a manifest.

        Only a fixed allow-list of generation parameters is exposed. Headers and
        the API key are never part of it.
        """
        return {
            key: value
            for key, value in self.extra_params.items()
            if key in _PUBLIC_PARAMETER_KEYS
        }


def load_provider_profile(
    path: str | Path,
    profile_name: str,
) -> ProviderProfile:
    """Load one arbitrary, user-defined provider profile from TOML."""
    config_path = Path(path)
    if not config_path.is_file():
        raise ProviderError(
            "provider_config_not_found",
            f"Provider configuration file was not found: {config_path}",
        )
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        profile_file = ProviderProfileFile.model_validate(raw)
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        ValidationError,
    ):
        raise ProviderError(
            "invalid_provider_config",
            "Provider configuration is invalid.",
        ) from None

    profile = profile_file.providers.get(profile_name)
    if profile is None:
        available = ", ".join(sorted(profile_file.providers))
        raise ProviderError(
            "unknown_provider",
            f"Provider profile {profile_name!r} was not found. "
            f"Available profiles: {available}",
        )
    return profile


def resolve_provider_profile(
    profile: ProviderProfile,
    environ: Mapping[str, str] | None = None,
) -> ResolvedProviderConfig:
    """Resolve API keys and header values from environment variables."""
    environment = os.environ if environ is None else environ
    api_key = environment.get(profile.api_key_env)
    if api_key is None or not api_key.strip():
        raise ProviderError(
            "missing_api_key",
            f"Environment variable {profile.api_key_env} must contain an API key.",
        )
    if _has_header_control_characters(api_key):
        raise ProviderError(
            "invalid_api_key",
            "The resolved API key contains invalid control characters.",
        )

    resolved_headers: dict[str, SecretStr] = {}
    for header, env_name in profile.headers_env.items():
        value = environment.get(env_name)
        if value is None:
            raise ProviderError(
                "missing_header_value",
                f"Environment variable {env_name} must contain header {header!r}.",
            )
        if _has_header_control_characters(value):
            raise ProviderError(
                "invalid_header_value",
                f"Environment variable {env_name} contains an invalid header value.",
            )
        resolved_headers[header] = SecretStr(value)

    try:
        return ResolvedProviderConfig(
            api_protocol=profile.api_protocol,
            base_url=profile.base_url,
            model=profile.model,
            timeout=profile.timeout,
            max_retries=profile.max_retries,
            tool_mode=profile.tool_mode,
            api_key=SecretStr(api_key),
            headers=resolved_headers,
            extra_params=profile.extra_params,
        )
    except ValidationError:
        raise ProviderError(
            "invalid_provider_config",
            "Resolved provider configuration is invalid.",
        ) from None


def default_provider_config_path(
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Use the configured path or ``providers.toml`` in the current directory."""
    environment = os.environ if environ is None else environ
    configured = environment.get("CM_OUTBOUND_PROVIDER_CONFIG")
    return Path(configured) if configured else Path("providers.toml")


def _checked_base_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "base_url must be an http(s) URL without credentials, query or fragment"
        )
    return value


def _has_header_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
