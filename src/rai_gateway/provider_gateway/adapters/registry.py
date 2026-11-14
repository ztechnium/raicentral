"""Default provider adapter registry."""

from __future__ import annotations

from rai_gateway.provider_gateway.adapters.anthropic import AnthropicAdapter
from rai_gateway.provider_gateway.adapters.azure_openai import AzureOpenAIAdapter
from rai_gateway.provider_gateway.adapters.bedrock import BedrockAdapter
from rai_gateway.provider_gateway.adapters.gemini import GeminiAdapter
from rai_gateway.provider_gateway.adapters.ollama import OllamaAdapter
from rai_gateway.provider_gateway.adapters.openai import OpenAIAdapter
from rai_gateway.provider_gateway.adapters.vertex import VertexAIAdapter
from rai_gateway.provider_gateway.proxy import AdapterRegistry


def default_adapter_registry() -> AdapterRegistry:
    return AdapterRegistry(
        [
            OpenAIAdapter(),
            AzureOpenAIAdapter(),
            AnthropicAdapter(),
            GeminiAdapter(),
            VertexAIAdapter(),
            BedrockAdapter(),
            OllamaAdapter(),
        ]
    )