"""Google AI (Gemini) generateContent adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.adapters.common import (
    declared_tool_names,
    load_json,
    max_output_tokens,
    normalize_usage,
    strip_route_prefix,
)
from rai_gateway.provider_gateway.domain import (
    ProviderCredential,
    ProxyRequest,
    UsageConfidence,
    UsageInfo,
)
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayActionType,
)

ROUTE_PREFIX = "/providers/google-ai"
_MODEL_RE = re.compile(r"/models/([^/:]+)")


def _model_from_request(path: str, body: dict[str, Any]) -> str | None:
    rel = strip_route_prefix(path, ROUTE_PREFIX)
    match = _MODEL_RE.search(rel)
    if match:
        return match.group(1)
    model = body.get("model")
    return str(model).strip() if isinstance(model, str) and model.strip() else None


def _usage_from_gemini(response: Mapping[str, Any] | None) -> UsageInfo | None:
    if not response:
        return None
    metadata = response.get("usageMetadata")
    if not isinstance(metadata, dict):
        return None
    prompt = metadata.get("promptTokenCount")
    completion = metadata.get("candidatesTokenCount")
    total = metadata.get("totalTokenCount")
    cached = metadata.get("cachedContentTokenCount")
    return normalize_usage(
        provider="google_ai",
        model=None,
        input_tokens=prompt if isinstance(prompt, int) else None,
        output_tokens=completion if isinstance(completion, int) else None,
        total_tokens=total if isinstance(total, int) else None,
        cached_input_tokens=cached if isinstance(cached, int) else None,
        confidence=UsageConfidence.PROVIDER_REPORTED,
        provider_reported=True,
    )


class GeminiAdapter(ProviderProtocolAdapter):
    provider_name = "google_ai"
    passthrough_headers = ("x-goog-api-client",)

    def matches_request(self, request: ProxyRequest) -> bool:
        return request.path.startswith(ROUTE_PREFIX)

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        body = load_json(request.body)
        model = _model_from_request(request.path, body)
        generation_config = body.get("generationConfig")
        stream = self.is_streaming(request)
        return GatewayAction(
            action_type=GatewayActionType.PROVIDER_CALL.value,
            provider=self.provider_name,
            operation="generateContent",
            model=model,
            declared_tools=declared_tool_names(body),
            parameters={
                "stream": stream,
                "max_output_tokens": max_output_tokens(body),
            },
            input_metadata={
                "resource_mapping": {
                    "provider": self.provider_name,
                    "model": model,
                    "endpoint": strip_route_prefix(request.path, ROUTE_PREFIX),
                },
                "has_system_instruction": bool(body.get("systemInstruction")),
                "tool_count": len(declared_tool_names(body)),
                "stream": stream,
                "generation_config_keys": sorted(generation_config.keys())
                if isinstance(generation_config, dict)
                else [],
            },
        )

    def upstream_url(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> str:
        suffix = strip_route_prefix(request.path, ROUTE_PREFIX)
        base = credential.base_url.rstrip("/")
        return f"{base}{suffix}"

    def is_streaming(self, request: ProxyRequest) -> bool:
        if "alt=sse" in (request.query or "").lower():
            return True
        body = load_json(request.body)
        config = body.get("generationConfig")
        if isinstance(config, dict) and config.get("responseMimeType") == "text/event-stream":
            return True
        return ":streamGenerateContent" in request.path

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        return _usage_from_gemini(response_body)