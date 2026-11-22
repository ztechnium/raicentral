"""Three-way reconciliation: baseline snapshot, new source meaning, live domain (Step 08)."""

from __future__ import annotations

from typing import Any

from rai_gateway.onboarding.delta_assertions import AssertionDiff, summarize_diff_counts
from rai_gateway.onboarding.service import canonical_digest_obj


def reconcile_views(
    *,
    baseline_projection: dict[str, Any],
    baseline_digest: str,
    current_projection: dict[str, Any],
    source_diffs: list[AssertionDiff],
) -> dict[str, Any]:
    """Read-only comparison without provider calls."""
    current_digest = canonical_digest_obj(current_projection)
    source_counts = summarize_diff_counts(source_diffs)
    domain_drift = baseline_digest != current_digest
    source_changed = any(d.change_type != "UNCHANGED" for d in source_diffs)
    if not source_changed and not domain_drift:
        situation = "UNCHANGED"
    elif source_changed and not domain_drift:
        situation = "SOURCE_CHANGED_DOMAIN_UNCHANGED"
    elif not source_changed and domain_drift:
        situation = "DOMAIN_DRIFT"
    elif source_changed and domain_drift:
        situation = "BOTH_CHANGED"
    else:
        situation = "UNKNOWN"
    return {
        "situation": situation,
        "baseline_digest": baseline_digest,
        "current_domain_digest": current_digest,
        "source_delta_counts": source_counts,
        "domain_record_count": current_projection.get("record_count", 0),
        "capabilities": {
            "auto_delete_on_withdrawal": False,
            "auto_disable_controls": False,
            "domain_refresh_without_llm": True,
        },
    }
