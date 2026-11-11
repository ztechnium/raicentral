"""Customer onboarding artifact generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_terraform(connection_id: str, *, project_id: str) -> str:
    template_path = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "gcp-connector"
        / "main.tf"
    )
    if template_path.is_file():
        return template_path.read_text(encoding="utf-8").replace(
            "PROJECT_ID_PLACEHOLDER", project_id
        )

    return f"""# RAICentral GCP connector — connection {connection_id}
# Placeholder Terraform; see docs/integrations/gcp/terraform.md for full module.

terraform {{
  required_version = ">= 1.5.0"
}}

variable "project_id" {{
  type        = string
  description = "Customer GCP project id"
  default     = "{project_id}"
}}

resource "google_service_account" "raicentral_discover" {{
  project      = var.project_id
  account_id   = "raicentral-discover"
  display_name = "RAICentral discovery (read-only)"
}}

resource "google_service_account" "raicentral_enforce" {{
  project      = var.project_id
  account_id   = "raicentral-enforce"
  display_name = "RAICentral enforcement (optional)"
}}
"""


def cloud_shell_instructions(*, project_id: str, connection_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "connection_id": connection_id,
        "steps": [
            "Open Google Cloud Shell in the target project.",
            "Enable Cloud Asset API and Model Armor API if not already enabled.",
            "Create dedicated service accounts raicentral-discover and raicentral-enforce.",
            "Grant least-privilege roles documented in docs/integrations/gcp/permissions.md.",
            "Configure Workload Identity Federation for RAICentral host (no JSON keys).",
            "Paste the external account JSON into the RAICentral GCP wizard verify step.",
        ],
        "docs": "/docs/integrations/gcp/setup.md",
    }
