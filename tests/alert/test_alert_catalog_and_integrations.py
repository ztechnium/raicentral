"""Alert catalog matching and integration service."""

from __future__ import annotations

import uuid

import pytest

from rai_gateway.alert_integrations.service import AlertIntegrationService
from rai_gateway.events.alert_catalog import (
    ENFORCEMENT_DENY,
    RISK_FINDING_CREATED,
    any_subscription_matches,
    event_type_matches,
)


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