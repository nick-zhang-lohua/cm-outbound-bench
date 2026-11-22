"""Provider-neutral integration surface for the benchmark."""

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
from cm_outbound_bench.providers.config import (
    ProviderProfile,
    ResolvedProviderConfig,
    default_provider_config_path,
    load_provider_profile,
    resolve_provider_profile,
)
from cm_outbound_bench.providers.openai_compatible import (
    OpenAICompatibleClient,
    build_openai_compatible_client,
)
from cm_outbound_bench.providers.registry import (
    ProviderFactory,
    create_provider_client,
    register_provider_factory,
)

register_provider_factory(
    "openai_compatible",
    build_openai_compatible_client,
)

__all__ = [
    "FINISH_TOOL",
    "FINISH_TOOL_NAME",
    "OpenAICompatibleClient",
    "ProviderClient",
    "ProviderError",
    "ProviderFactory",
    "ProviderMessage",
    "ProviderProfile",
    "ProviderResponse",
    "ProviderToolCall",
    "ResolvedProviderConfig",
    "build_openai_compatible_client",
    "create_provider_client",
    "default_provider_config_path",
    "load_provider_profile",
    "redact_secrets",
    "register_provider_factory",
    "resolve_provider_profile",
    "validate_tool_call",
]
