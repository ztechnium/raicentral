"""HTTP routes for Step 07 runtime connection, activation and verification."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from rai_gateway.core.authorization import require_permission
from rai_gateway.core.database import get_db
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.onboarding.activation import ActivationCoordinator
from rai_gateway.onboarding.api import SOURCES_PERMISSION, _no_store, _service, idempotency_key
from rai_gateway.onboarding.runtime_context import RuntimeContextService
from rai_gateway.onboarding.service import (
    POLICIES_WRITE_PERMISSION,
    REGISTRY_WRITE_PERMISSION,
    Access,
)
from rai_gateway.onboarding.verification_plans import VerificationCoordinator
from rai_gateway.onboarding.verification_schemas import (
    ActivateControlsBody,
    PrepareActivationPlanBody,
    PrepareVerificationPlanBody,
    SaveRuntimeContextBody,
    VerifyRuntimeBody,
)

SESSION_PREFIX = "/v1/productization/onboarding/sessions"
ONBOARDING_PREFIX = "/v1/productization/onboarding"
session_router = APIRouter(prefix=SESSION_PREFIX, tags=["onboarding-verification"])
plan_router = APIRouter(prefix=ONBOARDING_PREFIX, tags=["onboarding-verification"])


def verify_access(
    tenant: TenantContext = Depends(require_permission("sdk:manage")),
) -> tuple[TenantContext, Access]:
    access = Access.from_tenant(tenant)
    if not access.can_apply:
        from rai_gateway.core.errors import ErrorCode, RaiException

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


def verify_read(
    tenant: TenantContext = Depends(require_permission("sdk:read")),
) -> tuple[TenantContext, Access]:
    return tenant, Access.from_tenant(tenant)


@session_router.get("/{session_id}/runtime-context")
def get_runtime_context(
    session_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_read),
):
    tenant, access = pair
    _no_store(response)
    return RuntimeContextService(_service(db, tenant)).get_runtime_context(session_id, access)


@session_router.patch("/{session_id}/runtime-context")
def save_runtime_context(
    session_id: UUID,
    body: SaveRuntimeContextBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_access),
):
    tenant, access = pair
    _no_store(response)
    return RuntimeContextService(_service(db, tenant)).save_runtime_context(
        session_id,
        expected_row_version=body.expected_row_version,
        delegation_mode=body.delegation_mode,
        selected=body.selected.model_dump(mode="json"),
        access=access,
    )


@session_router.post("/{session_id}/activation-plans")
def prepare_activation_plan(
    session_id: UUID,
    body: PrepareActivationPlanBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_access),
):
    tenant, access = pair
    _no_store(response)
    return ActivationCoordinator(_service(db, tenant)).prepare_activation_plan(
        session_id,
        expected_row_version=body.expected_row_version,
        expected_selection_digest=body.expected_selection_digest,
        access=access,
    )


@plan_router.post("/activation-plans/{plan_id}/activate", status_code=202)
def activate_controls(
    plan_id: UUID,
    body: ActivateControlsBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_access),
    key: str = Depends(idempotency_key),
):
    from rai_gateway.models.onboarding import OnboardingActivationPlan

    tenant, access = pair
    _no_store(response)
    plan = db.get(OnboardingActivationPlan, plan_id)
    if plan is None:
        from rai_gateway.core.errors import ErrorCode, RaiException

        raise RaiException(error_code=ErrorCode.NOT_FOUND, message="Activation plan not found")
    return ActivationCoordinator(_service(db, tenant)).enqueue_activate(
        plan.session_id,
        plan_id=plan_id,
        expected_row_version=body.expected_row_version,
        expected_payload_digest=body.expected_payload_digest,
        idempotency_key=key,
        access=access,
    )


@plan_router.get("/activation-plans/{plan_id}/result")
def activation_result(
    plan_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_read),
):
    from rai_gateway.models.onboarding import OnboardingActivationPlan

    tenant, access = pair
    _no_store(response)
    plan = db.get(OnboardingActivationPlan, plan_id)
    if plan is None:
        from rai_gateway.core.errors import ErrorCode, RaiException

        raise RaiException(error_code=ErrorCode.NOT_FOUND, message="Activation plan not found")
    return ActivationCoordinator(_service(db, tenant)).get_plan(plan.session_id, plan_id, access)


@session_router.post("/{session_id}/verification-plans")
def prepare_verification_plan(
    session_id: UUID,
    body: PrepareVerificationPlanBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_access),
):
    tenant, access = pair
    _no_store(response)
    return VerificationCoordinator(_service(db, tenant)).prepare_verification_plan(
        session_id,
        activation_plan_id=UUID(body.activation_plan_id),
        expected_row_version=body.expected_row_version,
        scenarios=body.scenarios or None,
        access=access,
    )


@plan_router.get("/verification-plans/{plan_id}")
def get_verification_plan(
    plan_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_read),
):
    from rai_gateway.models.onboarding import OnboardingVerificationPlan

    tenant, access = pair
    _no_store(response)
    plan = db.get(OnboardingVerificationPlan, plan_id)
    if plan is None:
        from rai_gateway.core.errors import ErrorCode, RaiException

        raise RaiException(error_code=ErrorCode.NOT_FOUND, message="Verification plan not found")
    return VerificationCoordinator(_service(db, tenant)).get_plan(plan.session_id, plan_id, access)


@plan_router.post("/verification-plans/{plan_id}/start", status_code=202)
def start_verification(
    plan_id: UUID,
    body: VerifyRuntimeBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(verify_access),
    key: str = Depends(idempotency_key),
):
    from rai_gateway.models.onboarding import OnboardingVerificationPlan

    tenant, access = pair
    _no_store(response)
    plan = db.get(OnboardingVerificationPlan, plan_id)
    if plan is None:
        from rai_gateway.core.errors import ErrorCode, RaiException

        raise RaiException(error_code=ErrorCode.NOT_FOUND, message="Verification plan not found")
    return VerificationCoordinator(_service(db, tenant)).enqueue_verify(
        plan.session_id,
        plan_id=plan_id,
        expected_row_version=body.expected_row_version,
        expected_payload_digest=body.expected_payload_digest,
        idempotency_key=key,
        access=access,
    )


def register_verification(app) -> None:
    app.include_router(session_router)
    app.include_router(plan_router)