"""Unit tests for deterministic inventory reconciliation (Step 04)."""

from __future__ import annotations

from rai_gateway.onboarding.reconciliation import match_proposal, reconcile_proposals
from rai_gateway.onboarding.registry_snapshot import MATCHING_RULE_VERSION, RegistrySnapshot


def _empty_snapshot() -> RegistrySnapshot:
    projection = {
        "ai_systems": [],
        "agents": [],
        "agent_versions": [],
        "models": [],
        "tools": [],
        "mcp_servers": [],
        "external_services": [],
        "data_sources": [],
        "dependencies": [],
        "indexes": {
            "system_by_key": {},
            "agent_by_key": {},
            "tool_by_scope_key": {},
            "mcp_by_scope_key": {},
            "service_by_scope_key": {},
            "data_by_scope_key": {},
        },
    }
    return RegistrySnapshot(
        captured_at="2026-11-21T00:00:00+00:00",
        matching_rule_version=MATCHING_RULE_VERSION,
        digest="a" * 64,
        projection=projection,
        record_count=0,
    )


def test_system_only_proposal_suggests_create():
    snapshot = _empty_snapshot()
    proposal = {
        "id": "p1",
        "entity_type": "ai_system",
        "generated": {
            "claims": {"system_key": "support-ai", "name": "Support AI"},
            "relationships": {},
        },
    }
    result = match_proposal(proposal, snapshot)
    assert result["match_state"] == "Proposed"
    assert result["suggested_action"] == "CREATE"


def test_exact_system_key_matches():
    snapshot = _empty_snapshot()
    snapshot.projection["ai_systems"].append(
        {
            "id": "sys-1",
            "system_key": "support-ai",
            "name": "Support",
            "digest": "d1",
        }
    )
    snapshot.projection["indexes"]["system_by_key"] = {"support-ai": "sys-1"}
    proposal = {
        "id": "p1",
        "entity_type": "ai_system",
        "generated": {"claims": {"system_key": "support-ai", "name": "Support AI"}},
    }
    result = match_proposal(proposal, snapshot)
    assert result["match_state"] == "Matched"
    assert result["candidates"][0]["record_id"] == "sys-1"


def test_agent_parent_conflict():
    snapshot = _empty_snapshot()
    snapshot.projection["agents"].append(
        {
            "id": "ag-1",
            "agent_key": "helper",
            "ai_system_id": "finance-sys",
            "name": "Helper",
            "digest": "d2",
        }
    )
    snapshot.projection["indexes"]["agent_by_key"] = {
        "helper": snapshot.projection["agents"][0]
    }
    proposal = {
        "id": "p2",
        "entity_type": "agent",
        "generated": {
            "claims": {"agent_key": "helper", "name": "Helper"},
            "relationships": {
                "ai_system": {
                    "reference": {"kind": "EXISTING", "record_id": "support-sys"}
                }
            },
        },
    }
    result = match_proposal(proposal, snapshot)
    assert result["match_state"] == "Needs clarification"
    assert any(b["code"] == "PARENT_CONFLICT" for b in result["blockers"])


def test_ambiguous_model_tuple():
    snapshot = _empty_snapshot()
    snapshot.projection["models"].extend(
        [
            {
                "id": "m1",
                "provider": "openai",
                "model_name": "gpt-4",
                "digest": "x",
            },
            {
                "id": "m2",
                "provider": "openai",
                "model_name": "gpt-4",
                "digest": "y",
            },
        ]
    )
    proposal = {
        "id": "p3",
        "entity_type": "model",
        "generated": {
            "claims": {"provider": "openai", "model_name": "gpt-4"},
        },
    }
    recon = reconcile_proposals([proposal], snapshot)
    assert recon["matches"][0]["match_state"] == "Needs clarification"