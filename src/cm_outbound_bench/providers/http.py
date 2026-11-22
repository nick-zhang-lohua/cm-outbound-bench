"""Small synchronous HTTP transport kept separate from provider adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from cm_outbound_bench.providers.base import ProviderError


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: str


class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> HttpResponse:
        """Send one JSON POST request and return status and body."""


class UrllibHttpTransport:
    """Standard-library transport; no provider SDK is required."""

    def post(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> HttpResponse:
        try:
            encoded = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise ProviderError(
                "invalid_provider_request",
                "The provider request could not be encoded.",
            ) from None

        try:
            http_request = request.Request(
                url,
                data=encoded,
                headers=headers,
                method="POST",
            )
            with request.urlopen(http_request, timeout=timeout) as response:
                return HttpResponse(
                    status_code=response.status,
                    body=response.read().decode("utf-8", errors="replace"),
                )
        except error.HTTPError as exc:
            return HttpResponse(
                status_code=exc.code,
                body=exc.read().decode("utf-8", errors="replace"),
            )
        except (error.URLError, TimeoutError, OSError, TypeError, ValueError):
            raise ProviderError(
                "network_error",
                "The provider endpoint could not be reached.",
            ) from None
