"""Integration tests for onboarding governance generation (fixture mode)."""

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
from rai_gateway.onboarding.governance_compiler import compile_clause
from rai_gateway.onboarding.governance_proposals import (
    clauses_from_fixture_partition,
    proposals_from_clauses,
)
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
def governance_env(monkeypatch):
    monkeypatch.setattr(settings, "onboarding_discovery_fixture", True)
    monkeypatch.setattr(settings, "onboarding_governance_fixture", True)
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


def test_egp_refund_preserves_gaps():
    clause = {
        "text": "Refunds above EGP 5,000 require Finance approval",
        "modality": "MUST",
        "systems": [],
    }
    proposals = compile_clause(clause)
    assert proposals
    tool = next(p for p in proposals if p["proposal_family"] == "tool_rule")
    gaps = {g["code"] for g in tool["generated"]["capability_gaps"]}
    assert "PARAMETER_PATH_UNKNOWN" in gaps
    assert tool["generated"]["runtime_verification"] == "NOT_RUN"


def test_calendar_month_budget_not_silent_conversion():
    clause = {"text": "USD 2,000 per calendar month", "modality": "MUST", "systems": []}
    proposals = compile_clause(clause)
    budget = proposals[0]["generated"]
    assert budget["window_intent"] == "calendar_month"
    assert budget["window_seconds"] is None
    assert any(
        g["code"] == "CALENDAR_WINDOW_UNSUPPORTED"
        for g in budget["capability_gaps"]
    )


def test_streaming_redact_marks_unsupported():
    clause = {
        "text": "Redact personal data before any streamed output is delivered",
        "modality": "MUST",
        "systems": [],
    }
    proposals = compile_clause(clause)
    rule = proposals[0]["generated"]
    codes = {g["code"] for g in rule["capability_gaps"]}
    assert "STREAMING_BLOCK_UNSUPPORTED" in codes


def test_sliding_rate_not_budget_monitor():
    clause = {
        "text": "Block the eleventh call in any sliding ten minutes",
        "modality": "MUST",
        "systems": [],
    }
    proposals = compile_clause(clause)
    assert proposals[0]["proposal_family"] == "risk_monitor_rule"
    assert any(
        g["code"] == "RATE_LIMIT_NOT_MONITOR"
        for g in proposals[0]["generated"]["capability_gaps"]
    )


def test_two_systems_duplicate_rules():
    text = (
        "clause: Refunds above EGP 5,000 require Finance approval; "
        "systems=support-a,support-b"
    )
    clauses = clauses_from_fixture_partition(text)
    proposals = proposals_from_clauses(clauses)
    tool_rules = [p for p in proposals if p["proposal_family"] == "tool_rule"]
    keys = {p["generated"].get("system_key") for p in tool_rules}
    assert keys == {"support-a", "support-b"}


@pytest.mark.usefixtures("governance_env", "ai_ready")
def test_governance_fixture_pipeline(api, client, session_factory, db_session):
    session = api.create().json()
    session = api.text(
        session,
        "system: demo-system, name=Demo\n",
        role="SUPPORTING_MATERIAL",
        title="inventory.txt",
    ).json()["session"]
    api.text(
        session,
        "clause: Refunds above EGP 5,000 require Finance approval\n"
        "clause: USD 2,000 per calendar month\n",
        role="SUPPORTING_MATERIAL",
        title="policy.txt",
    )
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
    inv_preview = client.post(
        f"{api.BASE}/{session['id']}/inventory/preview",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_clarification_revision": 0,
            "extraction_run_id": extraction_run_id,
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
    assert inv_analyze.status_code == 202, inv_analyze.text
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
    assert gov_preview.status_code == 200, gov_preview.text
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
    assert gov_analyze.status_code == 202, gov_analyze.text
    run_pass(session_factory, get_source_store(), worker_id="t")
    gsets = client.get(
        f"{api.BASE}/{session['id']}/governance-sets", headers=api.headers
    )
    assert gsets.status_code == 200
    assert len(gsets.json()["items"]) == 1
    set_id = gsets.json()["items"][0]["id"]
    proposals = client.get(
        f"{api.BASE}/{session['id']}/governance-sets/{set_id}/proposals",
        headers=api.headers,
    ).json()["items"]
    assert any(p["proposal_family"] == "runtime_budget" for p in proposals)
    before_sources = db_session.execute(text("SELECT count(*) FROM policy_sources")).scalar()
    after_sources = db_session.execute(text("SELECT count(*) FROM policy_sources")).scalar()
    assert before_sources == after_sources