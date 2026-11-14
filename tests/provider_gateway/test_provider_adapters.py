"""Step 4: provider adapter metadata and metering."""

from __future__ import annotations

import json

import pytest

from rai_gateway.provider_gateway.adapters.anthropic import AnthropicAdapter
from rai_gateway.provider_gateway.adapters.azure_openai import AzureOpenAIAdapter
from rai_gateway.provider_gateway.adapters.bedrock import BedrockAdapter
from rai_gateway.provider_gateway.adapters.gemini import GeminiAdapter
from rai_gateway.provider_gateway.adapters.ollama import OllamaAdapter
from rai_gateway.provider_gateway.adapters.openai import OpenAIAdapter
from rai_gateway.provider_gateway.adapters.registry import default_adapter_registry
from rai_gateway.provider_gateway.adapters.vertex import VertexAIAdapter
from rai_gateway.provider_gateway.domain import UsageConfidence
from rai_gateway.provider_gateway.proxy import AdapterRegistry
from rai_gateway.provider_gateway.domain import ProxyRequest


def _post(path: str, body: dict) -> ProxyRequest:
    return ProxyRequest(
        method="POST",
        path=path,
        headers={"content-type": "application/json"},
        body=json.dumps(body).encode(),
    )


def test_registry_includes_all_major_providers() -> None:
    names = {a.provider_name for a in default_adapter_registry()._adapters}
    assert names == {
        "openai",
        "azure_openai",
        "anthropic",
        "google_ai",
        "vertex",
        "bedrock",
        "ollama",
    }


def test_openai_extracts_model_tools_and_stream() -> None:
    adapter = OpenAIAdapter()
    req = _post(
        "/providers/openai/v1/chat/completions",
        {
            "model": "gpt-4o",
            "stream": True,
            "tools": [{"type": "function", "function": {"name": "search"}}],
            "max_completion_tokens": 512,
        },
    )
    action = adapter.extract_governance_action(req)
    assert action.model == "gpt-4o"
    assert action.operation == "chat.completions"
    assert action.declared_tools == ["search"]
    assert action.parameters["stream"] is True
    usage = adapter.extract_usage(
        {
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12},
        }
    )
    assert usage is not None
    assert usage.confidence == UsageConfidence.PROVIDER_REPORTED
    assert usage.input_tokens == 4


def test_azure_maps_deployment_to_model() -> None:
    adapter = AzureOpenAIAdapter()
    req = _post(
        "/providers/azure-openai/openai/deployments/my-dep/chat/completions",
        {"messages": []},
    )
    action = adapter.extract_governance_action(req)
    assert action.model == "my-dep"
    assert action.operation == "chat.completions"
    assert action.input_metadata["resource_mapping"]["deployment"] == "my-dep"


def test_anthropic_usage_with_cache_fields() -> None:
    adapter = AnthropicAdapter()
    usage = adapter.extract_usage(
        {
            "model": "claude-3-5-sonnet",
            "usage": {
                "input_tokens": 90,
                "output_tokens": 10,
                "cache_read_input_tokens": 5,
            },
        }
    )
    assert usage is not None
    assert usage.input_tokens == 90
    assert usage.cached_input_tokens == 5


def test_gemini_usage_from_usage_metadata() -> None:
    adapter = GeminiAdapter()
    usage = adapter.extract_usage(
        {"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 7}}
    )
    assert usage is not None
    assert usage.total_tokens is None
    assert usage.input_tokens == 3
    assert usage.output_tokens == 7


def test_vertex_governance_prefers_configured_project() -> None:
    adapter = VertexAIAdapter()
    req = _post(
        "/providers/vertex-ai/projects/client-proj/locations/europe-west1/"
        "publishers/google/models/gemini-pro:generateContent",
        {},
    )
    action = adapter.extract_governance_action(req)
    mapping = action.input_metadata["resource_mapping"]
    assert mapping["gcp_project"] == "client-proj" or mapping["gcp_project"]
    assert action.parameters["client_declared_project"] == "client-proj"


def test_bedrock_extracts_region_and_profile() -> None:
    adapter = BedrockAdapter()
    req = _post(
        "/providers/bedrock/model/us.anthropic.claude/converse",
        {
            "modelId": "us.anthropic.claude",
            "inferenceProfileArn": "arn:aws:bedrock:profile/test",
        },
    )
    action = adapter.extract_governance_action(req)
    assert action.model == "us.anthropic.claude"
    assert action.parameters["inference_profile"] == "arn:aws:bedrock:profile/test"


@pytest.mark.parametrize(
    "path,adapter_type",
    [
        ("/providers/openai/v1/embeddings", OpenAIAdapter),
        ("/providers/anthropic/v1/messages", AnthropicAdapter),
        ("/providers/google-ai/v1beta/models/gemini:generateContent", GeminiAdapter),
    ],
)
def test_adapters_match_native_paths(path: str, adapter_type: type) -> None:
    registry = AdapterRegistry([adapter_type()])
    assert registry.resolve(_post(path, {})) is not None


def test_ollama_disabled_by_default() -> None:
    adapter = OllamaAdapter()
    req = _post("/providers/ollama/v1/chat/completions", {"model": "llama3"})
    assert adapter.matches_request(req) is False