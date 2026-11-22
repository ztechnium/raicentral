"""Static capability analysis for governance proposals (Step 05)."""

from __future__ import annotations

from typing import Any

from rai_gateway.onboarding.governance_schemas import RUNTIME_VERIFICATION

_BLOCKER_CODES = frozenset(
    {
        "UNBOUND_SYSTEM",
        "PARAMETER_PATH_UNKNOWN",
        "UNIT_OR_CURRENCY_UNRESOLVED",
        "CALENDAR_WINDOW_UNSUPPORTED",
        "NESTED_LOGIC_UNSUPPORTED",
        "CONTENT_APPROVAL_UNSUPPORTED",
        "STREAMING_BLOCK_UNSUPPORTED",
        "EVALUATOR_NOT_INSPECTED",
        "TRACEABILITY_MODEL_GAP",
        "UNSUPPORTED_UPDATE",
        "SCOPE_GAP_AGENT_VERSION",
        "RATE_LIMIT_NOT_MONITOR",
        "MANUAL_PROCESS",
    }
)


def capability_for_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """Return capability dimensions for a compiled proposal."""
    family = proposal.get("proposal_family") or proposal.get("entity_type")
    generated = proposal.get("generated") or {}
    gaps = list(generated.get("capability_gaps") or [])
    blockers = [g.get("code") for g in gaps if isinstance(g, dict)]
    representation = generated.get("representation") or "REPRESENTABLE"
    execution = generated.get("execution_support") or "UNKNOWN"
    if blockers:
        if "MANUAL_PROCESS" in blockers:
            execution = "MANUAL_PROCESS"
        elif any(
            c in blockers
            for c in (
                "STREAMING_BLOCK_UNSUPPORTED",
                "CONTENT_APPROVAL_UNSUPPORTED",
                "CALENDAR_WINDOW_UNSUPPORTED",
                "RATE_LIMIT_NOT_MONITOR",
            )
        ):
            execution = "UNSUPPORTED"
        elif representation == "APPLICATION_SERVICE_GAP":
            execution = "UNKNOWN"
    binding = generated.get("binding") or "unbound"
    return {
        "evidence_basis": generated.get("interpretation") or "SOURCE_STATED",
        "binding": binding,
        "representation": representation,
        "execution_support": execution,
        "configuration_prerequisites": generated.get("prerequisites") or [],
        "runtime_verification": RUNTIME_VERIFICATION,
        "blockers": [c for c in blockers if c in _BLOCKER_CODES],
    }


def reconcile_capabilities(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in proposals:
        pid = item.get("id") or item.get("proposal_id")
        out.append(
            {
                "proposal_id": pid,
                **capability_for_proposal(item),
            }
        )
    return out