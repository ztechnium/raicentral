"""HTTP routes for incremental onboarding updates (Step 08)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from rai_gateway.core.database import get_db
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.onboarding.api import (
    _no_store,
    _service,
    governance_access,
    idempotency_key,
    manage_access,
    read_access,
)
from rai_gateway.onboarding.application_api import apply_access
from rai_gateway.onboarding.delta_coordinator import DeltaUpdateService
from rai_gateway.onboarding.delta_schemas import (
    BaselineConfirmBody,
    BaselineReviewBody,
    CreateUpdateSessionBody,
    DeltaDecisionBody,
    DeltaPrepareReviewBody,
    DeltaPreviewBody,
    DeltaRefreshBody,
    DeltaRunBody,
    SourceLineageBody,
)
from rai_gateway.onboarding.service import Access

SESSION_PREFIX = "/v1/productization/onboarding/sessions"
UPDATE_PREFIX = "/v1/productization/onboarding"
session_router = APIRouter(prefix=SESSION_PREFIX, tags=["onboarding-delta"])
update_router = APIRouter(prefix=UPDATE_PREFIX, tags=["onboarding-delta"])


@update_router.post("/update-sessions")
def create_update_session(
    body: CreateUpdateSessionBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
    key: str = Depends(idempotency_key),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).create_update_session(
        title=body.title,
        baseline_session_id=body.baseline_session_id,
        baseline_id=body.baseline_id,
        idempotency_key=key,
        access=access,
    )


@session_router.get("/{session_id}/baselines")
def list_baselines(
    session_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).list_baselines(session_id, access)


@session_router.post("/{session_id}/baselines/review")
def review_baseline(
    session_id: UUID,
    body: BaselineReviewBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).review_baseline(
        session_id, body.model_dump(), access
    )


@session_router.post("/{session_id}/baselines/confirm")
def confirm_baseline(
    session_id: UUID,
    body: BaselineConfirmBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).confirm_baseline(
        session_id,
        body.baseline_id,
        expected_row_version=body.expected_row_version,
        access=access,
    )


@session_router.post("/{session_id}/source-lineage")
def record_source_lineage(
    session_id: UUID,
    body: SourceLineageBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).record_lineage(
        session_id, body.model_dump(), access
    )


@session_router.post("/{session_id}/delta/preview")
def preview_delta(
    session_id: UUID,
    body: DeltaPreviewBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).preview_delta_plan(
        session_id,
        body.baseline_id,
        expected_row_version=body.expected_row_version,
        requested_input_revision=body.requested_input_revision,
        access=access,
    )


@session_router.post("/{session_id}/delta/runs")
def enqueue_delta(
    session_id: UUID,
    body: DeltaRunBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
    key: str = Depends(idempotency_key),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).enqueue_delta_run(
        session_id,
        body.delta_plan_id,
        expected_plan_digest=body.expected_plan_digest,
        expected_row_version=body.expected_row_version,
        idempotency_key=key,
        access=access,
    )


@session_router.get("/{session_id}/delta/summary")
def delta_summary(
    session_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(read_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).get_summary(session_id, access)


@session_router.get("/{session_id}/delta/findings")
def delta_findings(
    session_id: UUID,
    response: Response,
    plan_id: UUID = Query(...),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).list_findings(session_id, plan_id, access)


@session_router.get("/{session_id}/delta/impacts")
def delta_impacts(
    session_id: UUID,
    response: Response,
    plan_id: UUID = Query(...),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).list_impacts(session_id, plan_id, access)


@session_router.get("/{session_id}/delta/reconciliation")
def delta_reconciliation(
    session_id: UUID,
    response: Response,
    plan_id: UUID = Query(...),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).get_reconciliation(
        session_id, plan_id, access
    )


@session_router.post("/{session_id}/delta/refresh")
def delta_refresh(
    session_id: UUID,
    body: DeltaRefreshBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).refresh_reconciliation(
        session_id,
        body.delta_plan_id,
        expected_version=body.expected_reconciliation_version,
        access=access,
    )


@session_router.post("/{session_id}/delta/decisions")
def save_delta_decision(
    session_id: UUID,
    body: DeltaDecisionBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(governance_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).save_decision(
        session_id, body.model_dump(), access
    )


@session_router.post("/{session_id}/delta/prepare-review")
def prepare_delta_review(
    session_id: UUID,
    body: DeltaPrepareReviewBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).prepare_review(
        session_id, body.model_dump(), access
    )


@session_router.get("/{session_id}/delta/verification-impact")
def delta_verification_impact(
    session_id: UUID,
    response: Response,
    plan_id: UUID = Query(...),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(read_access),
):
    tenant, access = pair
    _no_store(response)
    return DeltaUpdateService(_service(db, tenant)).list_verification_impact(
        session_id, plan_id, access
    )


def register_delta(app) -> None:
    app.include_router(session_router)
    app.include_router(update_router)
