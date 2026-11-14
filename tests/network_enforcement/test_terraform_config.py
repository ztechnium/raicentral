"""Terraform skeleton validation for reference deployments."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NE = REPO_ROOT / "deploy" / "network-enforcement"


def _terraform_resource_names(*paths: Path) -> list[str]:
    pattern = re.compile(
        r'^resource\s+"(?P<type>[^"]+)"\s+"(?P<name>[^"]+)"', re.MULTILINE
    )
    names: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        names.extend(f'{m.group("type")}.{m.group("name")}' for m in pattern.finditer(text))
    return names


def test_gcp_terraform_has_no_duplicate_resource_blocks() -> None:
    tf_dir = NE / "gcp" / "terraform"
    names = _terraform_resource_names(*(tf_dir.glob("*.tf")))
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert duplicates == [], f"duplicate terraform resources: {duplicates}"


@pytest.mark.parametrize(
    "rel",
    [
        "gcp/terraform",
        "azure/terraform",
    ],
)
def test_terraform_validate(rel: str) -> None:
    terraform = shutil.which("terraform")
    if terraform is None:
        pytest.skip("terraform not installed")
    tf_dir = NE / rel
    init = subprocess.run(
        [terraform, "init", "-backend=false", "-input=false"],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr or init.stdout
    validate = subprocess.run(
        [terraform, "validate"],
        cwd=tf_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert validate.returncode == 0, validate.stderr or validate.stdout