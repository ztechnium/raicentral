"""Versioned catalog of governed AI provider destinations for firewall/DNS controls."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROVIDER_DESTINATION_CATALOG_VERSION = "1.0.0"

_CATALOG_PATH = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "network-enforcement"
    / "catalog"
    / "provider-destinations.yaml"
)


@dataclass(frozen=True, slots=True)
class ProviderDestination:
    """One governed provider family and its destination host patterns."""

    provider: str
    hostnames: tuple[str, ...]
    hostname_suffixes: tuple[str, ...] = ()
    notes: str = ""


_BUILTIN_CATALOG: tuple[ProviderDestination, ...] = (
    ProviderDestination(
        provider="OPENAI",
        hostnames=("api.openai.com",),
        notes="OpenAI API; Azure hosts are classified separately.",
    ),
    ProviderDestination(
        provider="ANTHROPIC",
        hostnames=("api.anthropic.com",),
    ),
    ProviderDestination(
        provider="GOOGLE_AI",
        hostnames=("generativelanguage.googleapis.com",),
    ),
    ProviderDestination(
        provider="VERTEX",
        hostnames=("aiplatform.googleapis.com",),
        hostname_suffixes=(".prediction.vertexai.goog",),
    ),
    ProviderDestination(
        provider="AZURE_OPENAI",
        hostnames=(),
        hostname_suffixes=(".openai.azure.com",),
        notes="Prefer hostname suffix; deployment-specific resource names vary.",
    ),
    ProviderDestination(
        provider="BEDROCK",
        hostnames=(),
        hostname_suffixes=(
            ".bedrock-runtime.us-east-1.amazonaws.com",
            ".bedrock-runtime.us-west-2.amazonaws.com",
            ".bedrock-runtime.eu-west-1.amazonaws.com",
            ".bedrock-runtime.ap-southeast-1.amazonaws.com",
        ),
        notes="Regional Bedrock runtime endpoints; extend per account/region.",
    ),
    ProviderDestination(
        provider="OLLAMA",
        hostnames=("localhost", "127.0.0.1"),
        hostname_suffixes=(".svc.cluster.local",),
        notes="On-prem / in-cluster inference; tighten to your service DNS names.",
    ),
)


def _normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


@lru_cache(maxsize=1)
def load_provider_destination_catalog() -> tuple[ProviderDestination, ...]:
    """Return the built-in catalog (filesystem YAML is validated in tests for parity)."""
    return _BUILTIN_CATALOG


def classify_hostname(hostname: str) -> str | None:
    """Map a destination hostname to a provider id, or None if not a catalog match."""
    normalized = _normalize_hostname(hostname)
    for entry in load_provider_destination_catalog():
        if normalized in entry.hostnames:
            return entry.provider
        for suffix in entry.hostname_suffixes:
            needle = suffix if suffix.startswith(".") else f".{suffix}"
            if normalized.endswith(needle) or normalized == suffix.lstrip("."):
                return entry.provider
    return None


def all_blocked_host_patterns() -> frozenset[str]:
    """Hostnames and suffix markers useful for domain-aware firewall documentation."""
    hosts: set[str] = set()
    for entry in load_provider_destination_catalog():
        hosts.update(entry.hostnames)
        hosts.update(entry.hostname_suffixes)
    return frozenset(hosts)


def catalog_path() -> Path:
    """Path to the human-editable YAML catalog shipped with reference deployments."""
    return _CATALOG_PATH
