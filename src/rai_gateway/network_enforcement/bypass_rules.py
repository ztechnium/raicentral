"""Bypass detection rules for observed AI egress."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from rai_gateway.governance.domain import IncidentSeverity
from rai_gateway.network_enforcement.deployment_modes import (
    GatewayNetworkRolloutMode,
    gateway_network_rollout_mode,
)
from rai_gateway.network_enforcement.domain import (
    ObservedConnectionAction,
    ObservedConnectionPayload,
    ProviderDestinationClass,
)


@dataclass(frozen=True, slots=True)
class BypassEvaluation:
    """Outcome of bypass rule evaluation."""

    is_bypass: bool
    severity: str | None = None
    reason: str | None = None
    shadow_provider: bool = False


def runtime_expected_governed(runtime_identity_id: UUID | None) -> bool:
    """A workload mapped to an active runtime identity is expected to use the gateway."""
    return runtime_identity_id is not None


def evaluate_bypass(
    payload: ObservedConnectionPayload,
    provider_guess: ProviderDestinationClass | None,
    *,
    authorized_providers: frozenset[str] | None = None,
    rollout_mode: GatewayNetworkRolloutMode | None = None,
) -> BypassEvaluation:
    """Detect shadow AI / gateway bypass for a single observed connection."""
    if provider_guess is None:
        return BypassEvaluation(is_bypass=False)

    if payload.gateway_used:
        return BypassEvaluation(is_bypass=False)

    if not runtime_expected_governed(payload.runtime_identity_id):
        return BypassEvaluation(
            is_bypass=False,
            reason="No governed runtime identity; observation only",
        )

    mode = rollout_mode or gateway_network_rollout_mode()
    severity = IncidentSeverity.MEDIUM.value
    if mode is GatewayNetworkRolloutMode.ENFORCE:
        severity = IncidentSeverity.HIGH.value
    elif mode is GatewayNetworkRolloutMode.WARN:
        severity = IncidentSeverity.MEDIUM.value
    else:
        severity = IncidentSeverity.LOW.value

    if payload.action is ObservedConnectionAction.BLOCKED:
        severity = IncidentSeverity.LOW.value

    shadow = False
    if authorized_providers is not None and provider_guess.value not in authorized_providers:
        shadow = True
        if severity == IncidentSeverity.LOW.value:
            severity = IncidentSeverity.MEDIUM.value

    return BypassEvaluation(
        is_bypass=True,
        severity=severity,
        reason="Direct AI provider egress outside gateway",
        shadow_provider=shadow,
    )