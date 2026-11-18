"""Integration tests — bypass ingest, incidents, attestation."""

from __future__ import annotations

from datetime import datetime, timezone

from rai_gateway.models.governance import AIIncident
from rai_gateway.models.network_bypass import ObservedConnectionEvent, RuntimeAttestationState
from rai_gateway.network_enforcement.domain import ObservedConnectionPayload
from rai_gateway.network_enforcement.service import BypassDetectionService


def test_bypass_incident_and_attestation(db_session, runtime_stack) -> None:
    identity_id = runtime_stack["identity"].id
    payload = ObservedConnectionPayload(
        observed_at=datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc),
        destination_hostname="api.anthropic.com",
        gateway_used=False,
        runtime_identity_id=identity_id,
    )
    svc = BypassDetectionService(db_session)
    result = svc.ingest_observed_connections([payload], reported_by="tester")
    assert result["accepted"] == 1
    assert result["bypass_events"] == 1
    assert len(result["incident_ids"]) == 1

    incident = (
        db_session.query(AIIncident)
        .filter(AIIncident.runtime_identity_id == identity_id)
        .one()
    )
    assert incident.category == "SHADOW_AI"
    assert incident.runtime_identity_id == identity_id

    attestation = (
        db_session.query(RuntimeAttestationState)
        .filter(RuntimeAttestationState.runtime_identity_id == identity_id)
        .one()
    )
    assert attestation.status == "BYPASS_DETECTED"

    event = (
        db_session.query(ObservedConnectionEvent)
        .filter(ObservedConnectionEvent.runtime_identity_id == identity_id)
        .one()
    )
    assert event.incident_id == incident.id
    assert event.bypass_detected is True


def test_gateway_traffic_not_bypass_flagged(db_session, runtime_stack) -> None:
    identity_id = runtime_stack["identity"].id
    payload = ObservedConnectionPayload(
        observed_at=datetime(2026, 11, 14, 10, 5, tzinfo=timezone.utc),
        destination_hostname="api.openai.com",
        gateway_used=True,
        runtime_identity_id=identity_id,
    )
    svc = BypassDetectionService(db_session)
    result = svc.ingest_observed_connections([payload])
    assert result["accepted"] == 1
    assert result["bypass_events"] == 0
    event = (
        db_session.query(ObservedConnectionEvent)
        .filter(ObservedConnectionEvent.runtime_identity_id == identity_id)
        .one()
    )
    assert event.bypass_detected is False
    assert (
        db_session.query(AIIncident)
        .filter(AIIncident.runtime_identity_id == identity_id)
        .count()
        == 0
    )

def test_traffic_share_counts_period_and_handles_no_data(db_session) -> None:
    svc = BypassDetectionService(db_session)
    start = datetime(2027, 3, 1, tzinfo=timezone.utc)
    end = datetime(2027, 3, 30, tzinfo=timezone.utc)

    empty = svc.traffic_share(period_start=start, period_end=end)
    assert empty["share_pct"] is None
    assert empty["observed_ai_connections"] == 0

    def event(day: int, host: str, via_gateway: bool) -> ObservedConnectionPayload:
        return ObservedConnectionPayload(
            observed_at=datetime(2027, 3, day, 9, 0, tzinfo=timezone.utc),
            destination_hostname=host,
            gateway_used=via_gateway,
        )

    svc.ingest_observed_connections(
        [
            event(2, "api.openai.com", True),
            event(3, "api.openai.com", True),
            event(4, "api.anthropic.com", True),
            event(5, "api.anthropic.com", False),
            # Outside the window: must not count.
            ObservedConnectionPayload(
                observed_at=datetime(2027, 2, 1, tzinfo=timezone.utc),
                destination_hostname="api.openai.com",
                gateway_used=False,
            ),
        ]
    )
    share = svc.traffic_share(period_start=start, period_end=end)
    assert share["observed_ai_connections"] == 4
    assert share["via_gateway"] == 3
    assert share["direct"] == 1
    assert share["share_pct"] == 75.0
