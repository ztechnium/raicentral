"""Step 07: runtime verification contracts and aggregation."""

from __future__ import annotations

import uuid

from rai_gateway.onboarding.verification_schemas import (
    VERIFICATION_LIMITS,
    default_verification_scenarios,
)


def test_default_scenarios_include_connection():
    selected = {
        "application_id": str(uuid.uuid4()),
        "controls": {"content_rule_ids": [str(uuid.uuid4())]},
    }
    scenarios = default_verification_scenarios(selected)
    assert scenarios[0]["kind"] == "CONNECTIVITY"
    assert len(scenarios) <= VERIFICATION_LIMITS["max_scenarios"]


def test_delegation_modes_schema():
    from rai_gateway.onboarding.verification_schemas import SaveRuntimeContextBody, RuntimeSelectedIds

    body = SaveRuntimeContextBody(
        expected_row_version=1,
        delegation_mode="NONE",
        selected=RuntimeSelectedIds(application_id=str(uuid.uuid4())),
    )
    assert body.delegation_mode == "NONE"