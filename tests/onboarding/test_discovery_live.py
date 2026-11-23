"""Inventory discovery with a real (mocked-transport) model call - not fixture mode."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from onboarding_support import fresh
from sqlalchemy import text

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import (
    OnboardingAIConfiguration,
    OnboardingDiscoveryCall,
    OnboardingInventoryProposal,
)
from rai_gateway.onboarding import ai_adapter, discovery_ai
from rai_gateway.onboarding.ai_configuration import AIConfigService
from rai_gateway.onboarding.extraction import current_profile
from rai_gateway.onboarding.preparation import run_pass
from rai_gateway.onboarding.storage import get_source_store

SECRET = "sk-discovery-secret-123456"
TARGET = {
    "key": "acme-chat",
    "label": "Acme Chat",
    "protocol": "OPENAI_COMPATIBLE_CHAT",
    "base_url": "https://llm.acme.example/v1",
    "models": ["acme-1"],
    "structured_modes": ["JSON_OBJECT"],
    "token_param": "max_tokens",
    "max_output_tokens": 4096,
    "credential_alias": "ACME",
    "credential_version": 1,
    "timeout_seconds": 5,
}
SOURCE = (
    "AI-001 | Support Copilot | answers customer questions | owner: Sara Nabil\n"
    "AI-002 | Fraud Scorer | scores transactions\n"
    "Refunds above EGP 5,000 require Finance approval.\n"
    "Do not disclose customer national IDs in any output.\n"
    "Spend must not exceed USD 2,000 per month.\n"
)


class Model:
    """A scripted OpenAI-style endpoint that records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.reply = self.good

    def good(self, body: dict) -> dict:
        return {
            "proposals": [
                {
                    "entity_type": "ai_system",
                    "key": "AI-001",
                    "name": "Support Copilot",
                    "description": None,
                    "business_purpose": "answers customer questions",
                    "business_unit": None,
                    "owner_name": "Sara Nabil",
                    "status": None,
                    "provider": None,
                    "parent_system_key": None,
                    "evidence": [{"block": "B1", "quote": "Support Copilot"}],
                },
                {
                    "entity_type": "agent",
                    "key": "triage-agent",
                    "name": "Triage agent",
                    "description": None,
                    "business_purpose": None,
                    "business_unit": None,
                    "owner_name": None,
                    "status": None,
                    "provider": None,
                    "parent_system_key": "AI-001",
                    "evidence": [{"block": "B1", "quote": "answers customer questions"}],
                },
                {  # invented: the quote is not in the source, so it must be dropped
                    "entity_type": "ai_system",
                    "key": "ghost",
                    "name": "Ghost",
                    "description": None,
                    "business_purpose": None,
                    "business_unit": None,
                    "owner_name": None,
                    "status": None,
                    "provider": None,
                    "parent_system_key": None,
                    "evidence": [{"block": "B1", "quote": "does not appear anywhere"}],
                },
            ]
        }

    def governance(self, body: dict) -> dict:
        prompt = body["messages"][1]["content"]
        blocks = dict(
            re.findall(r"\[(B\d+)\] \([^\n]*\)\n(.*?)(?=\n\n\[B\d+\]|\Z)", prompt, re.S)
        )

        def label_for(quote):
            return next((k for k, v in blocks.items() if quote in v), "B1")

        def req(quote, **over):
            base = {
                "block": label_for(quote),
                "quote": quote,
                "modality": "MUST",
                "topic": "t",
                "kind": "obligation",
                "title": quote[:40],
                "effect": None,
                "applies_to": None,
                "tool_selector": None,
                "approval_role": None,
                "system_keys": [],
                "budget": None,
            }
            return {**base, **over}

        return {
            "requirements": [
                req(
                    "Refunds above EGP 5,000 require Finance approval",
                    kind="tool_rule",
                    effect="REQUIRE_APPROVAL",
                    tool_selector="refund",
                    approval_role="Finance",
                    system_keys=["ai-001", "not-a-known-system"],
                ),
                req(
                    "Do not disclose customer national IDs in any output",
                    modality="MUST_NOT",
                    kind="content_rule",
                    effect="DENY",
                    applies_to="OUTPUT",
                ),
                req(
                    "Spend must not exceed USD 2,000 per month",
                    kind="runtime_budget",
                    budget={"metric": "COST", "limit_value": 2000, "currency": "USD", "window": "MONTH"},
                ),
                req("This sentence is not in any source", kind="obligation"),
            ]
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "boom"})
        body = json.loads(request.content)
        governance = "extract an AI inventory" not in body["messages"][0]["content"]
        content = json.dumps((self.governance if governance else self.reply)(body))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 40, "total_tokens": 90},
            },
        )


@pytest.fixture()
def model(monkeypatch, db_session):
    m = Model()
    monkeypatch.setattr(settings, "onboarding_discovery_fixture", False)
    monkeypatch.setattr(settings, "onboarding_ai_targets_json", json.dumps([TARGET]))
    monkeypatch.setenv("ONBOARDING_AI_KEY_ACME", SECRET)
    monkeypatch.setattr(ai_adapter, "resolve_host", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(
        ai_adapter, "build_transport", lambda: httpx.MockTransport(m.handler)
    )
    db_session.add(
        OnboardingAIConfiguration(
            singleton_key="default",
            config_version=1,
            enabled=True,
            target_key=TARGET["key"],
            model="acme-1",
            structured_mode="JSON_OBJECT",
            max_output_tokens=256,
            updated_by="test",
        )
    )
    db_session.commit()

    class _Probe:
        outcome = "PASSED"
        ended_at = datetime.now(timezone.utc)

    monkeypatch.setattr(AIConfigService, "_matching_probe", lambda self, c, f: _Probe())
    return m


def analyze(api, client, session_factory):
    session = api.create().json()
    session = api.text(
        session, SOURCE, role="SUPPORTING_MATERIAL", title="inventory.txt"
    ).json()["session"]
    assert api.prepare(session).status_code == 202
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
    assert extract.status_code == 202, extract.text
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
    preview = client.post(
        f"{api.BASE}/{session['id']}/inventory/preview",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "requested_clarification_revision": 0,
            "extraction_run_id": session["extraction"]["run_id"],
        },
        headers=api.headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["fixture_mode"] is False
    started = client.post(
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
    assert started.status_code == 202, started.text
    return session, run_pass(session_factory, get_source_store(), worker_id="t")


def test_live_analysis_publishes_grounded_proposals(api, client, session_factory, db_session, model):
    session, stats = analyze(api, client, session_factory)
    assert stats["succeeded"] >= 1 and stats["failed"] == 0

    # One bounded call, key only in the header, sources inside the prompt.
    assert len(model.requests) == 1
    request = model.requests[0]
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert SECRET.encode() not in request.content
    sent = json.loads(request.content)
    assert "Support Copilot" in sent["messages"][1]["content"]
    assert sent["max_tokens"] == 4096

    db_session.expire_all()
    rows = db_session.query(OnboardingInventoryProposal).all()
    by_key = {
        r.generated["claims"].get("system_key") or r.generated["claims"].get("agent_key"): r
        for r in rows
    }
    assert set(by_key) == {"ai-001", "triage-agent"}  # the ungrounded 'ghost' was dropped
    system = by_key["ai-001"].generated
    assert system["claims"]["name"] == "Support Copilot"
    assert system["claims"]["owner_name"] == "Sara Nabil"
    ev = system["evidence"][0]
    block_text = db_session.execute(
        text("SELECT text FROM onboarding_extracted_blocks WHERE id = :i"),
        {"i": ev["block_id"]},
    ).scalar()
    assert block_text[ev["range"][0] : ev["range"][1]] == "Support Copilot"
    # The agent is linked to its parent system proposal.
    parent = by_key["triage-agent"].generated["relationships"]["ai_system"]["reference"]
    assert parent == {"kind": "PROPOSAL", "proposal_id": str(by_key["ai-001"].id)}

    call = db_session.query(OnboardingDiscoveryCall).one()
    assert call.outcome == "SUCCEEDED" and call.usage["total_tokens"] == 90
    assert call.result["dropped_unverified"] == 1


def test_provider_failure_fails_the_run_without_publishing(
    api, client, session_factory, db_session, model
):
    model.status = 500
    _session, stats = analyze(api, client, session_factory)
    assert stats["failed"] >= 1
    db_session.expire_all()
    assert db_session.query(OnboardingInventoryProposal).count() == 0
    call = db_session.query(OnboardingDiscoveryCall).one()
    assert call.outcome == "FAILED" and call.error_code == "PROVIDER_ERROR"
    error = db_session.execute(
        text(
            "SELECT last_error FROM background_jobs "
            "WHERE job_type = 'ONBOARDING_DISCOVER_INVENTORY' ORDER BY created_at DESC LIMIT 1"
        )
    ).scalar()
    assert error == "AI_PROVIDER_ERROR"
    assert len(model.requests) == 1  # never retried


def test_malformed_reply_fails_the_run(api, client, session_factory, db_session, model):
    model.reply = lambda body: {"nope": True}
    _session, stats = analyze(api, client, session_factory)
    assert stats["failed"] >= 1
    db_session.expire_all()
    call = db_session.query(OnboardingDiscoveryCall).one()
    assert call.error_code == "INVALID_OUTPUT"
    assert db_session.query(OnboardingInventoryProposal).count() == 0


# ------------------------------------------------------------------ pure parsing
def _blocks():
    return [{"label": "B1", "block_id": "blk-1", "text": "Alpha bot (AI-9) reads the CRM."}]


def test_parse_requires_verbatim_evidence_and_normalizes_keys():
    reply = json.dumps(
        {
            "proposals": [
                {
                    "entity_type": "data_source",
                    "key": "  The CRM  ",
                    "name": None,
                    "evidence": [{"block": "[B1]", "quote": "the CRM"}],
                },
                {"entity_type": "tool", "key": "x", "evidence": [{"block": "B9", "quote": "x"}]},
                {"entity_type": "nonsense", "key": "y", "evidence": []},
            ]
        }
    )
    result = discovery_ai.parse_partition("```json\n" + reply + "\n```", _blocks())
    assert result.dropped_unverified == 1 and result.dropped_invalid == 1
    (item,) = result.proposals
    assert item["generated"]["claims"] == {"data_source_key": "the-crm", "scope": "ORGANIZATION"}
    assert item["generated"]["evidence"][0]["range"] == [23, 30]


def test_parse_rejects_non_object_replies():
    for bad in ("not json", "[]", '{"proposals": "x"}'):
        with pytest.raises(discovery_ai.InvalidModelOutput):
            discovery_ai.parse_partition(bad, _blocks())


def test_merge_pools_evidence_and_fills_missing_claims():
    def proposal(**claims):
        return {
            "entity_type": "ai_system",
            "generated": {
                "existence_assertion": "REPORTED_EXISTING",
                "claims": {"system_key": "a", "scope": "ORGANIZATION", **claims},
                "relationships": {},
                "evidence": [{"basis": "SOURCE_STATED", "block_id": claims.get("name", "n"), "range": [0, 1]}],
            },
        }

    (merged,) = discovery_ai.merge_proposals(
        [[proposal(name="First")], [proposal(name="Second", description="d")]]
    )
    claims = merged["generated"]["claims"]
    assert claims["name"] == "First" and claims["description"] == "d"
    assert len(merged["generated"]["evidence"]) == 2


def test_new_agent_with_a_taken_key_is_rekeyed_under_its_system():
    def gen(key, parent_ref):
        return {
            "claims": {"agent_key": key, "name": key},
            "relationships": {"ai_system": {"claimed_key": "support-concierge", "reference": parent_ref}},
        }

    existing = {"support-agent": {"ai_system_id": "sys-old"}, "billing-agent": {"ai_system_id": "sys-b"}}
    rows = [
        ("a1", "agent", gen("support-agent", {"kind": "PROPOSAL", "proposal_id": "p-new"})),
        ("a2", "agent", gen("billing-agent", {"kind": "EXISTING", "record_id": "sys-b"})),
        ("a3", "agent", gen("fresh-agent", {"kind": "PROPOSAL", "proposal_id": "p-new"})),
        ("s1", "ai_system", {"claims": {"system_key": "support-concierge"}, "relationships": {}}),
    ]
    out = discovery_ai.deconflict_agent_keys(rows, existing)
    assert set(out) == {"a1"}  # the genuine match and the free key are untouched
    assert out["a1"]["claims"]["agent_key"] == "support-concierge-support-agent"
    assert out["a1"]["claims"]["requested_key"] == "support-agent"


def test_agent_of_an_already_registered_system_is_not_rekeyed():
    proposals = [
        ("s1", "ai_system", {"claims": {"system_key": "invoice-ai"}, "relationships": {}}),
        (
            "a1",
            "agent",
            {
                "claims": {"agent_key": "reviewer"},
                "relationships": {"ai_system": {"claimed_key": "invoice-ai"}},
            },
        ),
    ]
    linked = discovery_ai.link_parents(proposals, {"invoice-ai": "sys-1"})
    assert linked["a1"]["ai_system"]["reference"] == {"kind": "EXISTING", "record_id": "sys-1"}
    generated = {**proposals[1][2], "relationships": linked["a1"]}
    # Same system already owns the key: this is the existing agent, not a new one.
    assert (
        discovery_ai.deconflict_agent_keys(
            [("a1", "agent", generated)], {"reviewer": {"ai_system_id": "sys-1"}}
        )
        == {}
    )


def test_live_governance_extracts_grounded_disabled_proposals(
    api, client, session_factory, db_session, model
):
    session, _ = analyze(api, client, session_factory)
    session = fresh(api, session)
    inventory_set = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets", headers=api.headers
    ).json()["items"][0]["id"]
    body = {
        "expected_row_version": session["row_version"],
        "requested_input_revision": session["input_revision"],
        "requested_inventory_clarification_revision": 0,
        "requested_governance_clarification_revision": 0,
    }
    preview = client.post(
        f"{api.BASE}/{session['id']}/governance/preview",
        json={**body, "inventory_proposal_set_id": inventory_set},
        headers=api.headers,
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["fixture_mode"] is False
    started = client.post(
        f"{api.BASE}/{session['id']}/governance/generate",
        json={**body, "plan_id": plan["plan_id"], "expected_plan_digest": plan["plan_digest"]},
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert started.status_code == 202, started.text
    stats = run_pass(session_factory, get_source_store(), worker_id="t")
    if stats["failed"]:
        err = db_session.execute(
            text("SELECT last_error FROM background_jobs WHERE job_type = 'ONBOARDING_GENERATE_GOVERNANCE' ORDER BY created_at DESC LIMIT 1")
        ).scalar()
        raise AssertionError(f"governance job failed: {err}")

    governance_requests = [
        r for r in model.requests if "extract an AI inventory" not in json.loads(r.content)["messages"][0]["content"]
    ]
    assert len(governance_requests) == 1
    prompt = json.loads(governance_requests[0].content)["messages"][1]["content"]
    assert "- ai-001: Support Copilot" in prompt  # known systems are offered for binding

    set_id = client.get(
        f"{api.BASE}/{session['id']}/governance-sets", headers=api.headers
    ).json()["items"][0]["id"]
    listing = client.get(
        f"{api.BASE}/{session['id']}/governance-sets/{set_id}/proposals", headers=api.headers
    )
    items = listing.json()["items"]
    counts = db_session.execute(
        text("select (select count(*) from onboarding_governance_proposals), (select count(*) from onboarding_governance_clauses), (select result::text from onboarding_governance_calls limit 1)")
    ).one()
    assert items, (listing.text[:300], tuple(counts))
    by_family = {p["proposal_family"]: p["generated"] for p in items}
    assert set(by_family) == {"tool_rule", "content_rule", "runtime_budget"}
    assert all(p["generated"]["enabled"] is False for p in items)  # nothing starts enabled

    tool = by_family["tool_rule"]
    assert tool["system_key"] == "ai-001" and tool["binding"] == "bound"  # unknown key ignored
    assert tool["effect"] == "REQUIRE_APPROVAL" and tool["approval_role"] == "Finance"
    content = by_family["content_rule"]
    assert content["applies_to"] == "OUTPUT" and content["binding"] == "unbound"
    assert "UNBOUND_SYSTEM" in {g["code"] for g in content["capability_gaps"]}
    budget = by_family["runtime_budget"]
    assert budget["limit_value"] == 2000.0 and budget["window_seconds"] == 30 * 86400
    assert budget["metric"] == "COST" and "approx. rolling 30 days" in budget["title"]
    codes = {g["code"] for g in budget["capability_gaps"]}
    assert {"CALENDAR_WINDOW_UNSUPPORTED", "UNIT_OR_CURRENCY_UNRESOLVED"} <= codes

    db_session.expire_all()
    from rai_gateway.models.onboarding import OnboardingGovernanceCall, OnboardingGovernanceClause

    call = db_session.query(OnboardingGovernanceCall).one()
    assert call.outcome == "SUCCEEDED" and call.result["dropped_unverified"] == 1
    assert db_session.query(OnboardingGovernanceClause).count() == 3  # the invented one is gone


def _labels(body: dict) -> dict:
    prompt = body["messages"][1]["content"]
    return dict(re.findall(r"\[(B\d+)\] \([^\n]*\)\n(.*?)(?=\n\n\[B\d+\]|\Z)", prompt, re.S))


def _item(quote, labels, **over):
    base = {
        "block": next((k for k, v in labels.items() if quote in v), "B1"),
        "quote": quote,
        "modality": "MUST",
        "topic": "t",
        "kind": "obligation",
        "title": quote[:40],
        "effect": None,
        "applies_to": None,
        "tool_selector": None,
        "approval_role": None,
        "system_keys": [],
        "budget": None,
    }
    return {**base, **over}


def test_apply_creates_resources_tool_rules_and_budgets_disabled(
    api, client, session_factory, db_session, model
):
    def inventory(body):
        labels = _labels(body)

        def entity(kind, key, name, quote):
            return {
                "entity_type": kind,
                "key": key,
                "name": name,
                "description": None,
                "business_purpose": None,
                "business_unit": None,
                "owner_name": None,
                "status": None,
                "provider": None,
                "parent_system_key": None,
                "evidence": [
                    {"block": next((k for k, v in labels.items() if quote in v), "B1"), "quote": quote}
                ],
            }

        return {
            "proposals": [
                entity("ai_system", "AI-001", "Support Copilot", "Support Copilot"),
                entity("tool", "refund-api", "Refund API", "Refunds above EGP 5,000"),
                entity("data_source", "crm", "CRM", "answers customer questions"),
            ]
        }

    def governance(body):
        labels = _labels(body)
        return {
            "requirements": [
                _item(
                    "Refunds above EGP 5,000 require Finance approval",
                    labels,
                    kind="tool_rule",
                    effect="REQUIRE_APPROVAL",
                    tool_selector="refund",
                    approval_role="Finance",
                    system_keys=["ai-001"],
                ),
                _item(
                    "Spend must not exceed USD 2,000 per month",
                    labels,
                    kind="runtime_budget",
                    system_keys=["ai-001"],
                    budget={"metric": "COST", "limit_value": 2000, "currency": "USD", "window": "MONTH"},
                ),
                _item(  # not tied to a system: must be reported, never guessed
                    "Do not disclose customer national IDs in any output",
                    labels,
                    kind="content_rule",
                    effect="DENY",
                    applies_to="OUTPUT",
                ),
            ]
        }

    model.reply, model.governance = inventory, governance
    session, _ = analyze(api, client, session_factory)
    session = fresh(api, session)
    inventory_set = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets", headers=api.headers
    ).json()["items"][0]["id"]
    body = {
        "expected_row_version": session["row_version"],
        "requested_input_revision": session["input_revision"],
        "requested_inventory_clarification_revision": 0,
        "requested_governance_clarification_revision": 0,
    }
    plan = client.post(
        f"{api.BASE}/{session['id']}/governance/preview",
        json={**body, "inventory_proposal_set_id": inventory_set},
        headers=api.headers,
    ).json()
    assert client.post(
        f"{api.BASE}/{session['id']}/governance/generate",
        json={**body, "plan_id": plan["plan_id"], "expected_plan_digest": plan["plan_digest"]},
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    ).status_code == 202
    run_pass(session_factory, get_source_store(), worker_id="t")
    session = fresh(api, session)
    gov_set = client.get(
        f"{api.BASE}/{session['id']}/governance-sets", headers=api.headers
    ).json()["items"][0]["id"]

    inv_items = client.get(
        f"{api.BASE}/{session['id']}/proposal-sets/{inventory_set}/proposals", headers=api.headers
    ).json()["items"]
    gov_items = client.get(
        f"{api.BASE}/{session['id']}/governance-sets/{gov_set}/proposals", headers=api.headers
    ).json()["items"]
    prepared = client.post(
        f"{api.BASE}/{session['id']}/change-sets/prepare",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "selection": {
                "inventory_proposal_set_id": inventory_set,
                "governance_proposal_set_id": gov_set,
                "inventory": {p["id"]: "ACCEPT" for p in inv_items},
                "governance": {p["id"]: "ACCEPT" for p in gov_items},
            },
        },
        headers=api.headers,
    )
    assert prepared.status_code == 200, prepared.text
    cs = prepared.json()
    kinds = [(o["verb"], o["target_kind"]) for o in cs["operations"]]
    assert ("CREATE", "tool") in kinds and ("CREATE", "tool_rule") in kinds
    assert ("CREATE", "runtime_budget") in kinds and ("CREATE", "data_source") in kinds
    skipped = {s["family"]: s["code"] for s in cs["skipped"]}
    assert skipped == {"content_rule": "UNBOUND_SYSTEM"}  # explained, not silently dropped

    applied = client.post(
        f"{api.BASE}/{session['id']}/change-sets/{cs['id']}/apply",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "change_set_id": cs["id"],
            "expected_payload_digest": cs["payload_digest"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert applied.status_code == 202, applied.text
    stats = run_pass(session_factory, get_source_store(), worker_id="t")
    assert stats["failed"] == 0, stats

    db_session.expire_all()
    row = lambda sql: db_session.execute(text(sql)).mappings().all()  # noqa: E731
    (system,) = row("select id from ai_systems where system_key = 'ai-001'")
    (tool,) = row("select id, scope, status from tool_resources where tool_key = 'refund-api'")
    assert tool["scope"] == "ORGANIZATION"
    assert row("select 1 from data_sources where data_source_key = 'crm'")
    (rule,) = row(
        "select tool_id, enabled, effect, required_approver_role from tool_rules "
        "where title like 'Refunds above%'"
    )
    assert rule["tool_id"] == tool["id"] and rule["enabled"] is False
    assert rule["effect"] == "REQUIRE_APPROVAL" and rule["required_approver_role"] == "Finance"
    (budget,) = row(
        "select ai_system_id, metric, window_seconds, limit_value, enabled, effect_on_exceed "
        "from runtime_budgets where name like 'Spend must not exceed%'"
    )
    assert budget["ai_system_id"] == system["id"] and budget["enabled"] is False
    assert budget["metric"] == "COST" and budget["window_seconds"] == 30 * 86400
    assert budget["limit_value"] == 2000.0 and budget["effect_on_exceed"] == "DENY"

    # A second review of the same proposals must reuse what the first apply created: no
    # duplicate keys, no duplicate budget, and the tool rule still resolves its tool.
    session = fresh(api, session)
    again = client.post(
        f"{api.BASE}/{session['id']}/change-sets/prepare",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "selection": {
                "inventory_proposal_set_id": inventory_set,
                "governance_proposal_set_id": gov_set,
                "inventory": {p["id"]: "ACCEPT" for p in inv_items},
                "governance": {p["id"]: "ACCEPT" for p in gov_items},
            },
        },
        headers=api.headers,
    )
    assert again.status_code == 200, again.text
    cs2 = again.json()
    assert {o["verb"] for o in cs2["operations"]} == {"REUSE"}, cs2["operations"]
    assert {s["code"] for s in cs2["skipped"]} == {"UNBOUND_SYSTEM"}  # not "ambiguous tool"
    applied2 = client.post(
        f"{api.BASE}/{session['id']}/change-sets/{cs2['id']}/apply",
        json={
            "expected_row_version": session["row_version"],
            "requested_input_revision": session["input_revision"],
            "change_set_id": cs2["id"],
            "expected_payload_digest": cs2["payload_digest"],
        },
        headers={**api.headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert applied2.status_code == 202, applied2.text
    assert run_pass(session_factory, get_source_store(), worker_id="t")["failed"] == 0
    db_session.expire_all()
    assert len(row("select 1 from tool_rules where title like 'Refunds above%'")) == 1
    assert len(row("select 1 from runtime_budgets where name like 'Spend must not exceed%'")) == 1
    assert len(row("select 1 from tool_resources where tool_key = 'refund-api'")) == 1
