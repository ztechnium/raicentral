"""Model Armor read and optional write helpers."""

from __future__ import annotations

from typing import Any


def read_model_armor_state(*, project_id: str, location: str, credentials) -> dict[str, Any]:
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
