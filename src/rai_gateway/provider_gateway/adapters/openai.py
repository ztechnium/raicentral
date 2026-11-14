"""OpenAI-compatible provider protocol adapter (network gateway step 3)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from rai_gateway.provider_gateway.adapters.base import (
    ProviderProtocolAdapter,
    StreamUsageCollector,
)
from rai_gateway.provider_gateway.domain import (
    ProviderCredential,
    ProxyRequest,
    UsageInfo,
)
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayActionType,
)

ROUTE_PREFIX = "/providers/openai/v1"

_OPERATION_BY_SUFFIX: dict[str, str] = {
    "chat/completions": "chat.completions",
    "responses": "responses",
    "embeddings": "embeddings",
    "models": "models.list",
}


def _strip_gateway_prefix(path: str) -> str:
    if not path.startswith(ROUTE_PREFIX):
        return path
    suffix = path[len(ROUTE_PREFIX) :]
    if not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return suffix or "/"


def _load_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _operation_for_path(path: str) -> str:
    suffix = _strip_gateway_prefix(path).lstrip("/")
    if suffix in _OPERATION_BY_SUFFIX:
        return _OPERATION_BY_SUFFIX[suffix]
    if suffix.startswith("models/"):
        return "models.get"
    return suffix.split("/")[0] if suffix else "unknown"


def _model_from_request(path: str, body: dict[str, Any]) -> str | None:
    model = body.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    match = re.match(r"models/([^/]+)$", _strip_gateway_prefix(path).lstrip("/"))
    if match:
        return match.group(1)
    return None


def _declared_tools(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for entry in body.get("tools") or []:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
        elif entry.get("name"):
            names.append(str(entry["name"]))
    for entry in body.get("functions") or []:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return names


def _max_output_tokens(body: dict[str, Any]) -> int | None:
    for key in ("max_completion_tokens", "max_tokens", "max_output_tokens"):
        value = body.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _usage_from_openai(
    usage: Mapping[str, Any] | None, *, model: str | None = None
) -> UsageInfo | None:
    if not usage:
        return None
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    total = usage.get("total_tokens")
    cached = None
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        cached = prompt_details.get("cached_tokens")
    input_details = usage.get("input_tokens_details")
    if cached is None and isinstance(input_details, dict):
        cached = input_details.get("cached_tokens")
    reasoning = None
    completion_details = usage.get("completion_tokens_details")
    if isinstance(completion_details, dict):
        reasoning = completion_details.get("reasoning_tokens")
    output_details = usage.get("output_tokens_details")
    if reasoning is None and isinstance(output_details, dict):
        reasoning = output_details.get("reasoning_tokens")
    if not any(v is not None for v in (prompt, completion, total, cached, reasoning)):
        return None
    return UsageInfo(
        provider="openai",
        model=model,
        input_tokens=prompt if isinstance(prompt, int) else None,
        output_tokens=completion if isinstance(completion, int) else None,
        total_tokens=total if isinstance(total, int) else None,
        cached_input_tokens=cached if isinstance(cached, int) else None,
        reasoning_tokens=reasoning if isinstance(reasoning, int) else None,
    )


class OpenAIStreamUsageCollector(StreamUsageCollector):
    def __init__(self) -> None:
        self._usage: UsageInfo | None = None
        self._model: str | None = None
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("model"):
                self._model = str(event["model"])
            usage = event.get("usage")
            if isinstance(usage, dict):
                parsed = _usage_from_openai(usage, model=self._model)
                if parsed is not None:
                    self._usage = parsed

    def usage(self) -> UsageInfo | None:
        return self._usage


class OpenAIAdapter(ProviderProtocolAdapter):
    provider_name = "openai"
    passthrough_headers = (
        "openai-beta",
        "openai-organization",
        "openai-project",
        "x-stainless-arch",
        "x-stainless-lang",
        "x-stainless-os",
        "x-stainless-package-version",
        "x-stainless-runtime",
        "x-stainless-runtime-version",
    )

    def matches_request(self, request: ProxyRequest) -> bool:
        return request.path.startswith(ROUTE_PREFIX)

    def extract_governance_action(self, request: ProxyRequest) -> GatewayAction:
        body = _load_json(request.body)
        operation = _operation_for_path(request.path)
        model = _model_from_request(request.path, body)
        response_format = body.get("response_format")
        metadata = body.get("metadata")
        stream = self.is_streaming(request)
        return GatewayAction(
            action_type=GatewayActionType.PROVIDER_CALL.value,
            provider=self.provider_name,
            operation=operation,
            model=model,
            declared_tools=_declared_tools(body),
            parameters={
                "stream": stream,
                "max_output_tokens": _max_output_tokens(body),
            },
            input_metadata={
                "response_format": response_format,
                "request_metadata_keys": sorted(metadata.keys())
                if isinstance(metadata, dict)
                else [],
                "tool_count": len(_declared_tools(body)),
                "stream": stream,
            },
        )

    def upstream_url(
        self, request: ProxyRequest, credential: ProviderCredential
    ) -> str:
        suffix = _strip_gateway_prefix(request.path)
        base = credential.base_url.rstrip("/")
        if suffix == "/":
            return base
        return f"{base}{suffix}"

    def is_streaming(self, request: ProxyRequest) -> bool:
        body = _load_json(request.body)
        return bool(body.get("stream"))

    def new_stream_usage_collector(self) -> StreamUsageCollector:
        return OpenAIStreamUsageCollector()

    def extract_usage(
        self, response_body: Mapping[str, Any] | None
    ) -> UsageInfo | None:
        if not response_body:
            return None
        model = response_body.get("model")
        model_s = str(model) if model else None
        usage = response_body.get("usage")
        if isinstance(usage, dict):
            return _usage_from_openai(usage, model=model_s)
        # Responses API may nest usage under response.usage
        nested = response_body.get("response")
        if isinstance(nested, dict):
            nested_usage = nested.get("usage")
            if isinstance(nested_usage, dict):
                return _usage_from_openai(nested_usage, model=model_s)
        return None
