"""Compute runtime network attestation (DECLARED / OBSERVED / AUTHORIZED)."""

from __future__ import annotations

from rai_gateway.network_enforcement.deployment_modes import GatewayNetworkRolloutMode
from rai_gateway.network_enforcement.domain import (
    DeclaredObservedAuthorized,
    RuntimeAttestationStatus,
)


def compute_runtime_attestation_status(
    exposure: DeclaredObservedAuthorized,
    *,
    bypass_detected: bool,
    observed_gateway_only: bool,
    rollout_mode: GatewayNetworkRolloutMode | None = None,
) -> RuntimeAttestationStatus:
    del rollout_mode
    if bypass_detected:
        return RuntimeAttestationStatus.BYPASS_DETECTED
    if observed_gateway_only:
        return RuntimeAttestationStatus.GATEWAY_ONLY
    if exposure.observed and not exposure.observed - exposure.authorized:
        if exposure.observed == exposure.authorized:
            return RuntimeAttestationStatus.NETWORK_ENFORCED
        return RuntimeAttestationStatus.PARTIALLY_OBSERVED
    if exposure.observed - exposure.authorized:
        return RuntimeAttestationStatus.BYPASS_DETECTED
    if exposure.observed or exposure.declared:
        return RuntimeAttestationStatus.PARTIALLY_OBSERVED
    return RuntimeAttestationStatus.UNKNOWN