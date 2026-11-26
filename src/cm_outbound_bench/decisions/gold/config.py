"""Dedicated DeepSeek V4 Pro judge configuration.

The Ground Truth Generator / Evaluation Judge configuration is loaded from
``configs/decision_annotation.toml`` and is deliberately separate from the Group A
agent configuration (``configs/baseline_agent.toml``). The two roles are never
mixed: the annotator records its own model identifier, prompt version, prompt hash
and configuration hash in every artifact.

No silent fallback exists. If the resolved model identifier differs from the
configured one, the pipeline refuses to start. If the endpoint is unavailable, the
exact error is reported and API-dependent execution stops.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Mapping

from pydantic import Field, ValidationError

from cm_outbound_bench.domain.data_model import DomainModel

DEFAULT_JUDGE_CONFIG = Path("configs/decision_annotation.toml")
JUDGE_CONFIG_ENV = "CM_JUDGE_CONFIG"

ENV_API_BASE = "CM_JUDGE_API_BASE"
ENV_API_KEY = "CM_JUDGE_API_KEY"
ENV_MODEL = "CM_JUDGE_MODEL"
ENV_TIMEOUT = "CM_JUDGE_TIMEOUT"
ENV_MAX_RETRIES = "CM_JUDGE_MAX_RETRIES"
ENV_TEMPERATURE = "CM_JUDGE_TEMPERATURE"
ENV_MAX_OUTPUT_TOKENS = "CM_JUDGE_MAX_OUTPUT_TOKENS"
ENV_HEADER_ENVS = "CM_JUDGE_HEADER_ENVS"
ENV_ANNOTATION_PROMPT_VERSION = "CM_JUDGE_ANNOTATION_PROMPT_VERSION"
ENV_EVALUATION_PROMPT_VERSION = "CM_JUDGE_EVALUATION_PROMPT_VERSION"

#: Model identifiers this pipeline has been verified against. Changing this list
#: requires a deliberate code change plus re-verification against the endpoint.
SUPPORTED_JUDGE_MODELS: frozenset[str] = frozenset({"deepseek-v4-pro"})

DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 6000


class JudgeConfigError(RuntimeError):
    """Raised when the judge configuration is missing or invalid."""


class RetryPolicy(DomainModel):
    max_attempts: int = Field(default=4, ge=1, le=10)
    backoff_base_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=5.0)


class JudgeStageConfig(DomainModel):
    prompt_version: str = "v1"
    guideline_version: str = "annotation-guidelines-v1"
    concurrency: int = Field(default=4, ge=1, le=16)
    batch_size: int = Field(default=20, ge=1)


class JudgeConfig(DomainModel):
    """Fully resolved judge configuration. Never holds a credential."""

    label: str = "deepseek-v4-pro-judge"
    base_url: str
    api_key_env: str
    model: str
    timeout: float = Field(default=120.0, gt=0, le=600)
    max_retries: int = Field(default=3, ge=0, le=10)
    extra_params: dict[str, object] = Field(default_factory=dict)
    headers_env: dict[str, str] = Field(default_factory=dict)
    temperature: float = DEFAULT_TEMPERATURE
    max_output_tokens: int = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, ge=1)
    seed: int | None = None
    annotation: JudgeStageConfig = Field(default_factory=JudgeStageConfig)
    evaluation: JudgeStageConfig = Field(default_factory=JudgeStageConfig)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    def model_checked(self) -> str:
        """Return the model identifier after enforcing the no-substitution rule."""
        if self.model not in SUPPORTED_JUDGE_MODELS:
            raise JudgeConfigError(
                f"judge model {self.model!r} is not the verified DeepSeek V4 Pro "
                f"identifier (supported: {sorted(SUPPORTED_JUDGE_MODELS)}); refusing "
                "to annotate or score with an unverified model"
            )
        return self.model

    def config_hash(self, *, stage: str) -> str:
        """Deterministic hash of the settings that affect a given stage.

        The hash covers the model identifier, endpoint host, generation parameters,
        retry policy and the stage's prompt version. Credentials and header values
        are never part of it.
        """
        from urllib.parse import urlsplit

        host = urlsplit(self.base_url).netloc
        payload = {
            "model": self.model,
            "host": host,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "seed": self.seed,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "retry": self.retry.model_dump(mode="json"),
            "extra_params": self.extra_params,
            "prompt_version": getattr(self, stage).prompt_version,
            "stage": stage,
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise JudgeConfigError(f"cannot read judge config {path}: {error}") from error


def load_judge_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> JudgeConfig:
    """Load the judge configuration, applying documented environment overrides."""
    environment = os.environ if environ is None else environ
    explicit = path if path is not None else environment.get(JUDGE_CONFIG_ENV)
    target = Path(explicit).expanduser() if explicit else DEFAULT_JUDGE_CONFIG
    if not target.is_file():
        if explicit:
            raise JudgeConfigError(f"judge config not found: {target}")
        raise JudgeConfigError(
            f"judge config not found: {target} (expected the dedicated DeepSeek "
            "V4 Pro configuration)"
        )
    raw = _load_toml(target)
    provider = raw.get("provider") if isinstance(raw.get("provider"), dict) else {}
    generation = (
        raw.get("generation") if isinstance(raw.get("generation"), dict) else {}
    )
    annotation = (
        raw.get("annotation") if isinstance(raw.get("annotation"), dict) else {}
    )
    evaluation = (
        raw.get("evaluation") if isinstance(raw.get("evaluation"), dict) else {}
    )
    retry = raw.get("retry") if isinstance(raw.get("retry"), dict) else {}

    payload: dict[str, object] = {
        "label": str(provider.get("label", "deepseek-v4-pro-judge")),
        "base_url": environment.get(ENV_API_BASE, provider.get("base_url", "")),
        "api_key_env": str(provider.get("api_key_env", "DEEPSEEK_API_KEY")),
        "model": environment.get(ENV_MODEL, provider.get("model", "")),
        "timeout": float(
            environment.get(ENV_TIMEOUT, provider.get("timeout", 120.0))
        ),
        "max_retries": int(
            environment.get(ENV_MAX_RETRIES, provider.get("max_retries", 3))
        ),
        "extra_params": provider.get("extra_params", {}),
        "headers_env": provider.get("headers_env", {}),
        "temperature": float(
            environment.get(ENV_TEMPERATURE, generation.get("temperature", 0.0))
        ),
        "max_output_tokens": int(
            environment.get(
                ENV_MAX_OUTPUT_TOKENS,
                generation.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS),
            )
        ),
        "seed": generation.get("seed"),
        "annotation": {
            **annotation,
            "prompt_version": environment.get(
                ENV_ANNOTATION_PROMPT_VERSION,
                annotation.get("prompt_version", "v1"),
            ),
        },
        "evaluation": {
            **evaluation,
            "prompt_version": environment.get(
                ENV_EVALUATION_PROMPT_VERSION,
                evaluation.get("prompt_version", "v1"),
            ),
        },
        "retry": retry,
    }
    header_envs = environment.get(ENV_HEADER_ENVS)
    if header_envs:
        try:
            payload["headers_env"] = {**dict(payload["headers_env"]), **json.loads(header_envs)}
        except json.JSONDecodeError as error:
            raise JudgeConfigError(
                f"{ENV_HEADER_ENVS} must be a JSON object"
            ) from error
    try:
        return JudgeConfig.model_validate(payload)
    except ValidationError as error:
        raise JudgeConfigError(
            f"invalid judge config {target}: {error.error_count()} validation error(s)"
        ) from error


def resolve_api_key(
    config: JudgeConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the API key from the environment. Never logged, never stored."""
    environment = os.environ if environ is None else environ
    key = environment.get(config.api_key_env, "")
    if not key.strip():
        raise JudgeConfigError(
            f"environment variable {config.api_key_env} must contain the API key; "
            "no fallback provider or model is used"
        )
    return key


def resolve_header_values(
    config: JudgeConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = os.environ if environ is None else environ
    headers: dict[str, str] = {}
    for header, env_name in config.headers_env.items():
        value = environment.get(env_name)
        if value is None:
            raise JudgeConfigError(
                f"environment variable {env_name} must contain header {header!r}"
            )
        headers[header] = value
    return headers


__all__ = [
    "DEFAULT_JUDGE_CONFIG",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_TEMPERATURE",
    "JUDGE_CONFIG_ENV",
    "SUPPORTED_JUDGE_MODELS",
    "JudgeConfig",
    "JudgeConfigError",
    "JudgeStageConfig",
    "RetryPolicy",
    "load_judge_config",
    "resolve_api_key",
    "resolve_header_values",
]
