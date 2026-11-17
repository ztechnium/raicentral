"""Azure OpenAI deployment-native adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.adapters.common import (
    declared_tool_names,
    load_json,
    max_output_tokens,
    strip_route_prefix,
)
from rai_gateway.provider_gateway.adapters.openai import (
    OpenAIStreamUsageCollector,
    _usage_from_openai,
)
from rai_gateway.provider_gateway.domain import ProviderCredential, ProxyRequest, UsageInfo
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayActionType,
)

ROUTE_PREFIX = "/providers/azure-openai"
_DEPLOYMENT_RE = re.compile(r"/openai/deployments/([^/]+)/")


class AzureOpenAIAdapter(ProviderProtocolAdapter):
    provider_name = "azure_openai"
    passthrough_headers = ("api-version",)

    def matches_request(self, request: ProxyRequest) -> bool:
        return request.path.startswith(ROUTE_PREFIX)

    def _deployment(self, path: str, body: dict[str, Any]) -> str | None:
        rel = strip_route_prefix(path, ROUTE_PREFIX)
        match = _DEPLOYMENT_RE.search(rel)
        if match:
            return match.group(1)
        model = body.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
        return settings.azure_openai_deployment

    def _operation(self, path: str) -> str:
        rel = strip_route_prefix(path, ROUTE_PREFIX).lstrip("/")
        if "chat/completions" in rel:
            return "chat.completions"
        if "embeddings" in rel:
            return "embeddings"
        return rel.split("/")[-1] if rel else "unknown"

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        body = load_json(request.body)
        deployment = self._deployment(request.path, body)
        operation = self._operation(request.path)
        stream = self.is_streaming(request)
        return GatewayAction(
            action_type=GatewayActionType.PROVIDER_CALL.value,
            provider=self.provider_name,
            operation=operation,
            model=deployment,
            declared_tools=declared_tool_names(body),
            parameters={
                "stream": stream,
                "deployment": deployment,
                "max_output_tokens": max_output_tokens(body),
            },
            input_metadata={
                "resource_mapping": {
                    "provider": self.provider_name,
                    "model": deployment,
                    "deployment": deployment,
                    "endpoint": strip_route_prefix(request.path, ROUTE_PREFIX),
                    "subscription": settings.azure_openai_endpoint,
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
        return bool(load_json(request.body).get("stream"))

    def new_stream_usage_collector(self):
        return OpenAIStreamUsageCollector()

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        if not response_body:
            return None
        model = response_body.get("model")
        usage = response_body.get("usage")
        if isinstance(usage, dict):
            return _usage_from_openai(
                usage, model=str(model) if model else None
            )
        return None