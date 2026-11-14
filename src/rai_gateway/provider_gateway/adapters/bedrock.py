"""AWS Bedrock model invocation adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.adapters.common import (
    load_json,
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

ROUTE_PREFIX = "/providers/bedrock"


def _model_from_body(body: dict[str, Any]) -> str | None:
    for key in ("modelId", "model_id", "model"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _usage_from_bedrock(response: Mapping[str, Any] | None) -> UsageInfo | None:
    if not response:
        return None
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    inp = usage.get("inputTokens")
    out = usage.get("outputTokens")
    total = usage.get("totalTokens")
    return normalize_usage(
        provider="bedrock",
        model=_model_from_body(response) if isinstance(response, dict) else None,
        input_tokens=inp if isinstance(inp, int) else None,
        output_tokens=out if isinstance(out, int) else None,
        total_tokens=total if isinstance(total, int) else None,
        confidence=UsageConfidence.PROVIDER_REPORTED,
        provider_reported=True,
    )


class BedrockAdapter(ProviderProtocolAdapter):
    provider_name = "bedrock"
    passthrough_headers = ("x-amz-target", "amz-sdk-request")

    def matches_request(self, request: ProxyRequest) -> bool:
        return request.path.startswith(ROUTE_PREFIX)

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        body = load_json(request.body)
        model = _model_from_body(body)
        operation = "converse" if "converse" in request.path else "invoke"
        region = settings.aws_region
        profile = body.get("inferenceProfileArn") or body.get("inference_profile_arn")
        stream = self.is_streaming(request)
        return GatewayAction(
            action_type=GatewayActionType.PROVIDER_CALL.value,
            provider=self.provider_name,
            operation=operation,
            model=model,
            parameters={
                "stream": stream,
                "region": region,
                "inference_profile": profile,
            },
            input_metadata={
                "resource_mapping": {
                    "provider": self.provider_name,
                    "model": model,
                    "region": region,
                    "inference_profile": profile,
                    "endpoint": strip_route_prefix(request.path, ROUTE_PREFIX),
                },
                "stream": stream,
            },
        )

    def upstream_url(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> str:
        suffix = strip_route_prefix(request.path, ROUTE_PREFIX)
        base = credential.base_url.rstrip("/")
        return f"{base}{suffix}"

    def is_streaming(self, request: ProxyRequest) -> bool:
        body = load_json(request.body)
        return bool(body.get("stream")) or "stream" in request.path

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        return _usage_from_bedrock(response_body)