"""Ollama / OpenAI-compatible local provider adapter."""

from __future__ import annotations

from collections.abc import Mapping

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

ROUTE_PREFIX = "/providers/ollama/v1"


class OllamaAdapter(ProviderProtocolAdapter):
    provider_name = "ollama"

    def matches_request(self, request: ProxyRequest) -> bool:
        if not request.path.startswith(ROUTE_PREFIX):
            return False
        return settings.provider_gateway_ollama_enabled

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        body = load_json(request.body)
        model = body.get("model")
        model_s = str(model).strip() if isinstance(model, str) else None
        rel = strip_route_prefix(request.path, ROUTE_PREFIX).lstrip("/")
        operation = rel.split("/")[0] if rel else "unknown"
        stream = self.is_streaming(request)
        return GatewayAction(
            action_type=GatewayActionType.PROVIDER_CALL.value,
            provider=self.provider_name,
            operation=operation,
            model=model_s,
            declared_tools=declared_tool_names(body),
            parameters={"stream": stream, "max_output_tokens": max_output_tokens(body)},
            input_metadata={
                "resource_mapping": {
                    "provider": self.provider_name,
                    "model": model_s,
                    "endpoint": settings.ollama_endpoint,
                    "allowlisted": True,
                },
                "local_provider": True,
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
        self, response_body: Mapping[str, object] | None
    ) -> UsageInfo | None:
        if not response_body:
            return None
        usage = response_body.get("usage") or response_body.get("eval_count")
        if isinstance(usage, dict):
            return _usage_from_openai(usage)
        prompt = response_body.get("prompt_eval_count")
        completion = response_body.get("eval_count")
        if isinstance(prompt, int) or isinstance(completion, int):
            return _usage_from_openai(
                {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": (prompt or 0) + (completion or 0),
                }
            )
        return None