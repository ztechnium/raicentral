"""Google AI (Gemini) generateContent adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.adapters.common import (
    declared_tool_names,
    forward_safe_query,
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


_MAX_CAPTURE_CHARS = 64_000


def gemini_prompt_text(body: Mapping[str, Any]) -> str | None:
    """Readable prompt (system instruction + user/model turns) from a generateContent body."""
    lines: list[str] = []
    system = body.get("systemInstruction") or body.get("system_instruction")
    if isinstance(system, dict):
        text = _parts_text(system.get("parts"))
        if text:
            lines.append(f"[system] {text}")
    contents = body.get("contents")
    if isinstance(contents, dict):
        contents = [contents]
    for item in contents if isinstance(contents, list) else []:
        if not isinstance(item, dict):
            continue
        text = _parts_text(item.get("parts"))
        if text:
            lines.append(f"[{item.get('role') or 'user'}] {text}")
    joined = "\n".join(lines)
    return joined[:_MAX_CAPTURE_CHARS] or None


def _parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    return "".join(
        p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)
    )


def gemini_response_text(response: Mapping[str, Any] | None) -> str | None:
    if not response:
        return None
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    content = first.get("content") if isinstance(first, dict) else None
    text = _parts_text(content.get("parts")) if isinstance(content, dict) else ""
    return text or None


class GeminiStreamCollector:
    """SSE collector: last ``usageMetadata`` wins; response text is accumulated."""

    def __init__(self, provider: str | None = None, capture_text: bool = False) -> None:
        self._provider = provider
        self._capture = capture_text
        self._buffer = b""
        self._usage: UsageInfo | None = None
        self._text: list[str] = []
        self._text_len = 0

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload in {b"", b"[DONE]"}:
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            usage = _usage_from_gemini(event)
            if usage is not None:
                if self._provider:
                    usage.provider = self._provider
                self._usage = usage
            if self._capture and self._text_len < _MAX_CAPTURE_CHARS:
                piece = gemini_response_text(event)
                if piece:
                    self._text.append(piece)
                    self._text_len += len(piece)

    def usage(self) -> UsageInfo | None:
        return self._usage

    def text(self) -> str | None:
        return ("".join(self._text)[:_MAX_CAPTURE_CHARS]) or None


def gemini_stream_usage_collector(
    provider: str | None = None, capture_text: bool = False
) -> GeminiStreamCollector:
    return GeminiStreamCollector(provider, capture_text)


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
        # The client path already carries the API version (/v1beta/...). Avoid
        # doubling it when the configured base URL also ends with that version.
        version = base.rsplit("/", 1)[-1]
        if re.fullmatch(r"v\d+(?:beta\d*|alpha\d*)?", version) and suffix.startswith(
            f"/{version}/"
        ):
            base = base[: -len(version) - 1]
        return f"{base}{suffix}{forward_safe_query(request.query)}"

    def is_streaming(self, request: ProxyRequest) -> bool:
        if "alt=sse" in (request.query or "").lower():
            return True
        body = load_json(request.body)
        config = body.get("generationConfig")
        if isinstance(config, dict) and config.get("responseMimeType") == "text/event-stream":
            return True
        return ":streamGenerateContent" in request.path

    def new_stream_usage_collector(self):
        return gemini_stream_usage_collector(capture_text=True)

    def extract_prompt_text(self, request: ProxyRequest) -> str | None:
        return gemini_prompt_text(load_json(request.body))

    def extract_response_text(
        self, response_body: Mapping[str, Any] | None
    ) -> str | None:
        return gemini_response_text(response_body)

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        return _usage_from_gemini(response_body)