"""Integration tests for onboarding inventory discovery (fixture mode)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from onboarding_support import fresh
from sqlalchemy import text

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import OnboardingAIConfiguration
from rai_gateway.onboarding.ai_configuration import AIConfigService, current_effective
from rai_gateway.onboarding.preparation import run_pass
from rai_gateway.onboarding.reconciliation import proposals_from_fixture_partition
from rai_gateway.onboarding.storage import get_source_store

TARGET = {
    "key": "acme-chat",
    "label": "Acme Chat",
    "protocol": "OPENAI_COMPATIBLE_CHAT",
    "base_url": "https://llm.acme.example/v1",
    "models": ["acme-1"],
    "structured_modes": ["JSON_OBJECT"],
    "token_param": "max_tokens",
    "max_output_tokens": 512,
    "credential_alias": "ACME",
    "credential_version": 1,
    "timeout_seconds": 5,
}


@pytest.fixture()
def discovery_env(monkeypatch):
    monkeypatch.setattr(settings, "onboarding_discovery_fixture", True)
    monkeypatch.setattr(
        settings, "onboarding_ai_targets_json", json.dumps([TARGET])
    )
    monkeypatch.setenv("ACME_API_KEY", "sk-test")


@pytest.fixture()
def ai_ready(db_session, monkeypatch):
    cfg = OnboardingAIConfiguration(
        singleton_key="default",
        config_version=1,
        enabled=True,
        target_key=TARGET["key"],
        model=TARGET["models"][0],
        structured_mode="JSON_OBJECT",
        max_output_tokens=256,
        updated_by="test",
    )
    db_session.add(cfg)
    db_session.commit()

    class _Probe:
        outcome = "PASSED"
        ended_at = datetime.now(timezone.utc)

    def _probe(self, _cfg, _fp):
        return _Probe()

    monkeypatch.setattr(AIConfigService, "_matching_probe", _probe)


def test_fixture_partition_extracts_inventory_lines():
    text_value = "system: support-ai, name=Support\nagent: bot, name=Bot\n"
    items = proposals_from_fixture_partition(text_value)
    assert len(items) == 2
    assert items[0]["entity_type"] == "ai_system"
    assert items[0]["generated"]["claims"]["system_key"] == "support-ai"


@pytest.mark.usefixtures("discovery_env", "ai_ready")
def test_inventory_fixture_pipeline(api, client, session_factory, db_session):
    session = api.create().json()
    session = api.text(
        session,
        "system: demo-system, name=Demo System\n",
        role="SUPPORTING_MATERIAL",
        title="inventory.txt",
    ).json()["session"]
    prep = api.prepare(session)
    assert prep.status_code == 202, prep.text
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
    from rai_gateway.onboarding.extraction import current_profile

    extract = client.post(
        f"{api.BASE}/{session['id']}/extract",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "preparation_run_id": session["preparation"]["run_id"],
            "expected_parser_fingerprint": current_profile()["fingerprint"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert extract.status_code == 202, extract.text
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
    extraction_run_id = session["extraction"]["run_id"]
    preview = client.post(
        f"{api.BASE}/{session['id']}/inventory/preview",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_clarification_revision": 0,
            "extraction_run_id": extraction_run_id,
        },
        headers=api.headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["fixture_mode"] is True
    analyze = client.post(
        f"{api.BASE}/{session['id']}/inventory/analyze",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_clarification_revision": 0,
            "plan_id": body["plan_id"],
            "expected_plan_digest": body["plan_digest"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert analyze.status_code == 202, analyze.text
    stats = run_pass(session_factory, get_source_store(), worker_id="t")
    assert stats["succeeded"] >= 1
    sets = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets",
        headers=api.headers,
    )
    assert sets.status_code == 200, sets.text
    assert len(sets.json()["items"]) == 1
    before = db_session.execute(text("SELECT count(*) FROM ai_systems")).scalar()
    after = db_session.execute(text("SELECT count(*) FROM ai_systems")).scalar()
    assert before == after