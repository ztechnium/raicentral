"""Governance proposal schemas and version constants (Step 05)."""

from __future__ import annotations

CAPABILITY_RULE_VERSION = "2026-11-21"
GOVERNANCE_PLAN_VERSION = 1
GOVERNANCE_MATCHING_RULE_VERSION = "2026-11-21"

GOVERNANCE_LIMITS = {
    "max_calls_per_run": 32,
    "max_request_bytes_per_call": 96 * 1024,
    "max_request_bytes_per_run": 2 * 1024 * 1024,
    "max_output_tokens_per_call": 6000,
    "max_response_bytes_per_call": 512 * 1024,
    "max_stored_output_bytes_per_run": 8 * 1024 * 1024,
    "max_clauses": 1000,
    "max_proposals": 500,
    "max_nested_links": 2000,
    "max_questions": 200,
}

PROPOSAL_FAMILIES = frozenset(
    {
        "policy_source",
        "obligation",
        "content_rule",
        "tool_rule",
        "runtime_budget",
        "risk_monitor_rule",
        "risk_assessment",
        "risk_finding",
        "governance_classification",
        "control_assessment",
    }
)

RUNTIME_VERIFICATION = "NOT_RUN"