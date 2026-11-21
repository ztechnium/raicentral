"""Baseline matching for governance proposals (Step 05)."""

from __future__ import annotations

from typing import Any

from rai_gateway.onboarding.governance_capabilities import reconcile_capabilities
from rai_gateway.onboarding.governance_snapshot import GovernanceBaseline

QUESTION_CODES = frozenset(
    {
        "PARAMETER_PATH_UNKNOWN",
        "UNBOUND_SYSTEM",
        "CALENDAR_WINDOW_UNSUPPORTED",
    }
)


def reconcile_governance_proposals(
    proposals: list[dict[str, Any]],
    baseline: GovernanceBaseline,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for idx, proposal in enumerate(proposals):
        pid = proposal.get("id") or f"idx-{idx}"
        generated = proposal.get("generated") or {}
        gaps = generated.get("capability_gaps") or []
        match_state = "Proposed"
        if any(g.get("code") == "MANUAL_PROCESS" for g in gaps):
            match_state = "Manual process"
        matches.append(
            {
                "proposal_id": pid,
                "match_state": match_state,
                "suggested_action": "REVIEW",
                "baseline_digest": baseline.digest,
            }
        )
        for gap in gaps:
            code = gap.get("code")
            if code in QUESTION_CODES:
                questions.append(
                    {
                        "id": f"q-{pid}-{code}",
                        "proposal_id": pid,
                        "code": code,
                        "message": gap.get("message") or code,
                        "owner": "governance",
                    }
                )
    capabilities = reconcile_capabilities(
        [{**p, "id": p.get("id") or f"idx-{i}"} for i, p in enumerate(proposals)]
    )
    return {
        "matching_rule_version": baseline.matching_rule_version,
        "matches": matches,
        "capabilities": capabilities,
        "questions": questions,
    }