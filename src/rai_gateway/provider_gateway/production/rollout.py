"""Rollout phases A–E for infrastructure gateway adoption."""

from __future__ import annotations

from enum import StrEnum

from rai_gateway.core.config import settings


class GatewayRolloutPhase(StrEnum):
    MIRROR = "MIRROR"
    PROXY_OPTIONAL = "PROXY_OPTIONAL"
    PROXY_PREFERRED = "PROXY_PREFERRED"
    NETWORK_ENFORCED = "NETWORK_ENFORCED"
    PRIVATE_PROVIDER_ACCESS = "PRIVATE_PROVIDER_ACCESS"


def resolve_rollout_phase(raw: str | None = None) -> GatewayRolloutPhase:
    value = (raw or settings.gateway_rollout_phase or "PROXY_OPTIONAL").upper()
    try:
        return GatewayRolloutPhase(value)
    except ValueError:
        return GatewayRolloutPhase.PROXY_OPTIONAL


def rollout_guidance(phase: GatewayRolloutPhase | None = None) -> dict[str, str]:
    p = phase or resolve_rollout_phase()
    mapping = {
        GatewayRolloutPhase.MIRROR: (
            "Mirror metadata only; direct provider calls continue; gateway does not block."
        ),
        GatewayRolloutPhase.PROXY_OPTIONAL: (
            "Selected workloads may use the gateway; direct provider path remains available."
        ),
        GatewayRolloutPhase.PROXY_PREFERRED: (
            "Gateway is the default integration path; bypass emits a warning signal."
        ),
        GatewayRolloutPhase.NETWORK_ENFORCED: (
            "Direct provider egress is denied at the network layer; gateway is mandatory."
        ),
        GatewayRolloutPhase.PRIVATE_PROVIDER_ACCESS: (
            "Providers are reached via private endpoints / PSC; no public provider URLs."
        ),
    }
    return {"phase": p.value, "summary": mapping[p]}
