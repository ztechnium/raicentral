"""IAM permission diagnostics via testIamPermissions."""

from __future__ import annotations

from typing import Any

READ_PERMISSIONS = (
    "cloudasset.assets.searchAllResources",
    "modelarmor.templates.get",
    "modelarmor.floorSettings.get",
    "resourcemanager.projects.get",
)

ENFORCE_PERMISSIONS = (
    "modelarmor.templates.update",
    "modelarmor.floorSettings.update",
)


def test_project_permissions(
    *,
    project_id: str,
    credentials,
    permissions: tuple[str, ...],
) -> dict[str, Any]:
    from googleapiclient import discovery

    service = discovery.build(
        "cloudresourcemanager", "v1", credentials=credentials, cache_discovery=False
    )
    name = f"projects/{project_id}"
    body = {"permissions": list(permissions)}
    response = service.projects().testIamPermissions(resource=name, body=body).execute()
    granted = set(response.get("permissions") or [])
    return {
        "resource": name,
        "requested": list(permissions),
        "granted": sorted(granted),
        "missing": sorted(set(permissions) - granted),
    }
