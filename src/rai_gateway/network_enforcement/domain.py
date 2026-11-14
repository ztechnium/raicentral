"""Domain types for bypass detection and runtime attestation (Step 7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ProviderDestinationClass(StrEnum):
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"
    AZURE_OPENAI = "AZURE_OPENAI"
    GOOGLE_AI = "GOOGLE_AI"
    VERTEX = "VERTEX"
    BEDROCK = "BEDROCK"
    OLLAMA = "OLLAMA"
    UNKNOWN_AI = "UNKNOWN_AI"


class RuntimeAttestationStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    PARTIALLY_OBSERVED = "PARTIALLY_OBSERVED"
    GATEWAY_ONLY = "GATEWAY_ONLY"
    NETWORK_ENFORCED = "NETWORK_ENFORCED"
    BYPASS_DETECTED = "BYPASS_DETECTED"


class ObservedConnectionAction(StrEnum):
    ALLOWED = "ALLOWED"
    OBSERVED = "OBSERVED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class ObservedConnectionPayload(BaseModel):
    observed_at: datetime
    destination_hostname: str = Field(..., min_length=1)
    gateway_used: bool = False
    source_workload: str | None = None
    source_process: str | None = None
    destination_ip: str | None = None
    destination_port: int | None = Field(default=None, ge=1, le=65535)
    protocol: str | None = None
    cloud_resource_identity: str | None = None
    runtime_identity_id: UUID | None = None
    action: ObservedConnectionAction = ObservedConnectionAction.OBSERVED
    collector_source: str | None = None
    correlation: dict[str, Any] | None = None


class ObservedConnectionBatch(BaseModel):
    events: list[ObservedConnectionPayload] = Field(..., min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class DeclaredObservedAuthorized:
    declared: frozenset[str]
    observed: frozenset[str]
    authorized: frozenset[str]


@dataclass(frozen=True, slots=True)
class AssuranceMetrics:
    gateway_requests: int
    direct_ai_egress_attempts: int
    blocked_bypass_attempts: int
    unclassified_ai_destinations: int
    runtimes_without_network_enforcement: int