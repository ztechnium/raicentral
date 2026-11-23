"""Alert catalog matching and integration service."""

from __future__ import annotations

import uuid

import pytest

import json

from rai_gateway.alert_integrations.providers import build_delivery
from rai_gateway.alert_integrations.service import AlertIntegrationService
from rai_gateway.events.alert_catalog import (
    ENFORCEMENT_DENY,
    RISK_FINDING_CREATED,
    any_subscription_matches,
    event_type_matches,
)


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
