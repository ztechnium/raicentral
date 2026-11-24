"""Best-effort indexing of runtime audit documents into Elasticsearch."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.evidence.domain import CONTENT_PAYLOAD_FIELDS
from rai_gateway.models.evidence import EvidenceEvent, RuntimeTrajectory
from rai_gateway.models.runtime_enforcement import RuntimeEnforcementReceipt
from rai_gateway.search_integrations.service import SearchIntegrationService

logger = logging.getLogger(__name__)


def _content_snippet(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    parts: list[str] = []
    for key in sorted(CONTENT_PAYLOAD_FIELDS):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            parts.append(raw.strip()[:2000])
    return "\n".join(parts)[:4000]


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def index_evidence_event(
    db: Session,
    *,
    event: EvidenceEvent,
    trajectory: RuntimeTrajectory,
) -> None:
    row = SearchIntegrationService(db).get_for_indexing()
    if not row:
        return
    svc = SearchIntegrationService(db)
    client = svc.build_client(row)
    index = svc.audit_index_name(row)
    payload = event.payload if isinstance(event.payload, dict) else {}
    body = {
        "record_type": "EVIDENCE_EVENT",
        "occurred_at": _iso(event.occurred_at),
        "conversation_id": trajectory.conversation_id,
        "trajectory_id": str(trajectory.id),
        "ai_system_id": str(trajectory.ai_system_id),
        "agent_id": str(trajectory.agent_id),
        "runtime_identity_id": str(trajectory.runtime_identity_id),
        "decision": payload.get("decision") or payload.get("effect"),
        "action": event.event_type,
        "correlation_id": event.correlation_id or trajectory.correlation_id,
        "content_text": _content_snippet(payload),
        "assurance": event.assurance_level,
    }
    try:
        client.index_document(index, str(event.id), body)
    except Exception as exc:  # noqa: BLE001 — indexing must not break runtime
        logger.warning("elasticsearch_index_evidence_failed", error=str(exc))
    finally:
        client.close()


def index_enforcement_receipt(
    db: Session,
    *,
    receipt: RuntimeEnforcementReceipt,
    include_execution: bool = False,
) -> None:
    row = SearchIntegrationService(db).get_for_indexing()
    if not row:
        return
    svc = SearchIntegrationService(db)
    client = svc.build_client(row)
    index = svc.audit_index_name(row)
    ctx = receipt.client_context if isinstance(receipt.client_context, dict) else {}
    principal = ctx.get("principal") or ctx.get("user_email")
    channel = ctx.get("channel")
    base = {
        "occurred_at": _iso(receipt.started_at),
        "conversation_id": ctx.get("conversation_id"),
        "ai_system_id": str(receipt.ai_system_id),
        "agent_id": str(receipt.agent_id),
        "runtime_identity_id": str(receipt.runtime_identity_id),
        "decision": receipt.enforcement_decision,
        "execution_status": receipt.execution_status,
        "correlation_id": receipt.correlation_id,
        "principal": principal,
        "channel": channel,
        "action": receipt.action_digest,
    }
    docs: list[tuple[str, dict[str, Any]]] = [
        (str(receipt.id), {**base, "record_type": "ENFORCEMENT"}),
    ]
    if include_execution and receipt.execution_attempted:
        docs.append(
            (f"exec:{receipt.id}", {**base, "record_type": "EXECUTION"}),
        )
    try:
        for doc_id, body in docs:
            client.index_document(index, doc_id, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("elasticsearch_index_receipt_failed", error=str(exc))
    finally:
        client.close()


def index_trajectory_summary(
    db: Session,
    *,
    trajectory: RuntimeTrajectory,
    ai_system_name: str | None = None,
    agent_name: str | None = None,
    runtime_identity_name: str | None = None,
) -> None:
    row = SearchIntegrationService(db).get_for_indexing()
    if not row:
        return
    svc = SearchIntegrationService(db)
    client = svc.build_client(row)
    index = svc.trajectory_index_name(row)
    body = {
        "started_at": _iso(trajectory.started_at),
        "status": trajectory.status,
        "conversation_id": trajectory.conversation_id,
        "client_session_id": trajectory.client_session_id,
        "correlation_id": trajectory.correlation_id,
        "ai_system_id": str(trajectory.ai_system_id),
        "agent_id": str(trajectory.agent_id),
        "runtime_identity_id": str(trajectory.runtime_identity_id),
        "ai_system_name": ai_system_name,
        "agent_name": agent_name,
        "runtime_identity_name": runtime_identity_name,
        "model": getattr(trajectory, "primary_model", None),
    }
    try:
        client.index_document(index, str(trajectory.id), body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("elasticsearch_index_trajectory_failed", error=str(exc))
    finally:
        client.close()
