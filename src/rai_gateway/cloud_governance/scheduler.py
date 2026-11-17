"""Scheduled cloud sync and drift for connected projects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from rai_gateway.cloud_governance.domain import ConnectionStatus
from rai_gateway.cloud_governance.service import CloudGovernanceService
from rai_gateway.models.cloud_governance import CloudConnection


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def process_connection_schedules(db: Session, *, limit: int = 20) -> int:
    """Enqueue sync/drift when interval elapsed for connected connections."""
    now = _utcnow()
    rows = (
        db.query(CloudConnection)
        .filter(CloudConnection.status == ConnectionStatus.CONNECTED.value)
        .limit(limit)
        .all()
    )
    svc = CloudGovernanceService(db)
    triggered = 0
    for conn in rows:
        if conn.sync_interval_hours and conn.sync_interval_hours > 0:
            due = conn.last_scheduled_sync_at is None or conn.last_scheduled_sync_at <= (
                now - timedelta(hours=conn.sync_interval_hours)
            )
            if due:
                svc.enqueue_sync(conn.id, actor_id="scheduler")
                conn.last_scheduled_sync_at = now
                triggered += 1
        if conn.drift_interval_hours and conn.drift_interval_hours > 0:
            due = conn.last_scheduled_drift_at is None or conn.last_scheduled_drift_at <= (
                now - timedelta(hours=conn.drift_interval_hours)
            )
            if due:
                svc.enqueue_drift_check(conn.id, actor_id="scheduler")
                conn.last_scheduled_drift_at = now
                triggered += 1
    if triggered:
        db.flush()
    return triggered
