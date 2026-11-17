"""Test doubles for the provider proxy core."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from rai_gateway.provider_gateway.adapters.base import (
    ProviderProtocolAdapter,
    StreamUsageCollector,
)
from rai_gateway.provider_gateway.auth import (
    ProviderCredentialResolver,
    ProviderCredentialUnavailable,
)
from rai_gateway.provider_gateway.domain import (
    ProviderCredential,
    ProxyRequest,
    UsageInfo,
)
from rai_gateway.runtime_enforcement.gateway_action import GatewayAction

UPSTREAM_KEY = "gateway-held-upstream-key"


class FakeUsageCollector(StreamUsageCollector):
    def __init__(self) -> None:
        self._seen = b""

    def feed(self, chunk: bytes) -> None:
        self._seen += chunk

    def usage(self) -> UsageInfo | None:
        if b"[DONE]" in self._seen:
            return UsageInfo(input_tokens=7, output_tokens=3, total_tokens=10)
        return None


class FakeAdapter(ProviderProtocolAdapter):
    provider_name = "fakeprov"
    passthrough_headers = ("x-fake-version",)

    def matches_request(self, request: ProxyRequest) -> bool:
        return request.path.startswith("/fake/")

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        import json

        body = json.loads(request.body or b"{}")
        return GatewayAction(
            action_type="PROVIDER_CALL",
            provider=self.provider_name,
            operation="chat",
            model=body.get("model"),
            declared_tools=[t["name"] for t in body.get("tools", [])],
        )

    def upstream_url(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> str:
        return credential.base_url + request.path.removeprefix("/fake")

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        usage = (response_body or {}).get("usage")
        if not usage:
            return None
        return UsageInfo(
            input_tokens=usage.get("in"),
            output_tokens=usage.get("out"),
            total_tokens=usage.get("total"),
        )

    def is_streaming(self, request: ProxyRequest) -> bool:
        import json

        return bool(json.loads(request.body or b"{}").get("stream"))

    def new_stream_usage_collector(self) -> StreamUsageCollector:
        return FakeUsageCollector()


class StaticResolver(ProviderCredentialResolver):
    def __init__(self, available: bool = True) -> None:
        self.available = available

    def resolve(self, provider: str, *, context=None) -> ProviderCredential:
        if not self.available:
            raise ProviderCredentialUnavailable(provider)
        return ProviderCredential(
            provider=provider,
            base_url="https://upstream.test/v1",
            headers={"Authorization": f"Bearer {UPSTREAM_KEY}"},
        )


class Upstream:
    """Records what the proxy actually sent upstream."""

    def __init__(self, handler) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)
