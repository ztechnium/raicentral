"""Transactional outbox helpers — never send HTTP inside the domain transaction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from rai_gateway.events.alert_catalog import (
    ENFORCEMENT_ALLOW,
    ENFORCEMENT_DENY,
    ENFORCEMENT_MONITOR,
    ENFORCEMENT_REQUIRE_APPROVAL,
    RISK_FINDING_CREATED,
)
from rai_gateway.models.approval import ApprovalRequest
from rai_gateway.models.events import EventOutbox
from rai_gateway.models.risk_intelligence import RuntimeRiskFinding
from rai_gateway.models.runtime_enforcement import RuntimeEnforcementReceipt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def approval_event_payload(request: ApprovalRequest) -> dict[str, Any]:
    """Safe webhook/outbox payload — no raw action parameters."""
    return {
        "approval_request_id": str(request.id),
        "status": request.status,
        "ai_system_id": str(request.ai_system_id),
        "agent_id": str(request.agent_id),
        "agent_version_id": str(request.agent_version_id),
        "runtime_identity_id": str(request.runtime_identity_id),
        "delegation_id": str(request.delegation_id) if request.delegation_id else None,
        "action_digest": request.action_digest,
        "risk_tier": request.risk_tier,
        "expires_at": request.expires_at.isoformat() if request.expires_at else None,
        "correlation_id": request.correlation_id,
        "runtime_idempotency_key": getattr(request, "runtime_idempotency_key", None),
    }


def enqueue_outbox_event(
    db: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    payload: dict[str, Any],
    available_at: datetime | None = None,
) -> EventOutbox:
    now = _utcnow()
    row = EventOutbox(
        id=uuid4(),
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        occurred_at=now,
        available_at=available_at or now,
        processed_at=None,
        attempt_count=0,
    )
    db.add(row)
    db.flush()
    return row


def enqueue_approval_event(
    db: Session,
    request: ApprovalRequest,
    event_type: str,
) -> EventOutbox | None:
    return enqueue_outbox_event(
        db,
        event_type=event_type,
        aggregate_type="approval_request",
        aggregate_id=request.id,
        payload=approval_event_payload(request),
    )


def enforcement_event_type(receipt: RuntimeEnforcementReceipt) -> str | None:
    decision = (receipt.enforcement_decision or "").upper()
    if decision == "DENY":
        return ENFORCEMENT_DENY
    if decision == "REQUIRE_APPROVAL":
        return ENFORCEMENT_REQUIRE_APPROVAL
    if decision == "ALLOW" and receipt.monitor:
        return ENFORCEMENT_MONITOR
    if decision == "ALLOW" and (receipt.policy_effect or "").upper() == "MONITOR":
        return ENFORCEMENT_MONITOR
    if decision == "ALLOW":
        return ENFORCEMENT_ALLOW
    if receipt.monitor:
        return ENFORCEMENT_MONITOR
    return None


def enforcement_event_payload(receipt: RuntimeEnforcementReceipt) -> dict[str, Any]:
    return {
        "receipt_id": str(receipt.id),
        "enforcement_decision": receipt.enforcement_decision,
        "reason_code": receipt.reason_code,
        "human_explanation": receipt.human_explanation,
        "policy_effect": receipt.policy_effect,
        "monitor": bool(receipt.monitor),
        "ai_system_id": str(receipt.ai_system_id),
        "agent_id": str(receipt.agent_id),
        "agent_version_id": str(receipt.agent_version_id),
        "runtime_identity_id": str(receipt.runtime_identity_id),
        "action_digest": receipt.action_digest,
        "correlation_id": receipt.correlation_id,
        "approval_request_id": str(receipt.approval_request_id)
        if receipt.approval_request_id
        else None,
    }


def enqueue_enforcement_alert(
    db: Session,
    receipt: RuntimeEnforcementReceipt,
) -> EventOutbox | None:
    event_type = enforcement_event_type(receipt)
    if not event_type:
        return None
    # Skip noisy pure allows unless subscribed explicitly via enforcement.allow
    if event_type == ENFORCEMENT_ALLOW:
        return None
    return enqueue_outbox_event(
        db,
        event_type=event_type,
        aggregate_type="enforcement_receipt",
        aggregate_id=receipt.id,
        payload=enforcement_event_payload(receipt),
    )


def finding_event_payload(finding: RuntimeRiskFinding) -> dict[str, Any]:
    return {
        "finding_id": str(finding.id),
        "finding_type": finding.finding_type,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity,
        "status": finding.status,
        "category": finding.category,
        "ai_system_id": str(finding.ai_system_id),
        "agent_id": str(finding.agent_id) if finding.agent_id else None,
        "agent_version_id": str(finding.agent_version_id)
        if finding.agent_version_id
        else None,
        "runtime_identity_id": str(finding.runtime_identity_id)
        if finding.runtime_identity_id
        else None,
        "intervention_effect": finding.intervention_effect,
    }


def enqueue_finding_alert(
    db: Session,
    finding: RuntimeRiskFinding,
) -> EventOutbox:
    return enqueue_outbox_event(
        db,
        event_type=RISK_FINDING_CREATED,
        aggregate_type="runtime_risk_finding",
        aggregate_id=finding.id,
        payload=finding_event_payload(finding),
    )
