"""Alert catalog matching and integration service."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import json

from rai_gateway.alert_integrations.providers import build_delivery
from rai_gateway.alert_integrations.service import AlertIntegrationService
from rai_gateway.core.config import settings
from rai_gateway.events.alert_catalog import (
    ENFORCEMENT_DENY,
    RISK_FINDING_CREATED,
    any_subscription_matches,
    event_type_matches,
)
from rai_gateway.models.productization import WebhookDelivery
from rai_gateway.sdk.webhooks import WebhookService


def test_whatsapp_meta_cloud_api_delivery() -> None:
    url, headers, body = build_delivery(
        "whatsapp",
        event_type="enforcement.deny",
        payload={"enforcement_decision": "DENY", "reason_code": "POLICY"},
        config={
            "access_token": "meta-test-token",
            "phone_number_id": "987654321",
            "recipient_phone": "+15551234567",
        },
    )
    assert "graph.facebook.com" in url
    assert url.endswith("/987654321/messages")
    assert headers["Authorization"] == "Bearer meta-test-token"
    data = json.loads(body)
    assert data["messaging_product"] == "whatsapp"
    assert data["to"] == "15551234567"


def test_event_type_wildcards() -> None:
    assert event_type_matches("enforcement.*", ENFORCEMENT_DENY)
    assert event_type_matches("approval.*", "approval.requested")
    assert not event_type_matches("enforcement.deny", RISK_FINDING_CREATED)
    assert any_subscription_matches(["enforcement.*", "risk.finding.created"], ENFORCEMENT_DENY)


def test_create_integration_and_list(db_session) -> None:
    svc = AlertIntegrationService(db_session)
    row = svc.create_integration(
        name="Ops Slack",
        provider="slack",
        event_types=["enforcement.*"],
        config={"webhook_url": "https://hooks.example.test/slack"},
        custom_headers={"X-Test": "1"},
        created_by="tester",
    )
    assert row["provider"] == "slack"
    listed = svc.list_integrations()
    assert any(i["id"] == row["id"] for i in listed)


@pytest.mark.parametrize("fails,expected", [(False, "DELIVERED"), (True, "FAILED")])
def test_broadcast_records_last_event(db_session, monkeypatch, fails, expected) -> None:
    def deliver(self, row, **kwargs):
        if fails:
            raise RuntimeError("boom")

    monkeypatch.setattr(AlertIntegrationService, "_deliver_one", deliver)
    svc = AlertIntegrationService(db_session)
    created = svc.create_integration(
        name=f"Last event {expected}",
        provider="slack",
        event_types=["enforcement.*"],
        config={},
        custom_headers=None,
        created_by="tester",
    )
    assert created["last_event_at"] is None

    svc.broadcast(event_type=ENFORCEMENT_DENY, payload={}, event_id="evt-1")

    row = next(i for i in svc.list_integrations() if i["id"] == created["id"])
    assert row["last_event_status"] == expected
    assert row["last_event_at"]


def test_list_endpoints_reports_latest_delivery(db_session, monkeypatch) -> None:
    monkeypatch.setattr(settings, "webhook_allow_private_hosts", True)
    svc = WebhookService(db_session)
    endpoint, _secret = svc.create_endpoint(
        name="Latest delivery",
        url="https://hooks.example.test/latest",
        event_types=["enforcement.*"],
        created_by="tester",
    )
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for offset, status in ((0, "DELIVERED"), (1, "DEAD_LETTER")):
        db_session.add(
            WebhookDelivery(
                endpoint_id=uuid.UUID(endpoint["id"]),
                event_type=ENFORCEMENT_DENY,
                event_id=f"evt-{offset}",
                payload={},
                status=status,
                created_at=older + timedelta(days=offset),
            )
        )
    db_session.flush()

    row = next(i for i in svc.list_endpoints() if i["id"] == endpoint["id"])
    assert row["last_event_status"] == "DEAD_LETTER"
    assert row["last_event_at"].startswith("2026-01-02")


def test_create_integration_rejects_unknown_provider(db_session) -> None:
    svc = AlertIntegrationService(db_session)
    with pytest.raises(Exception):
        svc.create_integration(
            name="Bad",
            provider="pagerduty",
            event_types=["*"],
            config={},
            custom_headers=None,
            created_by="tester",
        )

def test_slack_delivery_includes_finding_and_trajectory_links(monkeypatch):
    import json

    from rai_gateway.alert_integrations.providers import build_delivery
    from rai_gateway.core.config import settings

    monkeypatch.setattr(settings, "app_public_url", "https://rai.example.com/")
    payload = {
        "finding_id": "f1",
        "trajectory_id": "t1",
        "ai_system_id": "s1",
        "title": "Policy denial",
    }
    _url, _headers, body = build_delivery(
        "slack",
        event_type="risk.finding.created",
        payload=payload,
        config={"webhook_url": "https://hooks.slack.com/services/x"},
    )
    text = body.decode()
    assert "https://rai.example.com/runtime/trajectories/t1" in text
    assert "https://rai.example.com/risk/findings?finding_id=f1&ai_system_id=s1" in text
    json.loads(text)
