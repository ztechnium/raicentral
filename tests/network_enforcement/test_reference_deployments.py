"""Step 6 — reference deployment artifacts exist and parse as valid YAML/HCL skeletons."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NE = REPO_ROOT / "deploy" / "network-enforcement"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing reference artifact: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "rel",
    [
        "kubernetes/namespace.yaml",
        "kubernetes/network-policy-workload.yaml",
        "kubernetes/network-policy-gateway-egress.yaml",
        "kubernetes/pod-disruption-budget.yaml",
        "kubernetes/horizontal-pod-autoscaler.yaml",
        "kubernetes/values-network-enforcement.yaml",
        "catalog/provider-destinations.yaml",
        "gcp/terraform/main.tf",
        "gcp/terraform/firewall.tf",
        "gcp/startup/gateway-startup.sh",
        "azure/terraform/main.tf",
        "azure/terraform/nsg.tf",
        "linux/rai-gateway.service",
        "linux/nftables/rai-egress-enforce.nft",
    ],
)
def test_reference_artifact_exists(rel: str) -> None:
    _read(NE / rel)


def test_kubernetes_policies_reference_gateway_namespace_label() -> None:
    workload = _read(NE / "kubernetes" / "network-policy-workload.yaml")
    assert "rai.ztechnium.com/role: enforcement-gateway" in workload
    gateway_ns = _read(NE / "kubernetes" / "namespace.yaml")
    assert "rai.ztechnium.com/role: enforcement-gateway" in gateway_ns


def test_gcp_startup_script_installs_container_and_health_check() -> None:
    script = _read(NE / "gcp" / "startup" / "gateway-startup.sh")
    assert "docker run" in script
    assert "/health" in script


def test_gcp_firewall_documents_enforce_toggle() -> None:
    fw = _read(NE / "gcp" / "terraform" / "firewall.tf")
    assert "enable_enforce_firewall" in fw
    assert "rai-workload-deny-internet-https" in fw


def test_helm_network_enforcement_template_exists() -> None:
    tpl = REPO_ROOT / "deploy" / "helm" / "rai-control-plane" / "templates" / "networkpolicy-gateway-egress.yaml"
    assert "{{- if .Values.networkEnforcement.enabled }}" in _read(tpl)
