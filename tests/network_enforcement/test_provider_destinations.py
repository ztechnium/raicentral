"""Step 6 — provider destination catalog and hostname classification."""

from pathlib import Path

import pytest

from rai_gateway.network_enforcement import (
    classify_hostname,
    load_provider_destination_catalog,
)
from rai_gateway.network_enforcement.deployment_modes import (
    GatewayNetworkRolloutMode,
    gateway_network_rollout_mode,
    infrastructure_blocks_direct_provider_egress,
)
from rai_gateway.network_enforcement.provider_destinations import (
    PROVIDER_DESTINATION_CATALOG_VERSION,
    catalog_path,
)

_CATALOG = Path(__file__).resolve().parents[2] / "deploy" / "network-enforcement" / "catalog" / "provider-destinations.yaml"


@pytest.mark.parametrize(
    ("hostname", "provider"),
    [
        ("api.openai.com", "OPENAI"),
        ("api.anthropic.com", "ANTHROPIC"),
        ("generativelanguage.googleapis.com", "GOOGLE_AI"),
        ("aiplatform.googleapis.com", "VERTEX"),
        ("myaccount.openai.azure.com", "AZURE_OPENAI"),
        ("bedrock-runtime.us-east-1.amazonaws.com", "BEDROCK"),
    ],
)
def test_classify_hostname_catalog(hostname: str, provider: str) -> None:
    assert classify_hostname(hostname) == provider


def test_classify_hostname_unknown() -> None:
    assert classify_hostname("example.com") is None


def test_catalog_yaml_parity_with_builtin() -> None:
    text = _CATALOG.read_text(encoding="utf-8")
    assert f'version: "{PROVIDER_DESTINATION_CATALOG_VERSION}"' in text
    for entry in load_provider_destination_catalog():
        for host in entry.hostnames:
            assert host in text, f"missing hostname {host} in catalog yaml"
        for suffix in entry.hostname_suffixes:
            assert suffix in text, f"missing suffix {suffix} in catalog yaml"


def test_catalog_path_points_at_shipped_yaml() -> None:
    assert catalog_path().resolve() == _CATALOG.resolve()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("observe", GatewayNetworkRolloutMode.OBSERVE),
        ("WARN", GatewayNetworkRolloutMode.WARN),
        ("enforce", GatewayNetworkRolloutMode.ENFORCE),
        ("invalid", GatewayNetworkRolloutMode.ENFORCE),
    ],
)
def test_gateway_network_rollout_mode(raw: str, expected: GatewayNetworkRolloutMode) -> None:
    assert gateway_network_rollout_mode(raw) is expected


def test_infrastructure_blocks_direct_provider_egress_only_in_enforce() -> None:
    assert not infrastructure_blocks_direct_provider_egress(GatewayNetworkRolloutMode.OBSERVE)
    assert not infrastructure_blocks_direct_provider_egress(GatewayNetworkRolloutMode.WARN)
    assert infrastructure_blocks_direct_provider_egress(GatewayNetworkRolloutMode.ENFORCE)
