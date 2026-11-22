"""Pure schema tests for Step 07 (no database)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from rai_gateway.onboarding.verification_schemas import (
    PrepareVerificationPlanBody,
    RuntimeSelectedIds,
    SaveRuntimeContextBody,
    default_verification_scenarios,
)


def test_schema_runtime_selected_ids():
    body = RuntimeSelectedIds(
        application_id=str(uuid.uuid4()),
        ai_system_id=str(uuid.uuid4()),
        controls={"content_rule_ids": [str(uuid.uuid4())]},
    )
    SaveRuntimeContextBody(
        expected_row_version=1,
        delegation_mode="NONE",
        selected=body,
    )


def test_schema_rejects_invalid_delegation():
    with pytest.raises(ValidationError):
        SaveRuntimeContextBody(
            expected_row_version=1,
            delegation_mode="INVALID",  # type: ignore[arg-type]
            selected=RuntimeSelectedIds(application_id=str(uuid.uuid4())),
        )


def test_default_verification_scenarios():
    selected = {
        "controls": {
            "content_rule_ids": [str(uuid.uuid4())],
            "tool_rule_ids": [str(uuid.uuid4())],
        }
    }
    scenarios = default_verification_scenarios(selected)
    assert scenarios[0]["kind"] == "CONNECTIVITY"
    assert any(s["kind"] == "CONTENT_RULE" for s in scenarios)
    assert any(s["kind"] == "TOOL_RULE" for s in scenarios)


def test_prepare_verification_plan_schema():
    PrepareVerificationPlanBody(
        activation_plan_id=str(uuid.uuid4()),
        expected_row_version=1,
        scenarios=[{"key": "smoke", "kind": "CONNECTIVITY"}],
    )