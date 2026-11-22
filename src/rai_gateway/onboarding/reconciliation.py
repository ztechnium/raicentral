"""Deterministic registry matching for onboarding inventory proposals (Step 04)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from rai_gateway.onboarding.registry_snapshot import (
    MATCHING_RULE_VERSION,
    RegistrySnapshot,
    resolve_claimed_registry_id,
)

RESOURCE_TYPES = frozenset(
    {"model", "tool", "mcp_server", "external_service", "data_source"}
)

SCOPE_KEY_TYPES = {
    "tool": ("tools", "tool_key"),
    "mcp_server": ("mcp_servers", "server_key"),
    "external_service": ("external_services", "service_key"),
    "data_source": ("data_sources", "data_source_key"),
}


def _norm_key(value: str | None) -> str | None:
    if value is None:
        return None
    return unicodedata.normalize("NFC", value).casefold()


def _claims(proposal: dict[str, Any]) -> dict[str, Any]:
    generated = proposal.get("generated") or {}
    return generated.get("claims") or {}


def _parent_system_id(generated: dict[str, Any]) -> str | None:
    rel = generated.get("relationships") or {}
    parent = rel.get("ai_system") or {}
    ref = parent.get("reference") or {}
    if ref.get("kind") == "PROPOSAL":
        return ref.get("proposal_id")
    if ref.get("kind") == "EXISTING":
        return ref.get("record_id")
    return None


def match_proposal(
    proposal: dict[str, Any], snapshot: RegistrySnapshot
) -> dict[str, Any]:
    """Return match_state, candidates, field_diffs, suggested_action, blockers."""
    entity_type = proposal["entity_type"]
    generated = proposal.get("generated") or {}
    claims = _claims(proposal)
    blockers: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    claimed_id = claims.get("claimed_registry_reference")
    if isinstance(claimed_id, str) and claimed_id.strip():
        row = resolve_claimed_registry_id(
            snapshot, entity_type.replace("dependency", "agent_version"), claimed_id
        )
        if row:
            candidates.append(
                {
                    "match_kind": "EXACT_REFERENCE",
                    "record_id": row["id"],
                    "digest": row.get("digest"),
                    "reasons": ["UUID reference resolved in snapshot"],
                }
            )
        else:
            blockers.append(
                {
                    "code": "UNRESOLVED_REGISTRY_REFERENCE",
                    "message": "Claimed registry identifier not found in snapshot",
                }
            )

    if entity_type == "ai_system":
        key = claims.get("system_key")
        if isinstance(key, str):
            rid = snapshot.projection["indexes"]["system_by_key"].get(key)
            if rid:
                row = next(
                    r for r in snapshot.projection["ai_systems"] if r["id"] == rid
                )
                candidates.append(
                    {
                        "match_kind": "EXACT_KEY",
                        "record_id": rid,
                        "digest": row.get("digest"),
                        "reasons": [f"Exact system_key '{key}'"],
                    }
                )
            elif key and not candidates:
                return _suggest_create("CREATE", claims, blockers, candidates)

    elif entity_type == "agent":
        key = claims.get("agent_key")
        expected_parent = _parent_system_id(generated)
        if isinstance(key, str):
            agent_row = snapshot.projection["indexes"]["agent_by_key"].get(key)
            if agent_row:
                parent_conflict = (
                    expected_parent is not None
                    and expected_parent != agent_row["ai_system_id"]
                )
                if parent_conflict:
                    blockers.append(
                        {
                            "code": "PARENT_CONFLICT",
                            "message": "Agent key exists under a different AI system",
                        }
                    )
                candidates.append(
                    {
                        "match_kind": "EXACT_KEY",
                        "record_id": agent_row["id"],
                        "digest": agent_row.get("digest"),
                        "reasons": [f"Exact agent_key '{key}'"],
                        "parent_conflict": parent_conflict,
                    }
                )
            elif key and not blockers:
                return _suggest_create("CREATE", claims, blockers, candidates)

    elif entity_type in SCOPE_KEY_TYPES:
        table_key, field = SCOPE_KEY_TYPES[entity_type]
        scope = claims.get("scope") or "ORGANIZATION"
        key = claims.get(field.replace("_key", "_key")) or claims.get(
            "tool_key"
        ) or claims.get("server_key") or claims.get("service_key") or claims.get(
            "data_source_key"
        )
        if isinstance(key, str):
            idx = f"{scope}:{key}"
            rid = snapshot.projection["indexes"].get(
                {
                    "tool": "tool_by_scope_key",
                    "mcp_server": "mcp_by_scope_key",
                    "external_service": "service_by_scope_key",
                    "data_source": "data_by_scope_key",
                }[entity_type],
                {},
            ).get(idx)
            if rid:
                row = next(
                    r for r in snapshot.projection[table_key] if r["id"] == rid
                )
                candidates.append(
                    {
                        "match_kind": "EXACT_SCOPE_KEY",
                        "record_id": rid,
                        "digest": row.get("digest"),
                        "reasons": [f"Exact ({entity_type}, {scope}, {key})"],
                    }
                )
            elif scope == "ORGANIZATION" and key:
                platform_idx = f"PLATFORM:{key}"
                pid = snapshot.projection["indexes"].get(
                    {
                        "tool": "tool_by_scope_key",
                        "mcp_server": "mcp_by_scope_key",
                        "external_service": "service_by_scope_key",
                        "data_source": "data_by_scope_key",
                    }[entity_type],
                    {},
                ).get(platform_idx)
                if pid:
                    row = next(
                        r for r in snapshot.projection[table_key] if r["id"] == pid
                    )
                    candidates.append(
                        {
                            "match_kind": "PLATFORM_REUSE_CANDIDATE",
                            "record_id": pid,
                            "digest": row.get("digest"),
                            "reasons": ["Matching PLATFORM scope key (reuse only)"],
                        }
                    )
                else:
                    return _suggest_create("CREATE", claims, blockers, candidates)

    elif entity_type == "model":
        provider = claims.get("provider")
        model_name = claims.get("model_name")
        if provider and model_name:
            matches = [
                r
                for r in snapshot.projection["models"]
                if r["provider"] == provider and r["model_name"] == model_name
            ]
            for row in matches[:5]:
                candidates.append(
                    {
                        "match_kind": "MODEL_TUPLE",
                        "record_id": row["id"],
                        "digest": row.get("digest"),
                        "reasons": ["Provider and model_name match"],
                    }
                )
            if len(matches) > 5:
                candidates.append(
                    {
                        "match_kind": "OVERFLOW",
                        "overflow_count": len(matches) - 5,
                        "reasons": ["Additional model rows not shown"],
                    }
                )
            if len(matches) > 1:
                blockers.append(
                    {
                        "code": "AMBIGUOUS_MODEL",
                        "message": "Multiple registry models share provider/name",
                    }
                )
            elif len(matches) == 0 and not candidates:
                return _suggest_create("CREATE", claims, blockers, candidates)

    name = claims.get("name")
    if not candidates and isinstance(name, str) and name.strip():
        candidates.append(
            {
                "match_kind": "POSSIBLE_NAME",
                "record_id": None,
                "reasons": [f"Similar name '{name}' requires review"],
            }
        )

    match_state = _resolve_match_state(candidates, blockers)
    field_diffs = _field_diffs(entity_type, claims, candidates, snapshot)
    return {
        "proposal_id": proposal.get("id"),
        "match_state": match_state,
        "candidates": candidates[:5],
        "candidate_count": len(candidates),
        "field_diffs": field_diffs,
        "suggested_action": _suggested_action(match_state, candidates),
        "blockers": blockers,
    }


def _resolve_match_state(
    candidates: list[dict[str, Any]], blockers: list[dict[str, Any]]
) -> str:
    if any(b.get("code") == "PARENT_CONFLICT" for b in blockers):
        return "Needs clarification"
    exact = [
        c
        for c in candidates
        if c.get("match_kind")
        in ("EXACT_REFERENCE", "EXACT_KEY", "EXACT_SCOPE_KEY", "MODEL_TUPLE")
        and c.get("record_id")
    ]
    if len(exact) == 1 and not blockers:
        return "Matched"
    if len(exact) > 1 or any(b.get("code") == "AMBIGUOUS_MODEL" for b in blockers):
        return "Needs clarification"
    if exact:
        return "Proposed"
    if blockers:
        return "Needs clarification"
    return "Proposed"


def _suggested_action(
    match_state: str, candidates: list[dict[str, Any]]
) -> str:
    if match_state == "Matched":
        return "REUSE"
    if any(c.get("match_kind") == "PLATFORM_REUSE_CANDIDATE" for c in candidates):
        return "REUSE"
    return "CREATE"


def _suggest_create(
    action: str,
    claims: dict[str, Any],
    blockers: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not claims.get("system_key") and not claims.get("agent_key") and not claims.get(
        "name"
    ):
        blockers.append(
            {
                "code": "MISSING_IDENTITY",
                "message": "Insufficient identity to propose creation",
            }
        )
    return {
        "proposal_id": None,
        "match_state": "Proposed",
        "candidates": candidates,
        "candidate_count": len(candidates),
        "field_diffs": [],
        "suggested_action": action,
        "blockers": blockers,
    }


def _field_diffs(
    entity_type: str,
    claims: dict[str, Any],
    candidates: list[dict[str, Any]],
    snapshot: RegistrySnapshot,
) -> list[dict[str, Any]]:
    if not candidates or not candidates[0].get("record_id"):
        return []
    record_id = candidates[0]["record_id"]
    table = {
        "ai_system": "ai_systems",
        "agent": "agents",
        "model": "models",
        "tool": "tools",
        "mcp_server": "mcp_servers",
        "external_service": "external_services",
        "data_source": "data_sources",
    }.get(entity_type)
    if table is None:
        return []
    row = next(
        (r for r in snapshot.projection[table] if r["id"] == record_id), None
    )
    if row is None:
        return []
    diffs: list[dict[str, Any]] = []
    for field, proposed in claims.items():
        if field in ("claimed_registry_reference",):
            continue
        current = row.get(field)
        if proposed is None:
            continue
        if current != proposed:
            capability = None
            if entity_type in ("external_service", "data_source"):
                capability = "UNSUPPORTED_FIELD_UPDATE"
            if field in ("agent_key", "ai_system_id", "system_key"):
                capability = "UNSUPPORTED_FIELD_UPDATE"
            diffs.append(
                {
                    "field": field,
                    "current": current,
                    "proposed": proposed,
                    "operation": "set",
                    "capability": capability,
                }
            )
    return diffs


def reconcile_proposals(
    proposals: list[dict[str, Any]], snapshot: RegistrySnapshot
) -> dict[str, Any]:
    matches = [match_proposal(p, snapshot) for p in proposals]
    questions: list[dict[str, Any]] = []
    for entry in matches:
        pid = entry.get("proposal_id")
        for blocker in entry.get("blockers") or []:
            if blocker.get("code") in (
                "PARENT_CONFLICT",
                "AMBIGUOUS_MODEL",
                "UNRESOLVED_REGISTRY_REFERENCE",
            ):
                questions.append(
                    {
                        "id": f"{pid}:{blocker['code']}",
                        "proposal_id": pid,
                        "code": blocker["code"],
                        "message": blocker["message"],
                        "answer_types": ["choice", "free_text", "unknown"],
                    }
                )
    return {
        "matching_rule_version": MATCHING_RULE_VERSION,
        "registry_digest": snapshot.digest,
        "matches": matches,
        "questions": questions[:200],
    }


INVENTORY_LINE = re.compile(
    r"^(?P<type>system|agent|tool|model|mcp|service|data)\s*:\s*(?P<key>[^\s,]+)\s*(?:,\s*name=(?P<name>[^,]+))?",
    re.IGNORECASE,
)


def proposals_from_fixture_partition(text: str) -> list[dict[str, Any]]:
    """Deterministic test/partition parser: lines like ``system: support-ai, name=Support``."""
    out: list[dict[str, Any]] = []
    type_map = {
        "system": "ai_system",
        "agent": "agent",
        "tool": "tool",
        "model": "model",
        "mcp": "mcp_server",
        "service": "external_service",
        "data": "data_source",
    }
    key_field = {
        "ai_system": "system_key",
        "agent": "agent_key",
        "tool": "tool_key",
        "model": "model_name",
        "mcp_server": "server_key",
        "external_service": "service_key",
        "data_source": "data_source_key",
    }
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = INVENTORY_LINE.match(line)
        if not m:
            continue
        entity = type_map[m.group("type").lower()]
        key = m.group("key")
        claims: dict[str, Any] = {key_field[entity]: key, "scope": "ORGANIZATION"}
        if entity == "model":
            claims["provider"] = "fixture"
            claims["model_name"] = key
            del claims["scope"]
        if m.group("name"):
            claims["name"] = m.group("name").strip()
        elif entity == "ai_system":
            claims["name"] = key
        out.append(
            {
                "entity_type": entity,
                "generated": {
                    "existence_assertion": "REPORTED_EXISTING",
                    "claims": claims,
                    "relationships": {},
                    "evidence": [
                        {
                            "basis": "SOURCE_STATED",
                            "block_id": None,
                            "range": [0, len(line)],
                        }
                    ],
                },
            }
        )
    return out