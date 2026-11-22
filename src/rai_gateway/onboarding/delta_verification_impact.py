"""Step 07 verification applicability without rewriting historical results (Step 08)."""

from __future__ import annotations

from typing import Any

from rai_gateway.onboarding.delta_assertions import AssertionDiff

APPLICABILITY = frozenset({"CURRENT", "STALE", "REVIEW_REQUIRED", "NOT_APPLICABLE"})


def compute_verification_impacts(
    *,
    dispatches: list[dict[str, Any]],
    material_diffs: list[AssertionDiff],
) -> list[dict[str, Any]]:
    """Mark scenarios stale when material dependency assertions changed."""
    material = {
        d.identity
        for d in material_diffs
        if d.change_type in ("MODIFIED", "ADDED", "WITHDRAWN_IN_SOURCE", "CONFLICT")
    }
    impacts: list[dict[str, Any]] = []
    for ordinal, dispatch in enumerate(dispatches):
        scenario_key = dispatch.get("scenario_key") or f"scenario-{ordinal}"
        target_digest = (dispatch.get("evidence") or {}).get("target_digest") or "unknown"
        if material:
            applicability = "STALE"
            reason = "A governed dependency changed since the recorded verification target"
        else:
            applicability = "CURRENT"
            reason = "No material dependency change detected for this scenario"
        impacts.append(
            {
                "ordinal": ordinal,
                "verification_attempt_id": dispatch.get("attempt_id"),
                "scenario_key": scenario_key,
                "target_digest": target_digest,
                "applicability": applicability,
                "reason": reason,
            }
        )
    return impacts
