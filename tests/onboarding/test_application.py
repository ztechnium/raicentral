"""Integration tests for onboarding change-set application (Step 06)."""

from __future__ import annotations

import json
import uuid

import pytest
from onboarding_support import fresh
from sqlalchemy import text

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import OnboardingAIConfiguration
from rai_gateway.onboarding.ai_configuration import AIConfigService
from rai_gateway.onboarding.extraction import current_profile
from rai_gateway.onboarding.preparation import run_pass
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
def apply_env(monkeypatch):
    monkeypatch.setattr(settings, "onboarding_discovery_fixture", True)
    monkeypatch.setattr(settings, "onboarding_governance_fixture", True)
    monkeypatch.setattr(settings, "onboarding_ai_targets_json", json.dumps([TARGET]))
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
        from datetime import datetime, timezone

        ended_at = datetime.now(timezone.utc)

    def _probe(self, _cfg, _fp):
        return _Probe()

    monkeypatch.setattr(AIConfigService, "_matching_probe", _probe)


def _pipeline_session(api, client, session_factory):
    session = api.create().json()
    session = api.text(
        session,
        "system: demo-system, name=Demo\n",
        role="SUPPORTING_MATERIAL",
        title="inventory.txt",
    ).json()["session"]
    session = api.text(
        session,
        "clause: USD 2,000 per calendar month\n",
        role="SUPPORTING_MATERIAL",
    ).json()["session"]
    prep = api.prepare(session)
    assert prep.status_code == 202
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
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
    assert extract.status_code == 202
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
    inv_preview = client.post(
        f"{api.BASE}/{session['id']}/inventory/preview",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_clarification_revision": 0,
            "extraction_run_id": session["extraction"]["run_id"],
        },
        headers=api.headers,
    )
    inv_body = inv_preview.json()
    inv_analyze = client.post(
        f"{api.BASE}/{session['id']}/inventory/analyze",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_clarification_revision": 0,
            "plan_id": inv_body["plan_id"],
            "expected_plan_digest": inv_body["plan_digest"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert inv_analyze.status_code == 202
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
    inventory_set = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets", headers=api.headers
    ).json()["items"][0]["id"]
    gov_preview = client.post(
        f"{api.BASE}/{session['id']}/governance/preview",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_inventory_clarification_revision": 0,
            "requested_governance_clarification_revision": 0,
            "inventory_proposal_set_id": inventory_set,
        },
        headers=api.headers,
    )
    gov_body = gov_preview.json()
    gov_analyze = client.post(
        f"{api.BASE}/{session['id']}/governance/generate",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_inventory_clarification_revision": 0,
            "requested_governance_clarification_revision": 0,
            "plan_id": gov_body["plan_id"],
            "expected_plan_digest": gov_body["plan_digest"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert gov_analyze.status_code == 202
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
    return session, inventory_set


@pytest.mark.usefixtures("apply_env", "ai_ready")
def test_prepare_and_apply_creates_system(api, client, session_factory, db_session):
    session, inventory_set = _pipeline_session(api, client, session_factory)
    proposals = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets/{inventory_set}/proposals",
        headers=api.headers,
    ).json()["items"]
    inv_selection = {p["id"]: "ACCEPT" for p in proposals if p["entity_type"] == "ai_system"}
    assert inv_selection
    before = db_session.execute(text("SELECT count(*) FROM ai_systems")).scalar()
    prepare = client.post(
        f"{api.BASE}/{session['id']}/change-sets/prepare",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "selection": {
                "inventory_proposal_set_id": inventory_set,
                "inventory": inv_selection,
                "governance": {},
            },
        },
        headers=api.headers,
    )
    assert prepare.status_code == 200, prepare.text
    cs = prepare.json()
    assert cs["operations"]
    apply = client.post(
        f"{api.BASE}/{session['id']}/change-sets/{cs['id']}/apply",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "change_set_id": cs["id"],
            "expected_payload_digest": cs["payload_digest"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert apply.status_code == 202, apply.text
    run_pass(session_factory, get_source_store(), worker_id="t")
    after = db_session.execute(text("SELECT count(*) FROM ai_systems")).scalar()
    assert after == before + 1


@pytest.mark.usefixtures("apply_env", "ai_ready")
def test_apply_idempotency(api, client, session_factory, db_session):
    session, inventory_set = _pipeline_session(api, client, session_factory)
    proposals = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets/{inventory_set}/proposals",
        headers=api.headers,
    ).json()["items"]
    pid = next(p["id"] for p in proposals if p["entity_type"] == "ai_system")
    prepare = client.post(
        f"{api.BASE}/{session['id']}/change-sets/prepare",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "selection": {
                "inventory_proposal_set_id": inventory_set,
                "inventory": {pid: "ACCEPT"},
                "governance": {},
            },
        },
        headers=api.headers,
    ).json()
    idem = uuid.uuid4().hex
    body = {
        "expected_row_version": session["row_version"],
        "requested_input_revision": session["input_revision"],
        "change_set_id": prepare["id"],
        "expected_payload_digest": prepare["payload_digest"],
    }
    first = client.post(
        f"{api.BASE}/{session['id']}/change-sets/{prepare['id']}/apply",
        json=body,
        headers={**api.headers, "Idempotency-Key": idem},
    )
    assert first.status_code == 202, first.text
    second = client.post(
        f"{api.BASE}/{session['id']}/change-sets/{prepare['id']}/apply",
        json=body,
        headers={**api.headers, "Idempotency-Key": idem},
    )
    assert second.status_code == 202, second.text
    assert second.json()["replayed"] is True
    assert second.json()["id"] == first.json()["id"]
    run_pass(session_factory, get_source_store(), worker_id="t")
    count = db_session.execute(
        text("SELECT count(*) FROM ai_systems WHERE system_key = 'demo-system'")
    ).scalar()
    assert count == 1


@pytest.mark.usefixtures("apply_env", "ai_ready")
def test_apply_rollback_on_duplicate(api, client, session_factory, db_session):
    session, inventory_set = _pipeline_session(api, client, session_factory)
    proposals = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets/{inventory_set}/proposals",
        headers=api.headers,
    ).json()["items"]
    pid = next(p["id"] for p in proposals if p["entity_type"] == "ai_system")
    prepare = client.post(
        f"{api.BASE}/{session['id']}/change-sets/prepare",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "selection": {
                "inventory_proposal_set_id": inventory_set,
                "inventory": {pid: "ACCEPT"},
                "governance": {},
            },
        },
        headers=api.headers,
    ).json()
    ops = client.get(
        f"{api.BASE}/{session['id']}/change-sets/{prepare['id']}",
        headers=api.headers,
    ).json()["operations"]
    assert any(op["verb"] == "CREATE" for op in ops), "expected CREATE before domain collision"
    db_session.execute(
        text(
            "INSERT INTO ai_systems (id, system_key, name, status, created_by, updated_by) "
            "VALUES (gen_random_uuid(), 'demo-system', 'Existing', 'DRAFT', 'seed', 'seed')"
        )
    )
    db_session.commit()
    before = db_session.execute(text("SELECT count(*) FROM ai_systems")).scalar()
    client.post(
        f"{api.BASE}/{session['id']}/change-sets/{prepare['id']}/apply",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "change_set_id": prepare["id"],
            "expected_payload_digest": prepare["payload_digest"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    run_pass(session_factory, get_source_store(), worker_id="t")
    after = db_session.execute(text("SELECT count(*) FROM ai_systems")).scalar()
    assert after == before
    app_status = db_session.execute(
        text("SELECT status FROM onboarding_applications ORDER BY created_at DESC LIMIT 1")
    ).scalar()
    assert app_status == "FAILED"
