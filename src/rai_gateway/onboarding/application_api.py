"""HTTP routes for onboarding change-set review and apply (Step 06)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from rai_gateway.core.authorization import require_permission
from rai_gateway.core.database import get_db
from rai_gateway.core.errors import ErrorCode, RaiException
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.onboarding.api import (
    SOURCES_PERMISSION,
    _no_store,
    _service,
    idempotency_key,
)
from rai_gateway.onboarding.application import ApplicationCoordinator
from rai_gateway.onboarding.application_schemas import (
    ApplyChangeSetBody,
    PrepareChangeSetBody,
    RetryApplicationBody,
    ReviewDecisionsBody,
)
from rai_gateway.onboarding.change_sets import ChangeSetService
from rai_gateway.onboarding.service import (
    POLICIES_WRITE_PERMISSION,
    REGISTRY_WRITE_PERMISSION,
    Access,
)

SESSION_PREFIX = "/v1/productization/onboarding/sessions"
router = APIRouter(prefix=SESSION_PREFIX, tags=["onboarding-application"])


def apply_access(
    tenant: TenantContext = Depends(require_permission("sdk:manage")),
) -> tuple[TenantContext, Access]:
    access = Access.from_tenant(tenant)
    if not access.can_apply:
        missing = []
        if not tenant.has_permission(SOURCES_PERMISSION):
            missing.append(SOURCES_PERMISSION)
        if not tenant.has_permission(REGISTRY_WRITE_PERMISSION):
            missing.append(REGISTRY_WRITE_PERMISSION)
        if not tenant.has_permission(POLICIES_WRITE_PERMISSION):
            missing.append(POLICIES_WRITE_PERMISSION)
        raise RaiException(
            error_code=ErrorCode.NOT_ALLOWED,
            message="Missing required permission",
            details={"required_permissions": missing or [SOURCES_PERMISSION]},
        )
    return tenant, access


@router.post("/{session_id}/change-sets/prepare")
def prepare_change_set(
    session_id: UUID,
    body: PrepareChangeSetBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    return ChangeSetService(_service(db, tenant)).prepare_change_set(
        session_id,
        body.selection.model_dump(),
        access,
        expected_row_version=body.expected_row_version,
        requested_input_revision=body.requested_input_revision,
    )


@router.get("/{session_id}/change-sets")
def list_change_sets(
    session_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    return ChangeSetService(_service(db, tenant)).list_change_sets(session_id, access)


@router.get("/{session_id}/change-sets/{change_set_id}")
def get_change_set(
    session_id: UUID,
    change_set_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    return ChangeSetService(_service(db, tenant)).get_change_set(
        session_id, change_set_id, access
    )


@router.post("/{session_id}/change-sets/{change_set_id}/discard")
def discard_change_set(
    session_id: UUID,
    change_set_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    return ChangeSetService(_service(db, tenant)).discard(
        session_id, change_set_id, access=access
    )


@router.post("/{session_id}/change-sets/{change_set_id}/review")
def review_change_set(
    session_id: UUID,
    change_set_id: UUID,
    body: ReviewDecisionsBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    decisions = [d.model_dump() for d in body.decisions]
    return ChangeSetService(_service(db, tenant)).record_review_decisions(
        session_id, change_set_id, decisions, access
    )


@router.post("/{session_id}/change-sets/{change_set_id}/apply", status_code=202)
def apply_change_set(
    session_id: UUID,
    change_set_id: UUID,
    body: ApplyChangeSetBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
    key: str = Depends(idempotency_key),
):
    tenant, access = pair
    _no_store(response)
    return ApplicationCoordinator(_service(db, tenant)).enqueue_apply(
        session_id,
        change_set_id=change_set_id,
        expected_row_version=body.expected_row_version,
        requested_input_revision=body.requested_input_revision,
        expected_payload_digest=body.expected_payload_digest,
        idempotency_key=key,
        access=access,
    )


@router.get("/{session_id}/applications/{application_id}")
def get_application(
    session_id: UUID,
    application_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    return ApplicationCoordinator(_service(db, tenant)).get_application(
        session_id, application_id, access
    )


@router.post("/{session_id}/applications/{application_id}/cancel")
def cancel_application(
    session_id: UUID,
    application_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
):
    tenant, access = pair
    _no_store(response)
    return ApplicationCoordinator(_service(db, tenant)).cancel(
        session_id, application_id, access=access
    )


@router.post("/{session_id}/applications/{application_id}/retry", status_code=202)
def retry_application(
    session_id: UUID,
    application_id: UUID,
    body: RetryApplicationBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(apply_access),
    key: str = Depends(idempotency_key),
):
    tenant, access = pair
    _no_store(response)
    return ApplicationCoordinator(_service(db, tenant)).retry(
        session_id,
        application_id,
        expected_row_version=body.expected_row_version,
        idempotency_key=key,
        access=access,
    )


def register_application(app) -> None:
    app.include_router(router)
