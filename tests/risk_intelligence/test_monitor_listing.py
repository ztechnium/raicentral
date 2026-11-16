"""Monitor listing: expiry sweep and the fields the monitoring page shows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rai_gateway.risk_intelligence.service import RiskIntelligenceService


def _monitor(repo, system, identity, *, expires_in, rule_id=None, reason="r", count=1):
    return repo.create_monitor(
        ai_system_id=system.id,
        monitor_rule_id=rule_id,
        scope_type="RUNTIME_IDENTITY",
        scope_id=str(identity.id),
        reason=reason,
        status="ACTIVE",
        current_count=count,
        current_sum=0.0,
        threshold=None,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )


def test_expired_monitors_are_flipped_and_hidden(db_session, runtime_stack):
    svc = RiskIntelligenceService(db_session)
    system, identity = runtime_stack["system"], runtime_stack["identity"]
    expired = _monitor(svc._repo, system, identity, expires_in=-60)
    live = _monitor(svc._repo, system, identity, expires_in=600, reason="live")
    db_session.commit()

    items = svc.list_monitors(ai_system_id=system.id, status="ACTIVE", limit=50)
    ids = {i["id"] for i in items}
    assert str(live.id) in ids and str(expired.id) not in ids
    db_session.refresh(expired)
    assert expired.status == "EXPIRED"


def test_policy_monitor_is_labelled_with_source_and_scope_name(db_session, runtime_stack):
    svc = RiskIntelligenceService(db_session)
    system, identity = runtime_stack["system"], runtime_stack["identity"]
    _monitor(svc._repo, system, identity, expires_in=600, reason="Budget MONITOR: x", count=3)
    db_session.commit()

    item = svc.list_monitors(ai_system_id=system.id, status="ACTIVE", limit=50)[0]
    assert item["source"] == "POLICY"
    assert item["rule_name"] is None and item["aggregation"] is None
    assert item["scope_label"] == identity.name
    assert item["current_count"] == 3
