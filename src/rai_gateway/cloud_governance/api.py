"""Cloud governance REST API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import rai_gateway.models.cloud_governance  # noqa: F401
from rai_gateway.cloud_governance.domain import CLOUD_GOV_API_PREFIX
from rai_gateway.cloud_governance.service import CloudGovernanceService
from rai_gateway.core.authorization import Principal, require_permission, require_user
from rai_gateway.core.database import get_db

router = APIRouter(prefix=CLOUD_GOV_API_PREFIX, tags=["cloud-governance"])


class ConnectionCreateBody(BaseModel):
    provider: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    scope_identifier: str = Field(..., min_length=1, description="GCP project id")
    auth_mode: str = Field(..., min_length=1)
    auth_config: dict[str, Any] = Field(default_factory=dict)
    enforcement_enabled: bool = False


class CorrelateBody(BaseModel):
    ai_system_id: UUID


class PolicyPreviewBody(BaseModel):
    desired: dict[str, Any] = Field(default_factory=dict)


class BindApprovalBody(BaseModel):
    approval_request_id: UUID


@router.get("/providers")
def list_providers(
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    return {"providers": svc.list_providers()}


@router.get("/connections")
def list_connections(
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    return {
        "connections": [svc.serialize_connection(c) for c in svc.list_connections()]
    }


@router.post("/connections")
def create_connection(
    body: ConnectionCreateBody,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_permission("cloud_governance:manage")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        conn = svc.create_connection(
            provider=body.provider,
            display_name=body.display_name,
            scope_identifier=body.scope_identifier,
            auth_mode=body.auth_mode,
            auth_config=body.auth_config,
            enforcement_enabled=body.enforcement_enabled,
            created_by=user.user_id,
        )
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"connection": svc.serialize_connection(conn)}


@router.get("/connections/{connection_id}")
def get_connection(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    conn = svc.get_connection(connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return {"connection": svc.serialize_connection(conn)}


@router.post("/connections/{connection_id}/verify")
def verify_connection(
    connection_id: UUID,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_permission("cloud_governance:manage")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        result = svc.verify_connection(connection_id, actor_id=user.user_id)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.post("/connections/{connection_id}/sync")
def enqueue_sync(
    connection_id: UUID,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_permission("cloud_governance:manage")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        result = svc.enqueue_sync(connection_id, actor_id=user.user_id)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.get("/connections/{connection_id}/sync-runs")
def list_sync_runs(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        runs = svc.list_sync_runs(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "sync_runs": [
            {
                "id": str(r.id),
                "status": r.status,
                "run_type": r.run_type,
                "assets_discovered": r.assets_discovered,
                "assets_updated": r.assets_updated,
                "partial_errors": r.partial_errors,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in runs
        ]
    }


@router.get("/connections/{connection_id}/assets")
def list_assets(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        assets = svc.list_assets(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "assets": [
            {
                "id": str(a.id),
                "canonical_resource_name": a.canonical_resource_name,
                "asset_type": a.asset_type,
                "display_name": a.display_name,
                "location": a.location,
                "status": a.status,
                "ai_system_id": str(a.ai_system_id) if a.ai_system_id else None,
                "config_summary": a.config_summary,
            }
            for a in assets
        ]
    }


@router.post("/connections/{connection_id}/assets/{asset_id}/correlate")
def correlate_asset(
    connection_id: UUID,
    asset_id: UUID,
    body: CorrelateBody,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_permission("cloud_governance:manage")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        asset = svc.correlate_asset(
            connection_id, asset_id, body.ai_system_id, actor_id=user.user_id
        )
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "asset": {
            "id": str(asset.id),
            "status": asset.status,
            "ai_system_id": str(asset.ai_system_id),
        }
    }


@router.get("/connections/{connection_id}/coverage")
def get_coverage(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        matrix = svc.coverage(connection_id)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"coverage": matrix}


@router.get("/connections/{connection_id}/model-armor")
def get_model_armor(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        state = svc.inspect_model_armor(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail={"error": "GCP_UNAVAILABLE", "message": str(exc)},
        ) from exc
    return {"model_armor": state}


@router.post("/connections/{connection_id}/policy-changes/preview")
def preview_policy_change(
    connection_id: UUID,
    body: PolicyPreviewBody,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_permission("cloud_governance:enforce")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        row = svc.preview_policy_change(
            connection_id, body.desired, actor_id=user.user_id
        )
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "policy_change": {
            "id": str(row.id),
            "status": row.status,
            "preview": row.preview_payload,
        }
    }


@router.post("/policy-changes/{change_id}/bind-approval")
def bind_policy_approval(
    change_id: UUID,
    body: BindApprovalBody,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_permission("cloud_governance:enforce")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        row = svc.bind_policy_change_approval(
            change_id, body.approval_request_id, actor_id=user.user_id
        )
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policy_change_id": str(row.id), "status": row.status}


@router.get("/connections/{connection_id}/drift")
def list_drift(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    findings = svc.list_drift(connection_id)
    return {"findings": [{"id": str(f.id), "finding_type": f.finding_type} for f in findings]}


@router.get("/connections/{connection_id}/evidence")
def list_evidence(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    links = svc.list_evidence(connection_id)
    return {
        "evidence_links": [
            {"id": str(e.id), "ref_type": e.ref_type, "ref_uri": e.ref_uri}
            for e in links
        ]
    }


@router.get("/connections/{connection_id}/onboarding/terraform")
def onboarding_terraform(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> PlainTextResponse:
    svc = CloudGovernanceService(db)
    try:
        content = svc.onboarding_terraform(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(content, media_type="text/plain")


@router.get("/connections/{connection_id}/onboarding/cloud-shell")
def onboarding_cloud_shell(
    connection_id: UUID,
    db: Session = Depends(get_db),
    _user: Principal = Depends(require_permission("cloud_governance:read")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        return svc.onboarding_cloud_shell(connection_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/connections/{connection_id}")
def disconnect_connection(
    connection_id: UUID,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_permission("cloud_governance:manage")),
) -> dict[str, Any]:
    svc = CloudGovernanceService(db)
    try:
        conn = svc.disconnect(connection_id, actor_id=user.user_id)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"connection": svc.serialize_connection(conn)}


@router.get("/capabilities")
def capabilities(
    _user: Principal = Depends(require_user),
) -> dict[str, Any]:
    return {
        "product": "raicentral-ce",
        "cloud_governance": {
            "providers": ["gcp"],
            "enforcement_default": "read_only",
            "live_verification": "optional",
        },
    }
