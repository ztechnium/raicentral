"""Draft proposal rendering and edits (Step 04)."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingInventoryProposal,
    OnboardingProposalEdit,
    OnboardingProposalSet,
    OnboardingReconciliationSnapshot,
)
from rai_gateway.onboarding.service import IntakeService


class ProposalService:
    def __init__(self, svc: IntakeService, set_id: uuid.UUID) -> None:
        self.svc = svc
        self.db: Session = svc.db
        self.set_id = set_id

    def _set(self) -> OnboardingProposalSet:
        row = self.db.get(OnboardingProposalSet, self.set_id)
        assert row is not None
        return row

    def raw_proposals(self) -> list[OnboardingInventoryProposal]:
        return (
            self.db.query(OnboardingInventoryProposal)
            .filter(OnboardingInventoryProposal.set_id == self.set_id)
            .order_by(OnboardingInventoryProposal.created_at)
            .all()
        )

    def _edits(self) -> list[OnboardingProposalEdit]:
        return (
            self.db.query(OnboardingProposalEdit)
            .filter(OnboardingProposalEdit.set_id == self.set_id)
            .order_by(OnboardingProposalEdit.edit_version, OnboardingProposalEdit.created_at)
            .all()
        )

    def effective_proposals(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        edits_by_proposal: dict[str, list[OnboardingProposalEdit]] = {}
        for edit in self._edits():
            key = str(edit.proposal_id) if edit.proposal_id else "*"
            edits_by_proposal.setdefault(key, []).append(edit)
        recon = self._current_reconciliation()
        matches = {m.get("proposal_id"): m for m in (recon.matches if recon else [])}
        for proposal in self.raw_proposals():
            generated = deepcopy(proposal.generated)
            for edit in edits_by_proposal.get(str(proposal.id), []):
                if edit.replacement is not None:
                    generated = _apply_path(generated, edit.path, edit.replacement)
            match = matches.get(str(proposal.id), {})
            out.append(
                {
                    "id": str(proposal.id),
                    "entity_type": proposal.entity_type,
                    "generated": generated,
                    "match_state": match.get("match_state", "Proposed"),
                    "suggested_action": match.get("suggested_action"),
                }
            )
        return out

    def get_proposal(self, proposal_id: uuid.UUID) -> dict[str, Any] | None:
        for item in self.effective_proposals():
            if item["id"] == str(proposal_id):
                recon = self._current_reconciliation()
                if recon:
                    for match in recon.matches:
                        if match.get("proposal_id") == str(proposal_id):
                            item["match"] = match
                return item
        return None

    def _current_reconciliation(self) -> OnboardingReconciliationSnapshot | None:
        ps = self._set()
        if ps.current_reconciliation_id is None:
            return None
        return self.db.get(OnboardingReconciliationSnapshot, ps.current_reconciliation_id)

    def append_edit(
        self,
        *,
        proposal_id: uuid.UUID | None,
        path: str,
        replacement: Any,
        resolution: str | None,
        evidence: dict[str, Any],
        expected_edit_version: int,
    ) -> int:
        ps = self._set()
        if ps.edit_version != expected_edit_version:
            raise ValueError("EDIT_VERSION_CONFLICT")
        new_version = ps.edit_version + 1
        self.db.add(
            OnboardingProposalEdit(
                set_id=self.set_id,
                proposal_id=proposal_id,
                path=path,
                replacement=replacement,
                resolution=resolution,
                edit_version=new_version,
                evidence=evidence,
                actor=self.svc.actor,
            )
        )
        ps.edit_version = new_version
        return new_version


def _apply_path(target: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    parts = [p for p in path.split(".") if p]
    if not parts:
        return target
    cursor: Any = target
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor[part], dict):
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value
    return target