"""Network rollout modes for infrastructure enforcement (Observe → Warn → Enforce)."""

from enum import StrEnum

from rai_gateway.core.config import settings


class GatewayNetworkRolloutMode(StrEnum):
    """How strictly infrastructure blocks direct AI provider egress."""

    OBSERVE = "OBSERVE"
    WARN = "WARN"
    ENFORCE = "ENFORCE"


def gateway_network_rollout_mode(raw: str | None = None) -> GatewayNetworkRolloutMode:
    """Resolve rollout mode from env or explicit override; unknown values fail closed to ENFORCE."""
    value = (raw if raw is not None else settings.gateway_enforcement_mode).strip().upper()
    try:
        return GatewayNetworkRolloutMode(value)
    except ValueError:
        return GatewayNetworkRolloutMode.ENFORCE


def infrastructure_blocks_direct_provider_egress(
    mode: GatewayNetworkRolloutMode | None = None,
) -> bool:
    """True when network policy / firewall rules should deny workload → provider paths."""
    resolved = mode or gateway_network_rollout_mode()
    return resolved is GatewayNetworkRolloutMode.ENFORCE
