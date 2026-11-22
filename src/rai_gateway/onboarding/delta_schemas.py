"""Pydantic contracts for Step 08 delta/update APIs."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class CreateUpdateSessionBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    baseline_session_id: UUID
    baseline_id: UUID | None = None


class BaselineReviewBody(BaseModel):
    baseline_kind: str = Field(pattern="^(APPLIED_RECEIPT|REVIEWED_CURRENT_STATE)$")
    application_receipt_id: UUID | None = None
    scope_notes: str | None = Field(default=None, max_length=2000)


class BaselineConfirmBody(BaseModel):
    baseline_id: UUID
    expected_row_version: int = Field(ge=1)


class SourceLineageBody(BaseModel):
    previous_revision_id: UUID
    new_revision_id: UUID
    relationship: str = Field(pattern="^(SUPERSEDES|SUPPLEMENTS|INDEPENDENT)$")
    effective_notes: str | None = Field(default=None, max_length=2000)
    expected_row_version: int = Field(ge=1)


class DeltaPreviewBody(BaseModel):
    baseline_id: UUID
    expected_row_version: int = Field(ge=1)
    requested_input_revision: int = Field(ge=0)


class DeltaRunBody(BaseModel):
    delta_plan_id: UUID
    expected_plan_digest: str = Field(min_length=64, max_length=64)
    expected_row_version: int = Field(ge=1)


class DeltaDecisionBody(BaseModel):
    finding_id: UUID
    decision: str = Field(
        pattern="^(KEEP|UPDATE|EXCLUDE|MATCH|CLARIFY|DEFER|SOURCE_ONLY)$"
    )
    notes: str | None = Field(default=None, max_length=4000)
    expected_edit_revision: int = Field(ge=1)


class DeltaPrepareReviewBody(BaseModel):
    delta_plan_id: UUID
    selection: dict = Field(default_factory=dict)
    expected_row_version: int = Field(ge=1)
    requested_input_revision: int = Field(ge=0)


class DeltaRefreshBody(BaseModel):
    delta_plan_id: UUID
    expected_reconciliation_version: int = Field(ge=1)
