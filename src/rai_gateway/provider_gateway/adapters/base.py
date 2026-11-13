"""Provider protocol adapter interface.

Adapters translate a provider-native request into *governance metadata only*.
They never rewrite the request into a generic RAI shape: the original body is
forwarded byte-for-byte by the proxy core.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from typing import Any

from rai_gateway.provider_gateway.domain import (
    ProviderCredential,
    ProxyRequest,
    UsageInfo,
)
from rai_gateway.provider_gateway.headers import (
    filter_inbound_headers,
    filter_outbound_headers,
    inject_gateway_headers,
)
from rai_gateway.runtime_enforcement.gateway_action import GatewayAction


class StreamUsageCollector:
    """Observes streamed bytes (read-only) to recover usage. Default: none."""

    def feed(self, chunk: bytes) -> None:  # pragma: no cover - trivial
        return None

    def usage(self) -> UsageInfo | None:
        return None


class ProviderProtocolAdapter(ABC):
    provider_name: str
    # Extra inbound headers this provider legitimately needs (e.g. api versions).
    passthrough_headers: Iterable[str] = ()

    @abstractmethod
    def matches_request(self, request: ProxyRequest) -> bool:
        """True when this adapter owns the route."""

    @abstractmethod
    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        """Derive canonical governance metadata from the native request.

        Must not copy prompts/messages into the returned action. The proxy core
        adds the body digest and size.
        """

    @abstractmethod
    def upstream_url(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> str:
        """Server-controlled upstream URL (never derived from client hosts)."""

    @abstractmethod
    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        """Usage from a non-streaming provider response body."""

    def is_streaming(self, request: ProxyRequest) -> bool:
        return False

    def new_stream_usage_collector(self) -> StreamUsageCollector:
        return StreamUsageCollector()

    def upstream_headers(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> dict[str, str]:
        safe = filter_inbound_headers(
            request.headers, extra_allowed=self.passthrough_headers
        )
        return inject_gateway_headers(safe, credential.headers)

    def sanitize_response_metadata(self, headers: Mapping[str, str]) -> dict[str, str]:
        return filter_outbound_headers(headers)
