"""Governance draft proposal rendering and edits (Step 05)."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingGovernanceProposal,
    OnboardingGovernanceProposalEdit,
    OnboardingGovernanceProposalSet,
    OnboardingGovernanceReconciliationSnapshot,
)
from rai_gateway.onboarding.service import IntakeService


class GovernanceProposalService:
    def __init__(self, svc: IntakeService, set_id: uuid.UUID) -> None:
        self.svc = svc
        self.db: Session = svc.db
        self.set_id = set_id

    def _set(self) -> OnboardingGovernanceProposalSet:
        row = self.db.get(OnboardingGovernanceProposalSet, self.set_id)
        assert row is not None
        return row

    def raw_proposals(self) -> list[OnboardingGovernanceProposal]:
        return (
            self.db.query(OnboardingGovernanceProposal)
            .filter(OnboardingGovernanceProposal.set_id == self.set_id)
            .order_by(OnboardingGovernanceProposal.created_at)
            .all()
        )

    def _edits(self) -> list[OnboardingGovernanceProposalEdit]:
        return (
            self.db.query(OnboardingGovernanceProposalEdit)
            .filter(OnboardingGovernanceProposalEdit.set_id == self.set_id)
            .order_by(
                OnboardingGovernanceProposalEdit.edit_version,
                OnboardingGovernanceProposalEdit.created_at,
            )
            .all()
        )

    def effective_proposals(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        edits_by_proposal: dict[str, list[OnboardingGovernanceProposalEdit]] = {}
        for edit in self._edits():
            key = str(edit.proposal_id) if edit.proposal_id else "*"
            edits_by_proposal.setdefault(key, []).append(edit)
        recon = self._current_reconciliation()
        caps = {c.get("proposal_id"): c for c in (recon.capabilities if recon else [])}
        matches = {m.get("proposal_id"): m for m in (recon.matches if recon else [])}
        for proposal in self.raw_proposals():
            generated = deepcopy(proposal.generated)
            for edit in edits_by_proposal.get(str(proposal.id), []):
                if edit.replacement is not None:
                    generated = _apply_path(generated, edit.path, edit.replacement)
            match = matches.get(str(proposal.id), {})
            capability = caps.get(str(proposal.id), {})
            out.append(
                {
                    "id": str(proposal.id),
                    "proposal_family": proposal.proposal_family,
                    "generated": generated,
                    "match_state": match.get("match_state", "Proposed"),
                    "capability": capability,
                    "runtime_verification": capability.get("runtime_verification", "NOT_RUN"),
                }
            )
        return out

    def get_proposal(self, proposal_id: uuid.UUID) -> dict[str, Any] | None:
        for item in self.effective_proposals():
            if item["id"] == str(proposal_id):
                return item
        return None

    def _current_reconciliation(self) -> OnboardingGovernanceReconciliationSnapshot | None:
        ps = self._set()
        if ps.current_reconciliation_id is None:
            return None
        return self.db.get(
            OnboardingGovernanceReconciliationSnapshot, ps.current_reconciliation_id
        )


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