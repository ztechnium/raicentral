"""FastAPI REST endpoints for reporting and analytics.

Access model
------------
This edition is a single-organization instance: there is no per-row tenant key,
so isolation is enforced by authentication plus RBAC rather than by an
``organization_id`` predicate. Reading any report or snapshot requires
``reports:read``; *creating* a persisted snapshot requires the separate
``reports:snapshot`` permission so read-only roles (for example auditors)
cannot write rows.

Filter contract
---------------
Each endpoint declares exactly the filters it applies. Any other query
parameter is rejected with 422 (``strict_params``) so a client can never send a
filter that is silently ignored.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

import rai_gateway.models.reporting  # noqa: F401 — register snapshot models
from rai_gateway.core.authorization import require_permission
from rai_gateway.core.database import get_db
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.reporting.schemas import (
    AISystemReport,
    AssuranceReport,
    BudgetReport,
    ConversationReport,
    PolicyReport,
    ReportFilters,
    ReportSnapshot,
    ReportSnapshotCreate,
    ReportSummary,
    RiskReport,
    RuntimeReport,
)
from rai_gateway.reporting.service import ReportingService
from rai_gateway.reporting.snapshots import (
    create_snapshot,
    get_snapshot,
    list_snapshots,
    verify_snapshot_integrity,
)

router = APIRouter(prefix="/v1/reports", tags=["reporting"])

SNAPSHOT_REPORT_TYPES = frozenset(
    {
        "EXECUTIVE_SUMMARY",
        "RISK_REPORT",
        "POLICY_REPORT",
        "BUDGET_REPORT",
        "RUNTIME_REPORT",
        "ASSURANCE_REPORT",
        "AI_SYSTEM_REPORT",
    }
)

# Filters each endpoint actually applies. The UI mirrors this table
# (control-plane-ui/lib/reports/filter-support.ts).
PERIOD = ("period_start", "period_end")
SUMMARY_PARAMS = ("ai_system_id", *PERIOD)
RISK_PARAMS = (
    "ai_system_id",
    *PERIOD,
    "risk_severity",
    "risk_category",
    "finding_status",
)
POLICY_PARAMS = ("ai_system_id", *PERIOD, "effect")
BUDGET_PARAMS = ("ai_system_id", *PERIOD, "budget_metric")
RUNTIME_PARAMS = ("ai_system_id", *PERIOD)
ASSURANCE_PARAMS = ("ai_system_id", *PERIOD)
AI_SYSTEM_PARAMS = PERIOD


def strict_params(*allowed: str) -> Callable[[Request], None]:
    """Reject query parameters the endpoint does not apply (422)."""
    allowed_set = frozenset(allowed)

    def _check(request: Request) -> None:
        unsupported = sorted(set(request.query_params.keys()) - allowed_set)
        if unsupported:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "Unsupported report filter(s); they would be ignored.",
                    "unsupported": unsupported,
                    "supported": sorted(allowed_set),
                },
            )

    return _check


def _filters(**values) -> ReportFilters:
    try:
        return ReportFilters(**values)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/summary", response_model=ReportSummary)
def get_summary_report(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params(*SUMMARY_PARAMS)),
    ai_system_id: UUID | None = Query(None),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
) -> ReportSummary:
    """Executive summary: governed activity, open exposure, assurance, attention."""
    filters = _filters(
        ai_system_id=ai_system_id,
        period_start=period_start,
        period_end=period_end,
    )
    return ReportingService(db).get_summary_report(filters)


@router.get("/risk", response_model=RiskReport)
def get_risk_report(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params(*RISK_PARAMS)),
    ai_system_id: UUID | None = Query(None),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
    risk_severity: list[str] | None = Query(None),
    risk_category: list[str] | None = Query(None),
    finding_status: list[str] | None = Query(None),
) -> RiskReport:
    """Risk analysis: open findings (current state) and period events."""
    filters = _filters(
        ai_system_id=ai_system_id,
        period_start=period_start,
        period_end=period_end,
        risk_severity=risk_severity,
        risk_category=risk_category,
        finding_status=finding_status,
    )
    return ReportingService(db).get_risk_report(filters)


@router.get("/policies", response_model=PolicyReport)
def get_policy_report(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params(*POLICY_PARAMS)),
    ai_system_id: UUID | None = Query(None),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
    effect: str | None = Query(None),
) -> PolicyReport:
    """Policy enforcement: effects, decisions, execution outcomes, trends."""
    filters = _filters(
        ai_system_id=ai_system_id,
        period_start=period_start,
        period_end=period_end,
        effect=effect,
    )
    return ReportingService(db).get_policy_report(filters)


@router.get("/budgets", response_model=BudgetReport)
def get_budget_report(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params(*BUDGET_PARAMS)),
    ai_system_id: UUID | None = Query(None),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
    budget_metric: str | None = Query(None),
) -> BudgetReport:
    """Budget pressure and known-cost totals (see ``cost_basis``)."""
    filters = _filters(
        ai_system_id=ai_system_id,
        period_start=period_start,
        period_end=period_end,
        budget_metric=budget_metric,
    )
    return ReportingService(db).get_budget_report(filters)


@router.get("/runtime", response_model=RuntimeReport)
def get_runtime_report(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params(*RUNTIME_PARAMS)),
    ai_system_id: UUID | None = Query(None),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
) -> RuntimeReport:
    """Runtime governance activity and governance funnel."""
    filters = _filters(
        ai_system_id=ai_system_id,
        period_start=period_start,
        period_end=period_end,
    )
    return ReportingService(db).get_runtime_report(filters)


@router.get("/assurance", response_model=AssuranceReport)
def get_assurance_report(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params(*ASSURANCE_PARAMS)),
    ai_system_id: UUID | None = Query(None),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
) -> AssuranceReport:
    """Assurance and control coverage (control assurance, not certification)."""
    filters = _filters(
        ai_system_id=ai_system_id,
        period_start=period_start,
        period_end=period_end,
    )
    return ReportingService(db).get_assurance_report(filters)


@router.get("/ai-systems/{ai_system_id}", response_model=AISystemReport)
def get_ai_system_report(
    ai_system_id: UUID,
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params(*AI_SYSTEM_PARAMS)),
    period_start: datetime | None = Query(None),
    period_end: datetime | None = Query(None),
) -> AISystemReport:
    """Detailed metrics for a specific AI system."""
    filters = _filters(
        ai_system_id=ai_system_id,
        period_start=period_start,
        period_end=period_end,
    )
    try:
        return ReportingService(db).get_ai_system_report(ai_system_id, filters)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.get("/conversations/{conversation_id}", response_model=ConversationReport)
def get_conversation_report(
    conversation_id: UUID,
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(strict_params()),
) -> ConversationReport:
    """Metrics and events for a specific conversation."""
    filters = ReportFilters(conversation_id=conversation_id)
    try:
        return ReportingService(db).get_conversation_report(conversation_id, filters)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


# ============================================================================
# Report Snapshots
# ============================================================================


@router.post(
    "/snapshots", response_model=ReportSnapshot, status_code=status.HTTP_201_CREATED
)
def create_report_snapshot(
    request: ReportSnapshotCreate,
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:snapshot")),
) -> ReportSnapshot:
    """Create an immutable report snapshot (requires ``reports:snapshot``).

    Stores the effective filters and period, schema version, generation
    timestamp and a SHA-256 digest of the stored payload.
    """
    if request.report_type not in SNAPSHOT_REPORT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported report type: {request.report_type}",
        )

    service = ReportingService(db)
    if request.report_type == "EXECUTIVE_SUMMARY":
        report = service.get_summary_report(request.filters)
    elif request.report_type == "RISK_REPORT":
        report = service.get_risk_report(request.filters)
    elif request.report_type == "POLICY_REPORT":
        report = service.get_policy_report(request.filters)
    elif request.report_type == "BUDGET_REPORT":
        report = service.get_budget_report(request.filters)
    elif request.report_type == "RUNTIME_REPORT":
        report = service.get_runtime_report(request.filters)
    elif request.report_type == "ASSURANCE_REPORT":
        report = service.get_assurance_report(request.filters)
    else:  # AI_SYSTEM_REPORT
        if request.filters.ai_system_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ai_system_id is required for an AI system report snapshot",
            )
        try:
            report = service.get_ai_system_report(
                request.filters.ai_system_id, request.filters
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc

    return create_snapshot(
        db=db,
        report_data=report.model_dump(mode="json"),
        request=request,
        generated_by=tenant.user_id,
    )


@router.get("/snapshots", response_model=list[ReportSnapshot])
def list_report_snapshots(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
    _strict: None = Depends(
        strict_params("report_type", "scope_type", "limit", "offset")
    ),
    report_type: str | None = Query(None),
    scope_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[ReportSnapshot]:
    """List historical report snapshots, newest first."""
    return list_snapshots(
        db,
        {
            "report_type": report_type,
            "scope_type": scope_type,
            "limit": limit,
            "offset": offset,
        },
    )


@router.get("/snapshots/{snapshot_id}", response_model=ReportSnapshot)
def get_report_snapshot(
    snapshot_id: UUID,
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(require_permission("reports:read")),
) -> ReportSnapshot:
    """Read a snapshot and verify its content digest."""
    snapshot = get_snapshot(db, snapshot_id)

    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report snapshot not found"
        )

    snapshot.integrity_verified = verify_snapshot_integrity(snapshot)
    return snapshot
