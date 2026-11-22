"""Bounded impact graph construction for Step 08 delta analysis."""

from __future__ import annotations

from typing import Any

from rai_gateway.onboarding.delta_assertions import AssertionDiff

MAX_EDGES = 2_000
MAX_DEPTH = 6


def build_impact_edges(
    diffs: list[AssertionDiff],
    *,
    bindings: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Derive typed impact edges from findings and optional applied bindings."""
    bindings = bindings or []
    binding_by_kind: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        kind = binding.get("target_kind") or "unknown"
        binding_by_kind.setdefault(kind, []).append(binding)

    edges: list[dict[str, Any]] = []
    truncated = False
    ordinal = 0
    for finding_ordinal, diff in enumerate(diffs):
        if diff.change_type in ("UNCHANGED",):
            continue
        if ordinal >= MAX_EDGES:
            truncated = True
            break
        priority = "HIGH" if diff.change_type in ("MODIFIED", "CONFLICT") else "MEDIUM"
        subject = (diff.after or diff.before or {}).get("subject") or diff.identity
        edges.append(
            {
                "finding_ordinal": finding_ordinal,
                "ordinal": ordinal,
                "edge_type": "ASSERTION_CHANGE",
                "reference_kind": "assertion",
                "reference_id": diff.identity,
                "direct": True,
                "confidence": "CONFIRMED",
                "reason": f"{diff.change_type}: {diff.reason}",
                "impact_level": priority,
                "subject": subject,
            }
        )
        ordinal += 1
        for binding in binding_by_kind.get("ai_system", [])[:3]:
            if ordinal >= MAX_EDGES:
                truncated = True
                break
            edges.append(
                {
                    "finding_ordinal": finding_ordinal,
                    "ordinal": ordinal,
                    "edge_type": "DOMAIN_BINDING",
                    "reference_kind": binding.get("target_kind", "ai_system"),
                    "reference_id": str(binding.get("target_id")),
                    "direct": False,
                    "confidence": "INFERRED_NEEDS_REVIEW",
                    "reason": "Potential downstream registry impact from assertion change",
                    "impact_level": priority,
                }
            )
            ordinal += 1
    return edges, truncated
