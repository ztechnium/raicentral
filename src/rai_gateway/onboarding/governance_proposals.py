"""Fixture and partition parsers for governance generation (Step 05)."""

from __future__ import annotations

import re
from typing import Any

from rai_gateway.onboarding.governance_compiler import compile_clause

CLAUSE_LINE = re.compile(
    r"^(?P<prefix>clause|policy):\s*(?P<body>.+)$", re.IGNORECASE
)
MODALITY_LINE = re.compile(r"\b(MUST|MUST_NOT|SHOULD|MAY)\b")


def _clause_from_line(line: str, ordinal: int) -> dict[str, Any]:
    modality = "MUST"
    m = MODALITY_LINE.search(line)
    if m:
        modality = m.group(1)
    systems: list[str] = []
    sm = re.search(r"systems=([^;]+)", line, re.IGNORECASE)
    if sm:
        systems = [s.strip() for s in sm.group(1).split(",") if s.strip()]
    return {
        "ordinal": ordinal,
        "text": line,
        "modality": modality,
        "systems": systems,
        "topic": "policy_clause",
        "interpretation": "SOURCE_STATED",
        "binding": "bound" if systems else "unbound",
        "evidence": [
            {
                "basis": "SOURCE_STATED",
                "range": [0, len(line)],
            }
        ],
        "outcome": "mapped_proposal",
    }


def clauses_from_fixture_partition(text: str) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = []
    ordinal = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = CLAUSE_LINE.match(line)
        body = m.group("body").strip() if m else line
        clauses.append(_clause_from_line(body, ordinal))
        ordinal += 1
    if not clauses and text.strip():
        clauses.append(_clause_from_line(text.strip(), 0))
    return clauses


def proposals_from_clauses(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for clause in clauses:
        for compiled in compile_clause(clause):
            compiled["source_clause_ordinal"] = clause["ordinal"]
            proposals.append(compiled)
    return proposals