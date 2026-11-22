"""Frozen runtime connection and verification contracts (Step 07)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RUNTIME_CONTRACT_VERSION = "onboarding-runtime-v1"
VERIFICATION_CONTRACT_VERSION = "onboarding-verify-v1"

DELEGATION_MODES = frozenset({"NONE", "EXPLICIT", "TENANT"})

VERIFICATION_LIMITS = {
    "max_scenarios": 32,
    "max_payload_bytes": 256 * 1024,
}


class RuntimeControlSelection(BaseModel):
    content_rule_ids: list[str] = Field(default_factory=list)
    tool_rule_ids: list[str] = Field(default_factory=list)
    budget_ids: list[str] = Field(default_factory=list)


class RuntimeSelectedIds(BaseModel):
    application_id: str
    ai_system_id: str | None = None
    agent_id: str | None = None
    agent_version_id: str | None = None
    runtime_identity_id: str | None = None
    controls: RuntimeControlSelection = Field(default_factory=RuntimeControlSelection)


class SaveRuntimeContextBody(BaseModel):
    expected_row_version: int = Field(ge=1)
    delegation_mode: Literal["NONE", "EXPLICIT", "TENANT"] = "NONE"
    selected: RuntimeSelectedIds


class PrepareActivationPlanBody(BaseModel):
    expected_row_version: int = Field(ge=1)
    expected_selection_digest: str


class ActivateControlsBody(BaseModel):
    expected_row_version: int = Field(ge=1)
    plan_id: str
    expected_payload_digest: str


class PrepareVerificationPlanBody(BaseModel):
    activation_plan_id: str
    expected_row_version: int = Field(ge=1)
    scenarios: list[dict[str, Any]] = Field(default_factory=list)


class VerifyRuntimeBody(BaseModel):
    expected_row_version: int = Field(ge=1)
    plan_id: str
    expected_payload_digest: str


def default_verification_scenarios(selected: dict[str, Any]) -> list[dict[str, Any]]:
    """Minimal smoke scenarios derived from the saved runtime selection."""
    controls = selected.get("controls") or {}
    scenarios: list[dict[str, Any]] = [
        {
            "key": "connection_preflight",
            "kind": "CONNECTIVITY",
            "description": "Confirm runtime context binding is present",
        }
    ]
    for rule_id in controls.get("content_rule_ids") or []:
        scenarios.append(
            {
                "key": f"content_rule_{rule_id[:8]}",
                "kind": "CONTENT_RULE",
                "target_id": rule_id,
                "description": "Verify content rule is registered for the selected scope",
            }
        )
    for rule_id in controls.get("tool_rule_ids") or []:
        scenarios.append(
            {
                "key": f"tool_rule_{rule_id[:8]}",
                "kind": "TOOL_RULE",
                "target_id": rule_id,
                "description": "Verify tool rule is registered for the selected scope",
            }
        )
    return scenarios[: VERIFICATION_LIMITS["max_scenarios"]]