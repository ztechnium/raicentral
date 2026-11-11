"""Coverage evaluation (honest, evidence-based)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.cloud_governance.domain import AssetStatus
from rai_gateway.models.cloud_governance import CloudConnection, CloudDiscoveredAsset


def evaluate_connection_coverage(db: Session, connection: CloudConnection) -> dict[str, Any]:
    assets = (
        db.query(CloudDiscoveredAsset)
        .filter(CloudDiscoveredAsset.connection_id == connection.id)
        .all()
    )
    linked = [a for a in assets if a.status == AssetStatus.LINKED.value]
    candidates = [a for a in assets if a.status == AssetStatus.CANDIDATE.value]
    not_observed = [a for a in assets if a.status == AssetStatus.NOT_OBSERVED.value]

    caps = connection.capabilities or {}
    enforcement = bool(connection.enforcement_enabled)

    return {
        "methodology": "evidence_based_v1",
        "stages": {
            "discovered": len(assets),
            "linked_to_registry": len(linked),
            "candidates_pending_link": len(candidates),
            "not_observed": len(not_observed),
        },
        "controls": {
            "inventory_visibility": caps.get("discovery", False),
            "model_armor_inspection": caps.get("model_armor_read", False),
            "enforcement_ready": caps.get("enforce", False) and enforcement,
        },
        "limitations": [
            "Model Armor configuration does not imply all inference traffic is protected.",
            "Cloud Asset Inventory may omit runtime deployment associations.",
        ],
        "honesty_flags": {
            "traffic_routing_coverage": False,
            "prompt_content_ingest": False,
        },
    }
