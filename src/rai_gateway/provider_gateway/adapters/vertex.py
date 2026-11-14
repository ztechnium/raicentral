"""Google Vertex AI adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.adapters.common import (
    declared_tool_names,
    load_json,
    strip_route_prefix,
)
from rai_gateway.provider_gateway.adapters.gemini import _usage_from_gemini
from rai_gateway.provider_gateway.domain import ProviderCredential, ProxyRequest, UsageInfo
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayActionType,
)

ROUTE_PREFIX = "/providers/vertex-ai"
_VERTEX_RE = re.compile(
    r"/projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/"
    r"publishers/(?P<publisher>[^/]+)/models/(?P<model>[^/:]+)"
)


class VertexAIAdapter(ProviderProtocolAdapter):
    provider_name = "vertex"
    passthrough_headers = ("x-goog-user-project",)

    def _parse_route(self, path: str) -> dict[str, str | None]:
        rel = strip_route_prefix(path, ROUTE_PREFIX)
        match = _VERTEX_RE.search(rel)
        if not match:
            return {
                "project": None,
                "location": None,
                "publisher": None,
                "model": None,
            }
        return {k: match.group(k) for k in match.groupdict()}

    def matches_request(self, request: ProxyRequest) -> bool:
        return request.path.startswith(ROUTE_PREFIX)

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        body = load_json(request.body)
        route = self._parse_route(request.path)
        client_project = route.get("project")
        client_location = route.get("location")
        model = route.get("model")
        governed_project = settings.gcp_project_id
        governed_location = settings.gcp_location
        stream = self.is_streaming(request)
        return GatewayAction(
            action_type=GatewayActionType.PROVIDER_CALL.value,
            provider=self.provider_name,
            operation="generateContent",
            model=model,
            declared_tools=declared_tool_names(body),
            parameters={
                "stream": stream,
                "client_declared_project": client_project,
                "client_declared_location": client_location,
            },
            input_metadata={
                "resource_mapping": {
                    "provider": self.provider_name,
                    "model": model,
                    "gcp_project": governed_project or client_project,
                    "location": governed_location or client_location,
                    "publisher": route.get("publisher"),
                    "endpoint": strip_route_prefix(request.path, ROUTE_PREFIX),
                },
                "governed_project": governed_project,
                "governed_location": governed_location,
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
        return ":streamRawPredict" in request.path or ":streamGenerateContent" in request.path

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        usage = _usage_from_gemini(response_body)
        if usage is not None:
            usage.provider = self.provider_name
        return usage