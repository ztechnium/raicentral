"""REST API for bypass detection and enforcement assurance (Step 7)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

import rai_gateway.models.network_bypass  # noqa: F401
from rai_gateway.core.config import settings
from rai_gateway.core.database import get_db
from rai_gateway.core.authorization import require_permission
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.network_enforcement.domain import (
    ObservedConnectionBatch,
    ObservedConnectionPayload,
)
from rai_gateway.network_enforcement.service import BypassDetectionService

router = APIRouter(tags=["network-enforcement"])


def _svc(db: Session = Depends(get_db)) -> BypassDetectionService:
    return BypassDetectionService(db)


def _require_observation_ingest_token(
    x_rai_network_observation_key: str | None = Header(
        default=None, alias="X-RAI-Network-Observation-Key"
    ),
) -> None:
    expected = settings.network_observation_ingest_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Network observation ingest is not configured",
        )
    if not x_rai_network_observation_key or x_rai_network_observation_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid network observation ingest token",
        )


@router.post("/v1/network-observation/events")
def ingest_observed_connections(
    payload: ObservedConnectionBatch,
    _: None = Depends(_require_observation_ingest_token),
    svc: BypassDetectionService = Depends(_svc),
):
    """Ingest observed egress from eBPF, Hubble, flow logs, or proxy telemetry."""
    return svc.ingest_observed_connections(payload.events)


@router.get("/v1/enforcement-assurance/metrics")
def enforcement_assurance_metrics(
    _tenant: TenantContext = Depends(require_permission("reports:read")),
    svc: BypassDetectionService = Depends(_svc),
):
    metrics = svc.assurance_metrics()
    return {
        "gateway_requests": metrics.gateway_requests,
        "direct_ai_egress_attempts": metrics.direct_ai_egress_attempts,
        "blocked_bypass_attempts": metrics.blocked_bypass_attempts,
        "unclassified_ai_destinations": metrics.unclassified_ai_destinations,
        "runtimes_without_network_enforcement": metrics.runtimes_without_network_enforcement,
    }


@router.get("/v1/enforcement-assurance/runtime-attestation/{runtime_identity_id}")
def runtime_attestation(
    runtime_identity_id: UUID,
    _tenant: TenantContext = Depends(require_permission("reports:read")),
    svc: BypassDetectionService = Depends(_svc),
):
    row = svc.get_runtime_attestation(runtime_identity_id)
    if row is None:
        return {
            "runtime_identity_id": str(runtime_identity_id),
            "status": "UNKNOWN",
            "declared_providers": [],
            "observed_providers": [],
            "authorized_providers": [],
        }
    return row