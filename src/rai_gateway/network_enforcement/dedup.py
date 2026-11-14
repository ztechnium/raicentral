"""Dedup keys for observed connection telemetry."""

from __future__ import annotations

import hashlib
from datetime import datetime

from rai_gateway.network_enforcement.domain import ObservedConnectionPayload


def _bucket_minute(ts: datetime) -> str:
    return ts.replace(second=0, microsecond=0).isoformat()


def observed_event_dedup_key(payload: ObservedConnectionPayload) -> str:
    """Stable fingerprint to collapse duplicate flow log lines within a minute."""
    parts = (
        _bucket_minute(payload.observed_at),
        payload.source_workload or "",
        payload.source_process or "",
        payload.destination_hostname.lower(),
        payload.destination_ip or "",
        str(payload.destination_port or ""),
        payload.protocol or "",
        "1" if payload.gateway_used else "0",
        str(payload.runtime_identity_id or ""),
        payload.action.value,
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest