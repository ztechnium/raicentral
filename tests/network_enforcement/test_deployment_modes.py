"""Tests for network enforcement rollout modes."""

from rai_gateway.network_enforcement.deployment_modes import (
    GatewayNetworkRolloutMode,
    gateway_network_rollout_mode,
    infrastructure_blocks_direct_provider_egress,
)


def test_unknown_mode_fails_closed_to_enforce() -> None:
    assert gateway_network_rollout_mode("maybe") is GatewayNetworkRolloutMode.ENFORCE


def test_enforce_blocks_direct_egress() -> None:
    assert infrastructure_blocks_direct_provider_egress(GatewayNetworkRolloutMode.ENFORCE)


def test_observe_does_not_block_direct_egress() -> None:
    assert not infrastructure_blocks_direct_provider_egress(GatewayNetworkRolloutMode.OBSERVE)
