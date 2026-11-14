"""Regression checks for GCP reference Terraform (no cloud apply required)."""

from __future__ import annotations

import re
from pathlib import Path

TF_DIR = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "network-enforcement"
    / "gcp"
    / "terraform"
)

_FIREWALL_DECL = re.compile(
    r'resource\s+"google_compute_firewall"\s+"([^"]+)"'
)


def test_gcp_firewall_resources_declared_once() -> None:
    names: list[str] = []
    for path in TF_DIR.glob("*.tf"):
        names.extend(_FIREWALL_DECL.findall(path.read_text()))
    assert names, "expected firewall resources in gcp/terraform"
    assert len(names) == len(set(names)), f"duplicate firewall resources: {names}"


def test_gcp_startup_bootstrap_script_present() -> None:
    script = TF_DIR.parent / "startup" / "gateway-startup.sh"
    assert script.is_file()
    text = script.read_text()
    assert "docker run" in text
    assert "/health" in text