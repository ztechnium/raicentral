"""Step 7 — bypass detection, classification, dedup, and attestation."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from rai_gateway.network_enforcement.attestation import compute_runtime_attestation_status
from rai_gateway.network_enforcement.bypass_rules import evaluate_bypass
from rai_gateway.network_enforcement.dedup import observed_event_dedup_key
from rai_gateway.network_enforcement.deployment_modes import GatewayNetworkRolloutMode
from rai_gateway.network_enforcement.destination_classifier import classify_destination
from rai_gateway.network_enforcement.domain import (
    DeclaredObservedAuthorized,
    ObservedConnectionAction,
    ObservedConnectionPayload,
    ProviderDestinationClass,
    RuntimeAttestationStatus,
)


def _payload(**kwargs) -> ObservedConnectionPayload:
    defaults = {
        "observed_at": datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc),
        "destination_hostname": "api.openai.com",
        "gateway_used": False,
        "runtime_identity_id": uuid4(),
    }
    defaults.update(kwargs)
    return ObservedConnectionPayload(**defaults)


@pytest.mark.parametrize(
    ("hostname", "provider"),
    [
        ("api.openai.com", ProviderDestinationClass.OPENAI),
        ("api.anthropic.com", ProviderDestinationClass.ANTHROPIC),
        ("custom.llm.vendor.openai.example", ProviderDestinationClass.UNKNOWN_AI),
        ("example.com", None),
    ],
)
def test_classify_destination(hostname: str, provider: ProviderDestinationClass | None) -> None:
    assert classify_destination(hostname) == provider


def test_bypass_not_flagged_for_gateway_traffic() -> None:
    evaluation = evaluate_bypass(
        _payload(gateway_used=True),
        ProviderDestinationClass.OPENAI,
        authorized_providers=frozenset({"OPENAI"}),
    )
    assert not evaluation.is_bypass


def test_bypass_event_generated_for_direct_egress() -> None:
    evaluation = evaluate_bypass(
        _payload(gateway_used=False),
        ProviderDestinationClass.OPENAI,
        authorized_providers=frozenset({"OPENAI"}),
        rollout_mode=GatewayNetworkRolloutMode.WARN,
    )
    assert evaluation.is_bypass
    assert evaluation.severity == "MEDIUM"


def test_unknown_ai_endpoint_handling() -> None:
    provider = classify_destination("inference.shadow-ai.vendor.example")
    assert provider is None
    evaluation = evaluate_bypass(_payload(destination_hostname="example.com"), provider)
    assert not evaluation.is_bypass


def test_duplicated_network_events_deduped() -> None:
    first = _payload()
    second = ObservedConnectionPayload(
        observed_at=first.observed_at.replace(second=15),
        destination_hostname=first.destination_hostname,
        gateway_used=first.gateway_used,
        runtime_identity_id=first.runtime_identity_id,
    )
    assert observed_event_dedup_key(first) == observed_event_dedup_key(second)


def test_runtime_attestation_bypass_detected() -> None:
    exposure = DeclaredObservedAuthorized(
        declared=frozenset({"OPENAI"}),
        observed=frozenset({"OPENAI", "ANTHROPIC"}),
        authorized=frozenset({"OPENAI"}),
    )
    status = compute_runtime_attestation_status(
        exposure,
        bypass_detected=True,
        observed_gateway_only=False,
        rollout_mode=GatewayNetworkRolloutMode.OBSERVE,
    )
    assert status is RuntimeAttestationStatus.BYPASS_DETECTED


def test_runtime_attestation_gateway_only() -> None:
    exposure = DeclaredObservedAuthorized(
        declared=frozenset({"OPENAI"}),
        observed=frozenset(),
        authorized=frozenset({"OPENAI"}),
    )
    status = compute_runtime_attestation_status(
        exposure,
        bypass_detected=False,
        observed_gateway_only=True,
        rollout_mode=GatewayNetworkRolloutMode.WARN,
    )
    assert status is RuntimeAttestationStatus.GATEWAY_ONLY


def test_shadow_provider_unauthorized_observed() -> None:
    evaluation = evaluate_bypass(
        _payload(gateway_used=False),
        ProviderDestinationClass.ANTHROPIC,
        authorized_providers=frozenset({"OPENAI"}),
    )
    assert evaluation.is_bypass
    assert evaluation.shadow_provider


def test_blocked_action_lowers_severity() -> None:
    evaluation = evaluate_bypass(
        _payload(gateway_used=False, action=ObservedConnectionAction.BLOCKED),
        ProviderDestinationClass.OPENAI,
        authorized_providers=frozenset({"OPENAI"}),
        rollout_mode=GatewayNetworkRolloutMode.ENFORCE,
    )
    assert evaluation.is_bypass
    assert evaluation.severity == "LOW"