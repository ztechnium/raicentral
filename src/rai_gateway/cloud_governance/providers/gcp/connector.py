"""GCP connector implementation."""

from __future__ import annotations

from typing import Any

from rai_gateway.cloud_governance.domain import CloudProvider
from rai_gateway.cloud_governance.protocols import DiscoveredResource, VerifyResult
from rai_gateway.cloud_governance.providers.gcp.auth import load_credentials
from rai_gateway.cloud_governance.providers.gcp.deps import (
    GcpDependencyError,
    gcp_libraries_available,
)
from rai_gateway.cloud_governance.providers.gcp.discovery import search_vertex_assets
from rai_gateway.cloud_governance.providers.gcp.iam_diagnostics import (
    ENFORCE_PERMISSIONS,
    READ_PERMISSIONS,
    test_project_permissions,
)
from rai_gateway.cloud_governance.providers.gcp.model_armor import (
    apply_model_armor_changes,
    read_model_armor_state,
)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GcpConnector:
    provider_id = CloudProvider.GCP.value

    def _creds(self, auth_mode: str, auth_config: dict[str, Any]):
        return load_credentials(auth_mode, auth_config, scopes=_SCOPES)

    def verify(self, *, scope: str, auth_config: dict[str, Any]) -> VerifyResult:
        auth_mode = auth_config.get("_auth_mode", "")
        if not gcp_libraries_available():
            return VerifyResult(
                ok=False,
                capabilities={"discovery": False, "model_armor_read": False, "enforce": False},
                diagnostics=[
                    {
                        "code": "GCP_LIBS_MISSING",
                        "message": str(GcpDependencyError("missing")),
                    }
                ],
                health={"provider": self.provider_id, "scope": scope},
            )

        diagnostics: list[dict[str, Any]] = []
        try:
            creds = self._creds(auth_mode, auth_config)
        except Exception as exc:  # noqa: BLE001
            return VerifyResult(
                ok=False,
                capabilities={"discovery": False, "model_armor_read": False, "enforce": False},
                diagnostics=[{"code": "AUTH_FAILED", "message": str(exc)}],
                health={"provider": self.provider_id, "scope": scope},
            )

        project_id = scope.removeprefix("projects/") if scope.startswith("projects/") else scope
        read_diag = test_project_permissions(
            project_id=project_id, credentials=creds, permissions=READ_PERMISSIONS
        )
        diagnostics.append({"code": "IAM_READ", **read_diag})
        enforce_diag = test_project_permissions(
            project_id=project_id, credentials=creds, permissions=ENFORCE_PERMISSIONS
        )
        diagnostics.append({"code": "IAM_ENFORCE", **enforce_diag})

        can_discover = "cloudasset.assets.searchAllResources" in read_diag["granted"]
        can_read_ma = any(
            p in read_diag["granted"]
            for p in ("modelarmor.templates.get", "modelarmor.floorSettings.get")
        )
        can_enforce = not enforce_diag["missing"]

        return VerifyResult(
            ok=can_discover,
            capabilities={
                "discovery": can_discover,
                "model_armor_read": can_read_ma,
                "enforce": can_enforce,
            },
            diagnostics=diagnostics,
            health={"provider": self.provider_id, "scope": scope, "project_id": project_id},
        )

    def discover_assets(
        self,
        *,
        scope: str,
        auth_config: dict[str, Any],
        page_token: str | None = None,
    ) -> tuple[list[DiscoveredResource], str | None, list[str]]:
        auth_mode = auth_config.get("_auth_mode", "")
        project_id = scope.removeprefix("projects/") if scope.startswith("projects/") else scope
        creds = self._creds(auth_mode, auth_config)
        return search_vertex_assets(
            project_id=project_id,
            credentials=creds,
            page_token=page_token,
        )

    def inspect_model_armor(
        self, *, scope: str, auth_config: dict[str, Any]
    ) -> dict[str, Any]:
        auth_mode = auth_config.get("_auth_mode", "")
        project_id = scope.removeprefix("projects/") if scope.startswith("projects/") else scope
        location = auth_config.get("default_location", "us-central1")
        creds = self._creds(auth_mode, auth_config)
        return read_model_armor_state(
            project_id=project_id, location=location, credentials=creds
        )

    def preview_model_armor_change(
        self, *, scope: str, auth_config: dict[str, Any], desired: dict[str, Any]
    ) -> dict[str, Any]:
        current = self.inspect_model_armor(scope=scope, auth_config=auth_config)
        return {
            "dry_run": True,
            "current": current,
            "desired": desired,
            "diff_summary": {
                "templates_changed": desired.get("templates") != current.get("templates"),
                "floor_changed": desired.get("floor_settings") != current.get("floor_settings"),
            },
        }

    def apply_model_armor_change(
        self,
        *,
        scope: str,
        auth_config: dict[str, Any],
        desired: dict[str, Any],
        etag: str | None = None,
    ) -> dict[str, Any]:
        auth_mode = auth_config.get("_auth_mode", "")
        project_id = scope.removeprefix("projects/") if scope.startswith("projects/") else scope
        location = desired.get("location") or auth_config.get("default_location", "us-central1")
        creds = self._creds(auth_mode, auth_config)
        return apply_model_armor_changes(
            project_id=project_id,
            location=location,
            credentials=creds,
            desired=desired,
            etag=etag,
        )
