"""Frozen apply payload contract (Step 06)."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

CONTRACT_VERSION = "onboarding-apply-v1"

APPLY_LIMITS = {
    "max_operations": 500,
    "max_payload_bytes": 4 * 1024 * 1024,
    "max_binding_key_length": 128,
    "max_dependency_depth": 32,
}

SUPPORTED_VERBS = frozenset({"CREATE", "REUSE", "UPDATE"})
SUPPORTED_TARGET_KINDS = frozenset(
    {
        "ai_system",
        "agent",
        "policy_source",
        "content_rule",
        "model",
        "tool",
        "mcp_server",
        "external_service",
        "data_source",
        "tool_rule",
        "runtime_budget",
    }
)


class ChangeOperationPayload(TypedDict, total=False):
    verb: Literal["CREATE", "REUSE", "UPDATE"]
    target_kind: str
    source_domain: str | None
    source_proposal_id: str | None
    action: dict[str, Any]
    dependency_ordinals: list[int]


class FrozenChangeSetPayload(TypedDict):
    contract_version: str
    operations: list[ChangeOperationPayload]
    inventory_proposal_set_id: str | None
    governance_proposal_set_id: str | None
    selection_digest: str


class PrepareSelection(BaseModel):
    """Which proposals the reviewer included in the change set."""

    inventory: dict[str, Literal["ACCEPT", "SKIP"]] = Field(default_factory=dict)
    governance: dict[str, Literal["ACCEPT", "SKIP"]] = Field(default_factory=dict)
    inventory_proposal_set_id: str | None = None
    governance_proposal_set_id: str | None = None


class ReviewDecisionBody(BaseModel):
    operation_id: str
    decision: Literal["ACCEPT", "SKIP"]


class ReviewDecisionsBody(BaseModel):
    decisions: list[ReviewDecisionBody] = Field(default_factory=list)


class PrepareChangeSetBody(BaseModel):
    expected_row_version: int = Field(ge=1)
    requested_input_revision: int = Field(ge=0)
    selection: PrepareSelection


class ApplyChangeSetBody(BaseModel):
    expected_row_version: int = Field(ge=1)
    requested_input_revision: int = Field(ge=0)
    change_set_id: str
    expected_payload_digest: str


class RetryApplicationBody(BaseModel):
    expected_row_version: int = Field(ge=1)
