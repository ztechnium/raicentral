"""First-run setup routes: ``/v1/productization/first-run``."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from rai_gateway.core.authorization import require_permission, require_tenant
from rai_gateway.core.database import get_db
from rai_gateway.core.observability import generate_trace_id, log_admin_audit
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.public_product import first_run

router = APIRouter(prefix="/v1/productization/first-run", tags=["productization"])

# Every write below creates registry records and runtime access.
_registry_write = require_permission("registry:write")
_setup_manage = require_permission("sdk:manage")


def _http_error(exc: first_run.FirstRunError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"error": exc.code, "message": exc.message},
    )


def _log(tenant: TenantContext, entries: list[tuple[str, dict[str, Any]]]) -> None:
    trace_id = generate_trace_id()
    for action, target in entries:
        log_admin_audit(
            trace_id=trace_id,
            actor_user_id=tenant.user_id,
            actor_role=",".join(tenant.roles),
            action=action,
            target=target,
        )


@router.get("")
def get_first_run(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
):
    return first_run.get_status(db, tenant)


@router.get("/catalog")
def get_first_run_catalog(
    _tenant: TenantContext = Depends(require_permission("registry:read")),
):
    return first_run.get_catalog()


@router.post("/skip")
def skip_first_run(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_tenant),
):
    # Not gated on email verification: an unverified owner must still be able to leave.
    if not tenant.has_permission("sdk:manage"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "NOT_ALLOWED",
                "message": "Missing required permission",
            },
        )
    first_run.skip_setup(db)
    _log(tenant, [("first_run.skipped", {})])
    return first_run.get_status(db, tenant)


@router.post("/apply/resources")
def apply_first_run_resources(
    body: first_run.ResourcesRequest,
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(_registry_write),
    _manage: TenantContext = Depends(_setup_manage),
):
    try:
        result, audit = first_run.apply_resources(db, body, actor=tenant.user_id)
    except first_run.FirstRunError as exc:
        raise _http_error(exc) from exc
    _log(tenant, audit)
    return result


@router.post("/apply/systems")
def apply_first_run_systems(
    body: first_run.SystemsRequest,
    response: Response,
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(_registry_write),
    _manage: TenantContext = Depends(_setup_manage),
):
    try:
        result, audit = first_run.apply_systems(db, body, actor=tenant.user_id)
    except first_run.FirstRunError as exc:
        raise _http_error(exc) from exc
    _log(tenant, audit)
    # The response carries credential secrets.
    response.headers["Cache-Control"] = "no-store"
    return result
