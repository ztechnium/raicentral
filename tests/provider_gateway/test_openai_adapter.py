"""Unit tests for the OpenAI provider adapter."""

from __future__ import annotations

import json

from rai_gateway.provider_gateway.adapters.openai import (
    OpenAIAdapter,
    OpenAIStreamUsageCollector,
    _usage_from_openai,
)
from rai_gateway.provider_gateway.domain import ProxyRequest
from rai_gateway.runtime_enforcement.gateway_action import GatewayActionType

adapter = OpenAIAdapter()


def test_matches_openai_routes() -> None:
    assert adapter.matches_request(
        ProxyRequest("POST", "/providers/openai/v1/chat/completions", {}, b"{}")
    )
    assert not adapter.matches_request(ProxyRequest("POST", "/fake/chat", {}, b"{}"))


def test_extract_chat_completions_governance_metadata() -> None:
    body = {
        "model": "gpt-4o",
        "stream": True,
        "max_completion_tokens": 500,
        "response_format": {"type": "json_object"},
        "metadata": {"tenant": "a"},
        "tools": [{"type": "function", "function": {"name": "search"}}],
        "messages": [{"role": "user", "content": "secret"}],
    }
    req = ProxyRequest(
        "POST",
        "/providers/openai/v1/chat/completions",
        {},
        json.dumps(body).encode(),
    )
    action = adapter.extract_governance_action(req)
    assert action.action_type == GatewayActionType.PROVIDER_CALL.value
    assert action.provider == "openai"
    assert action.operation == "chat.completions"
    assert action.model == "gpt-4o"
    assert action.declared_tools == ["search"]
    assert action.parameters["stream"] is True
    assert action.parameters["max_output_tokens"] == 500
    assert action.input_metadata["response_format"] == {"type": "json_object"}
    assert "secret" not in json.dumps(action.__dict__, default=str)


def test_upstream_url_strips_gateway_prefix() -> None:
    from provider_gateway.fakes import StaticResolver

    cred = StaticResolver().resolve("openai")
    req = ProxyRequest("POST", "/providers/openai/v1/embeddings", {}, b"{}")
    assert (
        adapter.upstream_url(req, cred) == "https://upstream.test/v1/embeddings"
    )


def test_extract_usage_chat_shape() -> None:
    usage = _usage_from_openai(
        {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "prompt_tokens_details": {"cached_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 1},
        },
        model="gpt-4o",
    )
    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.cached_input_tokens == 2
    assert usage.reasoning_tokens == 1


def test_stream_collector_parses_sse_usage() -> None:
    collector = OpenAIStreamUsageCollector()
    chunk = (
        b'data: {"model":"gpt-4o","usage":{"prompt_tokens":3,"completion_tokens":2,'
        b'"total_tokens":5}}\n\ndata: [DONE]\n\n'
    )
    collector.feed(chunk)
    usage = collector.usage()
    assert usage is not None
    assert usage.total_tokens == 5
    assert usage.model == "gpt-4o"
