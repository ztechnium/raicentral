"""Google Vertex AI adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from rai_gateway.core.config import settings
from rai_gateway.network_enforcement.provider_destinations import classify_hostname
from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.adapters.common import (
    declared_tool_names,
    forward_safe_query,
    load_json,
    strip_route_prefix,
)
from rai_gateway.provider_gateway.adapters.gemini import (
    _usage_from_gemini,
    gemini_prompt_text,
    gemini_response_text,
    gemini_stream_usage_collector,
)
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


_LOCATION_RE = re.compile(r"^(global|[a-z]+(?:-[a-z]+)*\d+)$")


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
                    "location": self._effective_location(client_location)
                    or governed_location,
                    "publisher": route.get("publisher"),
                    "endpoint": strip_route_prefix(request.path, ROUTE_PREFIX),
                },
                "governed_project": governed_project,
                "governed_location": governed_location,
                "stream": stream,
            },
        )

    @staticmethod
    def _effective_location(client_location: str | None) -> str:
        governed = settings.gcp_location
        if settings.gcp_pin_location or not client_location:
            return governed or client_location or "global"
        if _LOCATION_RE.match(client_location):
            return client_location
        return governed

    @staticmethod
    def _base_for_location(base: str, location: str) -> str:
        """Point the Vertex base URL at the location's host (custom hosts untouched)."""
        parsed = urlparse(base)
        if not parsed.hostname or classify_hostname(parsed.hostname) != "VERTEX":
            return base
        if not parsed.hostname.endswith("aiplatform.googleapis.com"):
            return base
        host = (
            "aiplatform.googleapis.com"
            if location == "global"
            else f"{location}-aiplatform.googleapis.com"
        )
        return parsed._replace(netloc=host).geturl().rstrip("/")

    def upstream_url(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> str:
        suffix = strip_route_prefix(request.path, ROUTE_PREFIX)
        base = credential.base_url.rstrip("/")
        match = _VERTEX_RE.search(suffix)
        if match:
            # Project is always pinned to the governed value (the client cannot redirect
            # spend to another project). Location is chosen by the client (models such
            # as gemini-3.1-flash-lite exist only in some regions) unless pinning is
            # enabled; the version prefix is dropped since the base URL ends in /v1.
            project = settings.gcp_project_id or match.group("project")
            location = self._effective_location(match.group("location"))
            base = self._base_for_location(base, location)
            suffix = (
                f"/projects/{project}/locations/{location}/publishers/"
                f"{match.group('publisher')}/models/{match.group('model')}"
                f"{suffix[match.end():]}"
            )
        return f"{base}{suffix}{forward_safe_query(request.query)}"

    def new_stream_usage_collector(self):
        return gemini_stream_usage_collector(self.provider_name, capture_text=True)

    def extract_prompt_text(self, request: ProxyRequest) -> str | None:
        return gemini_prompt_text(load_json(request.body))

    def extract_response_text(
        self, response_body: Mapping[str, Any] | None
    ) -> str | None:
        return gemini_response_text(response_body)

    def is_streaming(self, request: ProxyRequest) -> bool:
        return ":streamRawPredict" in request.path or ":streamGenerateContent" in request.path

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        usage = _usage_from_gemini(response_body)
        if usage is not None:
            usage.provider = self.provider_name
        return usage