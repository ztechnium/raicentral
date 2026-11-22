"""Unit tests for Step 08 deterministic assertion diffing."""

from __future__ import annotations

from rai_gateway.onboarding.delta_assertions import (
    compare_assertion_sets,
    governance_assertions,
    inventory_assertions,
    normalize_amount,
    normalize_threshold_clause,
)


def test_threshold_normalization_preserves_operator():
    parsed = normalize_threshold_clause("> 500 USD")
    assert parsed is not None
    assert parsed.get("op") == ">"
    assert normalize_amount(parsed.get("amount")) == "500"
    assert parsed.get("currency") == "USD"


def test_modified_threshold_detected():
    before = inventory_assertions(
        [
            {
                "id": "p1",
                "entity_type": "tool",
                "generated": {
                    "claims": {
                        "resource_key": "refund-api",
                        "name": "Refund",
                        "threshold_text": "> 500 USD",
                    }
                },
            }
        ]
    )
    after = inventory_assertions(
        [
            {
                "id": "p2",
                "entity_type": "tool",
                "generated": {
                    "claims": {
                        "resource_key": "refund-api",
                        "name": "Refund",
                        "threshold_text": "> 750 USD",
                    },
                },
            }
        ]
    )
    diffs = compare_assertion_sets(before, after)
    assert any(d.change_type == "MODIFIED" for d in diffs)


def test_withdrawn_in_source_never_maps_to_delete():
    before = governance_assertions(
        [
            {
                "id": "c1",
                "ordinal": 0,
                "requirement": {"subject": "PII", "predicate": "protect"},
                "outcome": "obligation",
                "evidence": {},
            }
        ]
    )
    diffs = compare_assertion_sets(before, [])
    assert len(diffs) == 1
    assert diffs[0].change_type == "WITHDRAWN_IN_SOURCE"


def test_byte_identical_assertions_unchanged():
    proposals = [
        {
            "id": "p1",
            "entity_type": "ai_system",
            "generated": {"claims": {"system_key": "support-ai", "name": "Support"}},
        }
    ]
    assertions = inventory_assertions(proposals)
    diffs = compare_assertion_sets(assertions, assertions)
    assert all(d.change_type == "UNCHANGED" for d in diffs)
