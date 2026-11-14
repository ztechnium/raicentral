"""Versioned destination classifier for observed egress (catalog + UNKNOWN_AI heuristics)."""

from __future__ import annotations

import re

from rai_gateway.network_enforcement.domain import ProviderDestinationClass
from rai_gateway.network_enforcement.provider_destinations import classify_hostname

PROVIDER_DESTINATION_CLASSIFIER_VERSION = "1.0.0"

_UNKNOWN_AI_HOST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|\.)openai\.", re.I),
    re.compile(r"(^|\.)anthropic\.", re.I),
    re.compile(r"bedrock-runtime\.", re.I),
    re.compile(r"(^|\.)aiplatform\.googleapis\.com$", re.I),
    re.compile(r"generativelanguage\.googleapis\.com$", re.I),
    re.compile(r"\.openai\.azure\.com$", re.I),
    re.compile(r"(^|\.)cohere\.(com|ai)", re.I),
    re.compile(r"(^|\.)mistral\.ai", re.I),
    re.compile(r"(^|\.)groq\.com", re.I),
    re.compile(r"(^|\.)together\.(xyz|ai)", re.I),
    re.compile(r"(^|\.)fireworks\.ai", re.I),
    re.compile(r"(^|\.)perplexity\.ai", re.I),
    re.compile(r"/v1/(chat/)?completions", re.I),
)


def classify_destination(hostname: str) -> ProviderDestinationClass | None:
    """Return a provider class for AI-related destinations, or None if not AI-like."""
    catalog = classify_hostname(hostname)
    if catalog is not None:
        try:
            return ProviderDestinationClass(catalog)
        except ValueError:
            return ProviderDestinationClass.UNKNOWN_AI

    normalized = hostname.strip().lower().rstrip(".")
    for pattern in _UNKNOWN_AI_HOST_PATTERNS:
        if pattern.search(normalized):
            return ProviderDestinationClass.UNKNOWN_AI
    return None