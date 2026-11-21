"""Pure unit tests for governance compiler and fixture parsing (no database)."""

from rai_gateway.onboarding.governance_capabilities import capability_for_proposal
from rai_gateway.onboarding.governance_compiler import compile_clause
from rai_gateway.onboarding.governance_proposals import (
    clauses_from_fixture_partition,
    proposals_from_clauses,
)


def test_egp_refund_preserves_gaps():
    clause = {
        "text": "Refunds above EGP 5,000 require Finance approval",
        "modality": "MUST",
        "systems": [],
    }
    proposals = compile_clause(clause)
    tool = next(p for p in proposals if p["proposal_family"] == "tool_rule")
    gaps = {g["code"] for g in tool["generated"]["capability_gaps"]}
    assert "PARAMETER_PATH_UNKNOWN" in gaps
    cap = capability_for_proposal(tool)
    assert cap["runtime_verification"] == "NOT_RUN"


def test_calendar_month_budget_not_silent_conversion():
    clause = {"text": "USD 2,000 per calendar month", "modality": "MUST", "systems": []}
    budget = compile_clause(clause)[0]["generated"]
    assert budget["window_intent"] == "calendar_month"
    assert budget["window_seconds"] is None


def test_two_systems_duplicate_rules():
    text = (
        "clause: Refunds above EGP 5,000 require Finance approval; "
        "systems=support-a,support-b"
    )
    proposals = proposals_from_clauses(clauses_from_fixture_partition(text))
    tool_rules = [p for p in proposals if p["proposal_family"] == "tool_rule"]
    keys = {p["generated"].get("system_key") for p in tool_rules}
    assert keys == {"support-a", "support-b"}