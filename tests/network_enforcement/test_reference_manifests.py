"""Sanity checks for shipped Kubernetes reference manifests."""

from pathlib import Path

import pytest

K8S_DIR = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "network-enforcement"
    / "kubernetes"
)

REQUIRED_MANIFESTS = (
    "namespace.yaml",
    "network-policy-workload.yaml",
    "network-policy-gateway-egress.yaml",
    "pdb.yaml",
    "hpa.yaml",
    "values-network-enforcement.yaml",
)


@pytest.mark.parametrize("filename", REQUIRED_MANIFESTS)
def test_kubernetes_reference_files_exist(filename: str) -> None:
    path = K8S_DIR / filename
    assert path.is_file(), path
    text = path.read_text(encoding="utf-8")
    assert len(text.strip()) > 0


def test_workload_network_policy_denies_broad_egress() -> None:
    text = (K8S_DIR / "network-policy-workload.yaml").read_text(encoding="utf-8")
    assert "rai-workloads" in text
    assert "port: 8000" in text
    assert "policyTypes:" in text
    assert "Egress" in text
