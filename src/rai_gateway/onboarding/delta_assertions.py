"""Deterministic assertion extraction, normalization, and differencing (Step 08)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

NORMALIZATION_VERSION = "2026-11-22"

CHANGE_TYPES = frozenset(
    {
        "ADDED",
        "MODIFIED",
        "UNCHANGED",
        "MOVED",
        "WITHDRAWN_IN_SOURCE",
        "CONFLICT",
        "UNKNOWN_COVERAGE",
        "AUTHORITY_CHANGED",
        "COVERAGE_CHANGED",
    }
)

_CURRENCY_RE = re.compile(r"^(?P<amount>-?\d+(?:\.\d+)?)\s*(?P<code>[A-Z]{3})?$")
_THRESHOLD_RE = re.compile(
    r"(?P<op>>=|<=|>|<|=)\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<currency>[A-Z]{3})?",
    re.IGNORECASE,
)


def assertion_identity(subject: str, predicate: str, scope: str | None = None) -> str:
    parts = [subject.strip().lower(), predicate.strip().lower()]
    if scope:
        parts.append(scope.strip().lower())
    return "|".join(parts)


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return " ".join(text.split())


def normalize_identifier(value: str | None) -> str:
    return normalize_text(value).lower()


def normalize_amount(value: Any) -> str | None:
    if value is None:
        return None
    text = normalize_text(str(value))
    if not text:
        return None
    try:
        dec = Decimal(text.replace(",", ""))
        normalized = format(dec.normalize(), "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".") or "0"
        return normalized
    except (InvalidOperation, ValueError):
        return text


def normalize_threshold_clause(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    normalized = normalize_text(text)
    match = _THRESHOLD_RE.search(normalized)
    if not match:
        return {"raw": normalized}
    amount = normalize_amount(match.group("amount"))
    return {
        "op": match.group("op"),
        "amount": amount,
        "currency": (match.group("currency") or "").upper() or None,
        "raw": normalized,
    }


def stable_assertion_digest(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inventory_assertions(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn inventory proposals into comparable assertions with stable keys."""
    out: list[dict[str, Any]] = []
    for proposal in proposals:
        entity_type = proposal.get("entity_type") or "unknown"
        generated = proposal.get("generated") or {}
        claims = generated.get("claims") or {}
        stable_key = None
        if entity_type == "ai_system":
            stable_key = claims.get("system_key")
        elif entity_type == "agent":
            stable_key = claims.get("agent_key")
        elif entity_type in ("model", "tool", "mcp_server", "data_source", "external_service"):
            stable_key = claims.get("resource_key") or claims.get("name")
        subject = normalize_identifier(stable_key or proposal.get("id"))
        predicate = normalize_identifier(entity_type)
        scope = normalize_identifier(claims.get("name"))
        identity = assertion_identity(subject, predicate, scope or None)
        normalized_value = {
            "entity_type": entity_type,
            "claims": {k: normalize_text(v) if isinstance(v, str) else v for k, v in claims.items()},
        }
        out.append(
            {
                "source": "inventory",
                "proposal_id": proposal.get("id"),
                "identity": identity,
                "subject": subject,
                "predicate": predicate,
                "scope": scope,
                "normalized": normalized_value,
                "digest": stable_assertion_digest(normalized_value),
                "provenance": {"kind": "inventory_proposal"},
            }
        )
    return out


def governance_assertions(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for clause in clauses:
        requirement = clause.get("requirement") or {}
        subject = normalize_identifier(requirement.get("subject") or requirement.get("title"))
        predicate = normalize_identifier(requirement.get("predicate") or requirement.get("kind"))
        threshold = normalize_threshold_clause(requirement.get("threshold_text"))
        scope = normalize_identifier(requirement.get("scope"))
        identity = assertion_identity(subject or f"clause-{clause.get('ordinal')}", predicate, scope or None)
        normalized_value = {
            "requirement": requirement,
            "threshold": threshold,
            "outcome": normalize_text(clause.get("outcome")),
        }
        out.append(
            {
                "source": "governance",
                "clause_id": clause.get("id"),
                "ordinal": clause.get("ordinal"),
                "identity": identity,
                "subject": subject,
                "predicate": predicate,
                "scope": scope,
                "normalized": normalized_value,
                "digest": stable_assertion_digest(normalized_value),
                "provenance": clause.get("evidence") or {"kind": "governance_clause"},
            }
        )
    return out


@dataclass(frozen=True)
class AssertionDiff:
    change_type: str
    identity: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str


def compare_assertion_sets(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[AssertionDiff]:
    """Deterministic identity-keyed diff without interpreting absence as deletion."""
    by_id_before = {a["identity"]: a for a in baseline}
    by_id_after = {a["identity"]: a for a in current}
    diffs: list[AssertionDiff] = []
    all_ids = set(by_id_before) | set(by_id_after)
    for identity in sorted(all_ids):
        before = by_id_before.get(identity)
        after = by_id_after.get(identity)
        if before is None and after is not None:
            diffs.append(
                AssertionDiff("ADDED", identity, None, after, "New assertion in updated source")
            )
            continue
        if before is not None and after is None:
            diffs.append(
                AssertionDiff(
                    "WITHDRAWN_IN_SOURCE",
                    identity,
                    before,
                    None,
                    "Assertion missing from updated source; domain records are not retired automatically",
                )
            )
            continue
        assert before is not None and after is not None
        if before["digest"] == after["digest"]:
            diffs.append(AssertionDiff("UNCHANGED", identity, before, after, "Semantically unchanged"))
            continue
        if before.get("predicate") == after.get("predicate") and before.get("subject") == after.get("subject"):
            before_prov = before.get("provenance") or {}
            after_prov = after.get("provenance") or {}
            if before_prov.get("anchor") and after_prov.get("anchor") and before_prov.get("anchor") != after_prov.get("anchor"):
                diffs.append(
                    AssertionDiff("MOVED", identity, before, after, "Source anchor moved; semantics require review")
                )
            else:
                diffs.append(
                    AssertionDiff("MODIFIED", identity, before, after, "Material semantic change detected")
                )
        else:
            diffs.append(
                AssertionDiff("MODIFIED", identity, before, after, "Predicate or subject changed")
            )
    return diffs


def summarize_diff_counts(diffs: list[AssertionDiff]) -> dict[str, int]:
    counts: dict[str, int] = {k: 0 for k in CHANGE_TYPES}
    for diff in diffs:
        counts[diff.change_type] = counts.get(diff.change_type, 0) + 1
    return {k: v for k, v in counts.items() if v > 0}
