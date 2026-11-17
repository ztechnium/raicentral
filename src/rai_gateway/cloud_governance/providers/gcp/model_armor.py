"""Model Armor read and optional write helpers."""

from __future__ import annotations

from typing import Any


def read_model_armor_state(
    *, project_id: str, location: str, credentials
) -> dict[str, Any]:
    """Best-effort read of templates + floor settings for a location."""
    result: dict[str, Any] = {
        "project_id": project_id,
        "location": location,
        "supported": False,
        "templates": [],
        "floor_settings": None,
        "limitations": [],
    }
    try:
        from google.cloud import modelarmor_v1
    except ImportError:
        result["limitations"].append("google-cloud-modelarmor not installed")
        return result

    client = modelarmor_v1.ModelArmorClient(credentials=credentials)
    parent = f"projects/{project_id}/locations/{location}"
    templates: list[dict[str, Any]] = []
    try:
        for template in client.list_templates(parent=parent):
            templates.append(
                {
                    "name": template.name,
                    "display_name": template.display_name,
                    "filter_config": template.filter_config,
                    "etag": getattr(template, "etag", None),
                }
            )
        result["templates"] = templates
        result["supported"] = True
    except Exception as exc:  # noqa: BLE001
        result["limitations"].append(f"list_templates failed: {exc}")

    try:
        floor = client.get_floor_setting(name=f"{parent}/floorSetting")
        result["floor_settings"] = {
            "name": floor.name,
            "filter_config": floor.filter_config,
            "etag": getattr(floor, "etag", None),
        }
    except Exception as exc:  # noqa: BLE001
        result["limitations"].append(f"get_floor_setting failed: {exc}")

    return result


def apply_model_armor_changes(
    *,
    project_id: str,
    location: str,
    credentials,
    desired: dict[str, Any],
    etag: str | None = None,
) -> dict[str, Any]:
    """Apply floor setting updates when SDK and permissions are available."""
    result: dict[str, Any] = {
        "applied": False,
        "project_id": project_id,
        "location": location,
        "operations": [],
        "limitations": [],
    }
    try:
        from google.cloud import modelarmor_v1
    except ImportError:
        result["limitations"].append("google-cloud-modelarmor not installed")
        return result

    client = modelarmor_v1.ModelArmorClient(credentials=credentials)
    parent = f"projects/{project_id}/locations/{location}"
    floor = desired.get("floor_settings")
    if isinstance(floor, dict) and floor.get("filter_config") is not None:
        floor_setting = modelarmor_v1.FloorSetting(
            name=f"{parent}/floorSetting",
            filter_config=floor.get("filter_config"),
        )
        try:
            if etag and hasattr(client, "update_floor_setting"):
                updated = client.update_floor_setting(
                    floor_setting=floor_setting,
                    etag=etag,
                )
            else:
                updated = client.update_floor_setting(floor_setting=floor_setting)
            result["operations"].append(
                {
                    "type": "floor_setting",
                    "name": updated.name,
                    "etag": getattr(updated, "etag", None),
                }
            )
            result["applied"] = True
        except Exception as exc:  # noqa: BLE001
            result["limitations"].append(f"update_floor_setting failed: {exc}")
    else:
        result["limitations"].append(
            "desired.floor_settings.filter_config required for apply"
        )
    return result
