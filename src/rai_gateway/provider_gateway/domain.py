"""Transport-level types for the provider proxy core."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class UsageConfidence(StrEnum):
    UNKNOWN = "unknown"
    ESTIMATED = "estimated"
    PROVIDER_REPORTED = "provider_reported"


@dataclass
class ProxyRequest:
    """A provider-native inbound request, body kept byte-for-byte intact."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes = b""
    query: str = ""
    client_host: str | None = None

    def body_sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


@dataclass(frozen=True)
class ProviderCredential:
    """Server-side upstream credential material. Never sourced from clients."""

    provider: str
    base_url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    query_params: Mapping[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # never leak secrets into logs
        return f"ProviderCredential(provider={self.provider!r}, base_url={self.base_url!r})"


@dataclass
class ProxyResponse:
    """Provider-native response ready for the HTTP layer."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    stream: AsyncIterator[bytes] | None = None
    # Set for gateway-generated (non-upstream) responses.
    gateway_generated: bool = False
    receipt_id: str | None = None


@dataclass
class UsageInfo:
    """Normalized provider metering (ProviderUsage contract)."""

    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    confidence: UsageConfidence | str = UsageConfidence.UNKNOWN
    provider_reported: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if k == "provider_reported" and not v:
                continue
            if v is None:
                continue
            out[k] = v.value if isinstance(v, UsageConfidence) else v
        return out

    @property
    def empty(self) -> bool:
        return not any(
            v is not None
            for k, v in self.__dict__.items()
            if k
            not in {
                "provider",
                "model",
                "confidence",
                "provider_reported",
            }
        )


ProviderUsage = UsageInfo
