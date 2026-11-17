"""Configuration drift detection for cloud connections."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.cloud_governance.domain import PolicyChangeStatus
from rai_gateway.cloud_governance.service import CloudGovernanceService
from rai_gateway.models.cloud_governance import (
    CloudConnection,
    CloudDriftFinding,
    CloudPolicyChangeRequest,
)


def run_drift_check(db: Session, connection_id: UUID) -> dict[str, Any]:
    conn = db.query(CloudConnection).filter(CloudConnection.id == connection_id).first()
    if conn is None:
        raise ValueError("connection not found")

    last_applied = (
        db.query(CloudPolicyChangeRequest)
        .filter(
            CloudPolicyChangeRequest.connection_id == connection_id,
            CloudPolicyChangeRequest.status.in_(
                [
                    PolicyChangeStatus.APPLIED.value,
                    PolicyChangeStatus.VERIFIED.value,
                ]
            ),
        )
        .order_by(CloudPolicyChangeRequest.updated_at.desc())
        .first()
    )

    findings_created = 0
    observed: dict[str, Any] = CloudGovernanceService(db).inspect_model_armor(
        connection_id
    )
    expected = (last_applied.desired_payload if last_applied else None) or {
        "note": "no_applied_baseline"
    }

    drift = _diff_model_armor(expected, observed)
    if drift:
        row = CloudDriftFinding(
            connection_id=connection_id,
            resource_name=conn.scope_identifier,
            finding_type="model_armor_config_drift",
            severity="medium",
            expected=expected,
            observed=observed,
            status="open",
        )
        db.add(row)
        db.flush()
        findings_created = 1

    return {
        "connection_id": str(connection_id),
        "findings_created": findings_created,
        "drift_detected": findings_created > 0,
    }


def _diff_model_armor(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    if expected.get("note") == "no_applied_baseline":
        return False
    exp_templates = expected.get("templates")
    obs_templates = observed.get("templates")
    if exp_templates is not None and exp_templates != obs_templates:
        return True
    exp_floor = expected.get("floor_settings")
    obs_floor = observed.get("floor_settings")
    if exp_floor is not None and exp_floor != obs_floor:
        return True
    return False
