"""Small extension registry for generic and native provider adapters."""

from __future__ import annotations

from collections.abc import Callable

from cm_outbound_bench.providers.base import ProviderClient, ProviderError
from cm_outbound_bench.providers.config import ResolvedProviderConfig

ProviderFactory = Callable[[ResolvedProviderConfig], ProviderClient]

_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider_factory(
    api_protocol: str,
    factory: ProviderFactory,
) -> None:
    """Register a native or generic adapter under a user-selected protocol ID."""
    if not api_protocol.strip():
        raise ValueError("api_protocol must not be empty")
    _FACTORIES[api_protocol] = factory


def create_provider_client(config: ResolvedProviderConfig) -> ProviderClient:
    """Create the client registered for the profile's protocol."""
    factory = _FACTORIES.get(config.api_protocol)
    if factory is None:
        raise ProviderError(
            "unknown_provider_protocol",
            f"No provider adapter is registered for protocol {config.api_protocol!r}.",
        )
    return factory(config)
