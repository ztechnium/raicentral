"""Compile and persist immutable onboarding change sets (Step 06)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from rai_gateway.models.onboarding import (
    OnboardingChangeOperation,
    OnboardingChangeSet,
    OnboardingGovernanceProposalSet,
    OnboardingProposalSet,
    OnboardingReviewDecision,
)
from rai_gateway.onboarding.application_schemas import (
    APPLY_LIMITS,
    CONTRACT_VERSION,
    SUPPORTED_TARGET_KINDS,
)
from rai_gateway.onboarding.governance_proposal_service import GovernanceProposalService
from rai_gateway.onboarding.proposal_service import ProposalService
from rai_gateway.onboarding.service import (
    Access,
    IntakeError,
    IntakeService,
    canonical_digest_obj,
    utcnow,
)

_INVENTORY_TARGET_MAP = {
    "ai_system": "ai_system",
    "agent": "agent",
}

_GOVERNANCE_TARGET_MAP = {
    "policy_source": "policy_source",
    "content_rule": "content_rule",
}


class ChangeSetService:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    def prepare_change_set(
        self,
        session_id: uuid.UUID,
        selection: dict[str, Any],
        access: Access,
        *,
        expected_row_version: int,
        requested_input_revision: int,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        session = self.svc._get_session(session_id, lock=True)
        cs: OnboardingChangeSet
        op_rows: list[OnboardingChangeOperation]
        try:
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(409, "REVISION_CONFLICT", "Input revision changed")
            inv_set_id = selection.get("inventory_proposal_set_id")
            gov_set_id = selection.get("governance_proposal_set_id")
            if not inv_set_id and not gov_set_id:
                raise IntakeError(
                    422, "SELECTION_REQUIRED", "At least one proposal set is required"
                )
            inv_uuid = uuid.UUID(str(inv_set_id)) if inv_set_id else None
            gov_uuid = uuid.UUID(str(gov_set_id)) if gov_set_id else None
            if inv_uuid:
                inv_row = self.db.get(OnboardingProposalSet, inv_uuid)
                if inv_row is None or inv_row.session_id != session_id:
                    raise IntakeError(
                        404, "PROPOSAL_SET_NOT_FOUND", "Inventory set not found"
                    )
            if gov_uuid:
                gov_row = self.db.get(OnboardingGovernanceProposalSet, gov_uuid)
                if gov_row is None or gov_row.session_id != session_id:
                    raise IntakeError(
                        404, "GOVERNANCE_SET_NOT_FOUND", "Governance set not found"
                    )
            compiled = self._compile_operations(
                session_id,
                selection,
                inv_uuid,
                gov_uuid,
            )
            if len(compiled) > APPLY_LIMITS["max_operations"]:
                raise IntakeError(
                    422, "TOO_MANY_OPERATIONS", "Change set exceeds operation limit"
                )
            payload = {
                "contract_version": CONTRACT_VERSION,
                "operations": compiled,
                "inventory_proposal_set_id": str(inv_uuid) if inv_uuid else None,
                "governance_proposal_set_id": str(gov_uuid) if gov_uuid else None,
                "selection_digest": canonical_digest_obj(selection),
            }
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            if len(raw.encode("utf-8")) > APPLY_LIMITS["max_payload_bytes"]:
                raise IntakeError(
                    422, "PAYLOAD_TOO_LARGE", "Compiled payload exceeds size limit"
                )
            digest = canonical_digest_obj(payload)
            cs = OnboardingChangeSet(
                session_id=session_id,
                contract_version=CONTRACT_VERSION,
                status="PREPARED",
                inventory_proposal_set_id=inv_uuid,
                governance_proposal_set_id=gov_uuid,
                selection=selection,
                payload=payload,
                payload_digest=digest,
                created_by=self.svc.actor,
            )
            self.db.add(cs)
            self.db.flush()
            op_rows = []
            for ordinal, op in enumerate(compiled):
                row = OnboardingChangeOperation(
                    change_set_id=cs.id,
                    ordinal=ordinal,
                    verb=op["verb"],
                    target_kind=op["target_kind"],
                    source_domain=op.get("source_domain"),
                    source_proposal_id=(
                        uuid.UUID(op["source_proposal_id"])
                        if op.get("source_proposal_id")
                        else None
                    ),
                    action_payload=op.get("action") or {},
                    dependency_ordinals=op.get("dependency_ordinals") or [],
                )
                self.db.add(row)
                op_rows.append(row)
            self.db.flush()
            self.svc._audit(
                "onboarding.change_set.prepare",
                {"session_id": str(session_id), "change_set_id": str(cs.id)},
                "CHANGE_SET_PREPARED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self._serialize_change_set(cs, op_rows)

    def _compile_operations(
        self,
        session_id: uuid.UUID,
        selection: dict[str, Any],
        inv_set_id: uuid.UUID | None,
        gov_set_id: uuid.UUID | None,
    ) -> list[dict[str, Any]]:
        ops: list[dict[str, Any]] = []
        inv_sel: dict[str, str] = selection.get("inventory") or {}
        gov_sel: dict[str, str] = selection.get("governance") or {}

        if inv_set_id:
            ps = ProposalService(self.svc, inv_set_id)
            recon = ps._current_reconciliation()  # noqa: SLF001 — shared compile path
            matches = {m.get("proposal_id"): m for m in (recon.matches if recon else [])}
            for proposal in ps.effective_proposals():
                pid = proposal["id"]
                choice = inv_sel.get(pid, "ACCEPT")
                if choice == "SKIP":
                    continue
                entity = proposal.get("entity_type")
                target_kind = _INVENTORY_TARGET_MAP.get(entity or "")
                if target_kind not in SUPPORTED_TARGET_KINDS:
                    continue
                match = matches.get(pid, {})
                recon_match = next(
                    (
                        m
                        for m in (recon.matches if recon else [])
                        if m.get("proposal_id") == pid
                    ),
                    match,
                )
                verb, action = self._verb_for_inventory(proposal, recon_match)
                ops.append(
                    {
                        "verb": verb,
                        "target_kind": target_kind,
                        "source_domain": "inventory",
                        "source_proposal_id": pid,
                        "action": action,
                        "dependency_ordinals": self._inventory_deps(
                            proposal, ops, entity
                        ),
                    }
                )

        if gov_set_id:
            gps = GovernanceProposalService(self.svc, gov_set_id)
            recon_row = gps._current_reconciliation()  # noqa: SLF001
            matches = {
                m.get("proposal_id"): m for m in (recon_row.matches if recon_row else [])
            }
            for proposal in gps.effective_proposals():
                pid = proposal["id"]
                choice = gov_sel.get(pid, "SKIP")
                if choice == "SKIP":
                    continue
                family = proposal.get("proposal_family")
                target_kind = _GOVERNANCE_TARGET_MAP.get(family or "")
                if target_kind not in SUPPORTED_TARGET_KINDS:
                    continue
                match = matches.get(pid, {})
                verb = "CREATE" if match.get("match_state") == "Proposed" else "REUSE"
                action = {
                    "generated": proposal.get("generated") or {},
                    "proposal_family": family,
                }
                if verb == "REUSE":
                    rec_id = (match.get("candidates") or [{}])[0].get("record_id")
                    if rec_id:
                        action["target_id"] = rec_id
                deps = self._governance_system_deps(proposal, ops)
                ops.append(
                    {
                        "verb": verb,
                        "target_kind": target_kind,
                        "source_domain": "governance",
                        "source_proposal_id": pid,
                        "action": action,
                        "dependency_ordinals": deps,
                    }
                )
        if not ops:
            raise IntakeError(422, "NO_OPERATIONS", "Selection produced no apply operations")
        return ops

    @staticmethod
    def _verb_for_inventory(
        proposal: dict[str, Any], match: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        generated = proposal.get("generated") or {}
        claims = (generated.get("claims") or {}).copy()
        action: dict[str, Any] = {"claims": claims, "entity_type": proposal["entity_type"]}
        state = match.get("suggested_action") or match.get("match_state")
        candidates = match.get("candidates") or []
        if state in ("REUSE", "Existing", "Matched") and candidates:
            return "REUSE", {
                **action,
                "target_id": candidates[0].get("record_id"),
            }
        diffs = match.get("field_diffs") or []
        allowed = [d for d in diffs if d.get("capability") is None]
        if state == "UPDATE" or (candidates and allowed):
            rid = candidates[0].get("record_id") if candidates else None
            return "UPDATE", {
                **action,
                "target_id": rid,
                "fields": {d["field"]: d["proposed"] for d in allowed},
            }
        return "CREATE", action

    @staticmethod
    def _inventory_deps(
        proposal: dict[str, Any],
        prior: list[dict[str, Any]],
        entity: str | None,
    ) -> list[int]:
        if entity != "agent":
            return []
        rel = (proposal.get("generated") or {}).get("relationships") or {}
        parent = (rel.get("ai_system") or {}).get("reference") or {}
        parent_pid = parent.get("proposal_id")
        if not parent_pid:
            return []
        for idx, op in enumerate(prior):
            if op.get("source_proposal_id") == parent_pid:
                return [idx]
        return []

    @staticmethod
    def _governance_system_deps(
        proposal: dict[str, Any],
        prior: list[dict[str, Any]],
    ) -> list[int]:
        generated = proposal.get("generated") or {}
        sys_key = generated.get("system_key")
        if not sys_key:
            return []
        for idx, op in enumerate(prior):
            if op.get("target_kind") != "ai_system":
                continue
            claims = (op.get("action") or {}).get("claims") or {}
            if claims.get("system_key") == sys_key and op.get("verb") == "CREATE":
                return [idx]
        return []

    def get_change_set(
        self, session_id: uuid.UUID, change_set_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        cs = self.db.get(OnboardingChangeSet, change_set_id)
        if cs is None or cs.session_id != session_id:
            raise IntakeError(404, "CHANGE_SET_NOT_FOUND", "Change set not found")
        ops = (
            self.db.query(OnboardingChangeOperation)
            .filter(OnboardingChangeOperation.change_set_id == cs.id)
            .order_by(OnboardingChangeOperation.ordinal)
            .all()
        )
        return self._serialize_change_set(cs, ops)

    def list_change_sets(
        self, session_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_read:
            raise IntakeError(403, "NOT_ALLOWED", "Missing read permission")
        rows = (
            self.db.query(OnboardingChangeSet)
            .filter(OnboardingChangeSet.session_id == session_id)
            .order_by(OnboardingChangeSet.created_at.desc())
            .limit(50)
            .all()
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "status": r.status,
                    "payload_digest": r.payload_digest,
                    "contract_version": r.contract_version,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "discarded_at": r.discarded_at.isoformat() if r.discarded_at else None,
                }
                for r in rows
            ]
        }

    def discard(
        self,
        session_id: uuid.UUID,
        change_set_id: uuid.UUID,
        *,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        cs = self.db.get(OnboardingChangeSet, change_set_id)
        if cs is None or cs.session_id != session_id:
            raise IntakeError(404, "CHANGE_SET_NOT_FOUND", "Change set not found")
        if cs.status != "PREPARED":
            raise IntakeError(409, "CHANGE_SET_NOT_PREPARED", "Only prepared sets may be discarded")
        cs.status = "DISCARDED"
        cs.discarded_at = utcnow()
        self.db.commit()
        return {"id": str(cs.id), "status": cs.status}

    def record_review_decisions(
        self,
        session_id: uuid.UUID,
        change_set_id: uuid.UUID,
        decisions: list[dict[str, Any]],
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        cs = self.db.get(OnboardingChangeSet, change_set_id)
        if cs is None or cs.session_id != session_id:
            raise IntakeError(404, "CHANGE_SET_NOT_FOUND", "Change set not found")
        if cs.status != "PREPARED":
            raise IntakeError(409, "CHANGE_SET_NOT_PREPARED", "Change set is not prepared")
        for entry in decisions:
            op_id = uuid.UUID(str(entry["operation_id"]))
            op = self.db.get(OnboardingChangeOperation, op_id)
            if op is None or op.change_set_id != cs.id:
                raise IntakeError(404, "OPERATION_NOT_FOUND", "Operation not found")
            existing = (
                self.db.query(OnboardingReviewDecision)
                .filter(
                    OnboardingReviewDecision.change_set_id == cs.id,
                    OnboardingReviewDecision.operation_id == op_id,
                )
                .one_or_none()
            )
            if existing:
                existing.decision = entry["decision"]
            else:
                self.db.add(
                    OnboardingReviewDecision(
                        change_set_id=cs.id,
                        operation_id=op_id,
                        decision=entry["decision"],
                        actor=self.svc.actor,
                    )
                )
        cs.review_digest = canonical_digest_obj(decisions)
        self.db.commit()
        return {"change_set_id": str(cs.id), "review_digest": cs.review_digest}

    @staticmethod
    def _serialize_change_set(
        cs: OnboardingChangeSet, ops: list[OnboardingChangeOperation]
    ) -> dict[str, Any]:
        return {
            "id": str(cs.id),
            "session_id": str(cs.session_id),
            "status": cs.status,
            "contract_version": cs.contract_version,
            "payload_digest": cs.payload_digest,
            "review_digest": cs.review_digest,
            "inventory_proposal_set_id": (
                str(cs.inventory_proposal_set_id) if cs.inventory_proposal_set_id else None
            ),
            "governance_proposal_set_id": (
                str(cs.governance_proposal_set_id) if cs.governance_proposal_set_id else None
            ),
            "operations": [
                {
                    "id": str(o.id),
                    "ordinal": o.ordinal,
                    "verb": o.verb,
                    "target_kind": o.target_kind,
                    "source_domain": o.source_domain,
                    "source_proposal_id": (
                        str(o.source_proposal_id) if o.source_proposal_id else None
                    ),
                    "action": o.action_payload,
                    "dependency_ordinals": o.dependency_ordinals,
                    "status": o.status,
                }
                for o in ops
            ],
        }
