"""Anthropic Messages API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.adapters.common import (
    declared_tool_names,
    load_json,
    strip_route_prefix,
    usage_from_mapping,
)
from rai_gateway.provider_gateway.domain import ProviderCredential, ProxyRequest, UsageInfo
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayActionType,
)

ROUTE_PREFIX = "/providers/anthropic/v1"


def _anthropic_usage_from_event(event: dict[str, Any]) -> UsageInfo | None:
    usage = event.get("usage")
    if isinstance(usage, dict):
        return usage_from_mapping(
            usage,
            provider="anthropic",
            model=str(event["model"]) if event.get("model") else None,
            input_key="input_tokens",
            output_key="output_tokens",
            cached_key="cache_read_input_tokens",
        )
    message = event.get("message")
    if isinstance(message, dict):
        return _anthropic_usage_from_event(message)
    return None


class AnthropicStreamUsageCollector:
    def __init__(self) -> None:
        self._usage: UsageInfo | None = None
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            try:
                import json

                event = json.loads(payload)
            except ValueError:
                continue
            if isinstance(event, dict):
                parsed = _anthropic_usage_from_event(event)
                if parsed is not None:
                    self._usage = parsed

    def usage(self) -> UsageInfo | None:
        return self._usage


class AnthropicAdapter(ProviderProtocolAdapter):
    provider_name = "anthropic"
    passthrough_headers = (
        "anthropic-beta",
        "anthropic-dangerous-direct-browser-access",
    )

    def matches_request(self, request: ProxyRequest) -> bool:
        return request.path.startswith(ROUTE_PREFIX)

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        body = load_json(request.body)
        model = body.get("model")
        model_s = str(model).strip() if isinstance(model, str) else None
        max_tokens = body.get("max_tokens")
        stream = self.is_streaming(request)
        system_present = bool(body.get("system"))
        return GatewayAction(
            action_type=GatewayActionType.PROVIDER_CALL.value,
            provider=self.provider_name,
            operation="messages",
            model=model_s,
            declared_tools=declared_tool_names(body),
            parameters={
                "stream": stream,
                "max_tokens": max_tokens if isinstance(max_tokens, int) else None,
                "system_present": system_present,
            },
            input_metadata={
                "resource_mapping": {
                    "provider": self.provider_name,
                    "model": model_s,
                    "endpoint": strip_route_prefix(request.path, ROUTE_PREFIX),
                },
                "stream": stream,
                "tool_count": len(declared_tool_names(body)),
            },
        )

    def upstream_url(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> str:
        suffix = strip_route_prefix(request.path, ROUTE_PREFIX)
        base = credential.base_url.rstrip("/")
        return f"{base}{suffix}"

    def is_streaming(self, request: ProxyRequest) -> bool:
        return bool(load_json(request.body).get("stream"))

    def new_stream_usage_collector(self):
        return AnthropicStreamUsageCollector()

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        if not response_body:
            return None
        usage = response_body.get("usage")
        model = response_body.get("model")
        if isinstance(usage, dict):
            parsed = usage_from_mapping(
                usage,
                provider="anthropic",
                model=str(model) if model else None,
                input_key="input_tokens",
                output_key="output_tokens",
                cached_key="cache_read_input_tokens",
            )
            if parsed is not None:
                return parsed
        return None