"""Governance generation HTTP/service layer (Step 05)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func

from rai_gateway.models.onboarding import (
    OnboardingGovernanceClause,
    OnboardingGovernancePlan,
    OnboardingGovernanceProposalSet,
    OnboardingGovernanceReconciliationSnapshot,
    OnboardingProposalSet,
    OnboardingRun,
    OnboardingSession,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.governance_planner import (
    build_governance_plan,
    eligible_extraction_for_governance,
)
from rai_gateway.onboarding.governance_proposal_service import GovernanceProposalService
from rai_gateway.onboarding.governance_reconciliation import reconcile_governance_proposals
from rai_gateway.onboarding.governance_snapshot import capture_governance_baseline
from rai_gateway.onboarding.service import (
    GOVERNANCE_JOB_TYPE,
    STAGE_GOVERNANCE,
    Access,
    IntakeError,
    IntakeService,
    canonical_digest_obj,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue
from rai_gateway.core.config import settings


class GovernanceService:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    def preview(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        requested_inventory_clarification_revision: int,
        requested_governance_clarification_revision: int,
        inventory_proposal_set_id: uuid.UUID,
        include_suggested_controls: bool,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing governance permission")
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(409, "REVISION_CONFLICT", "Input revision changed")
            if (
                requested_inventory_clarification_revision
                != session.clarification_revision
            ):
                raise IntakeError(
                    409, "CLARIFICATION_CONFLICT", "Inventory clarification changed"
                )
            if (
                requested_governance_clarification_revision
                != session.governance_clarification_revision
            ):
                raise IntakeError(
                    409,
                    "GOVERNANCE_CLARIFICATION_CONFLICT",
                    "Governance clarification changed",
                )
            extraction = eligible_extraction_for_governance(self.db, session_id)
            try:
                body, digest = build_governance_plan(
                    self.db,
                    session_id=session_id,
                    input_revision=session.input_revision,
                    inventory_clarification_revision=session.clarification_revision,
                    governance_clarification_revision=session.governance_clarification_revision,
                    inventory_proposal_set_id=inventory_proposal_set_id,
                    extraction_run=extraction,
                    include_suggested_controls=include_suggested_controls,
                    actor=self.svc.actor,
                )
            except ValueError as exc:
                raise IntakeError(
                    422,
                    str(exc),
                    "Governance generation cannot start with the current prerequisites",
                ) from exc
            plan = OnboardingGovernancePlan(
                session_id=session_id,
                plan_version=body["plan_version"],
                body=body,
                digest=digest,
                created_by=self.svc.actor,
            )
            self.db.add(plan)
            self.db.flush()
            self.svc._audit(
                "onboarding.governance.preview",
                {"session_id": str(session_id), "plan_id": str(plan.id)},
                "PLAN_CREATED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {
            "plan_id": str(plan.id),
            "plan_digest": digest,
            "status": "READY",
            "partition_count": body["partition_count"],
            "limits": body["limits"],
            "inventory_proposal_set_id": str(inventory_proposal_set_id),
            "fixture_mode": body.get("fixture_mode"),
        }

    def generate(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        requested_inventory_clarification_revision: int,
        requested_governance_clarification_revision: int,
        plan_id: uuid.UUID,
        expected_plan_digest: str,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing governance permission")
        scope = self.svc._scope("governance_generate", session_id)
        fingerprint = self.svc._fingerprint(
            {
                "op": "generate",
                "plan_id": str(plan_id),
                "plan_digest": expected_plan_digest,
                "input_revision": requested_input_revision,
                "inventory_clarification_revision": requested_inventory_clarification_revision,
                "governance_clarification_revision": requested_governance_clarification_revision,
            }
        )
        receipt = self.svc._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is not None:
            return self.svc._run_result(
                session_id, receipt.resource_id, access, replayed=True
            )
        session = self.svc._get_session(session_id, lock=True)
        try:
            receipt = self.svc._lookup_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                self.db.rollback()
                return self.svc._run_result(
                    session_id, receipt.resource_id, access, replayed=True
                )
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            plan = self.db.get(OnboardingGovernancePlan, plan_id)
            if plan is None or plan.session_id != session_id:
                raise IntakeError(404, "PLAN_NOT_FOUND", "Governance plan not found")
            if plan.digest != expected_plan_digest:
                raise IntakeError(409, "PLAN_STALE", "Governance plan is stale")
            if self.svc._active_run(session_id) is not None:
                raise IntakeError(409, "RUN_ACTIVE", "A session run is already active")
            run = self._create_run(session, plan)
            self.svc._add_receipt(scope, idempotency_key, fingerprint, "RUN", run.id)
            self.svc._audit(
                "onboarding.governance.generate",
                {"session_id": str(session_id), "run_id": str(run.id)},
                "GOVERNANCE_REQUESTED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.svc._run_result(session_id, run.id, access, replayed=False)

    def _create_run(
        self, session: OnboardingSession, plan: OnboardingGovernancePlan
    ) -> OnboardingRun:
        body = plan.body
        extraction_id = uuid.UUID(body["extraction_run_id"])
        inventory_set_id = uuid.UUID(body["inventory_proposal_set_id"])
        run_id = uuid.uuid4()
        number = (
            self.db.query(func.max(OnboardingRun.run_number))
            .filter(OnboardingRun.session_id == session.id)
            .scalar()
            or 0
        ) + 1
        job = JobQueue(self.db).enqueue(
            job_type=GOVERNANCE_JOB_TYPE,
            payload={
                "run_id": str(run_id),
                "session_id": str(session.id),
                "input_revision": session.input_revision,
                "plan_id": str(plan.id),
                "plan_digest": plan.digest,
                "requested_by": self.svc.actor,
            },
            idempotency_key=f"onboarding:governance:{run_id}",
            created_by=self.svc.actor,
            max_attempts=settings.onboarding_job_max_attempts,
            allow_reserved=True,
        )
        run = OnboardingRun(
            id=run_id,
            session_id=session.id,
            run_number=number,
            stage=STAGE_GOVERNANCE,
            input_revision=session.input_revision,
            manifest={"governance": True},
            manifest_digest=canonical_digest_obj({"governance": True}),
            extraction_run_id=extraction_id,
            inventory_proposal_set_id=inventory_set_id,
            governance_plan_id=plan.id,
            plan=plan.body,
            plan_digest=plan.digest,
            job_id=uuid.UUID(job["id"]),
            created_by=self.svc.actor,
        )
        self.db.add(run)
        return run

    def list_governance_sets(
        self, session_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        rows = (
            self.db.query(OnboardingGovernanceProposalSet)
            .filter(OnboardingGovernanceProposalSet.session_id == session_id)
            .order_by(OnboardingGovernanceProposalSet.created_at.desc())
            .all()
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "governance_run_id": str(r.governance_run_id),
                    "inventory_proposal_set_id": str(r.inventory_proposal_set_id),
                    "edit_version": r.edit_version,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "total": len(rows),
        }

    def get_governance_set(
        self, session_id: uuid.UUID, set_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        row = self._get_set(session_id, set_id)
        svc = GovernanceProposalService(self.svc, set_id)
        return {
            "id": str(row.id),
            "session_id": str(session_id),
            "edit_version": row.edit_version,
            "proposal_count": len(svc.raw_proposals()),
            "clause_digest": row.clause_digest,
            "current_reconciliation_id": (
                str(row.current_reconciliation_id)
                if row.current_reconciliation_id
                else None
            ),
        }

    def list_clauses(
        self, session_id: uuid.UUID, set_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        self._get_set(session_id, set_id)
        rows = (
            self.db.query(OnboardingGovernanceClause)
            .filter(OnboardingGovernanceClause.set_id == set_id)
            .order_by(OnboardingGovernanceClause.ordinal)
            .all()
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "ordinal": r.ordinal,
                    "requirement": r.requirement,
                    "outcome": r.outcome,
                    "evidence": r.evidence,
                }
                for r in rows
            ]
        }

    def list_proposals(
        self, session_id: uuid.UUID, set_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        self._get_set(session_id, set_id)
        return {"items": GovernanceProposalService(self.svc, set_id).effective_proposals()}

    def get_proposal(
        self,
        session_id: uuid.UUID,
        set_id: uuid.UUID,
        proposal_id: uuid.UUID,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        self._get_set(session_id, set_id)
        item = GovernanceProposalService(self.svc, set_id).get_proposal(proposal_id)
        if item is None:
            raise IntakeError(404, "PROPOSAL_NOT_FOUND", "Proposal not found")
        return item

    def refresh_comparison(
        self,
        session_id: uuid.UUID,
        set_id: uuid.UUID,
        *,
        expected_edit_version: int,
        expected_row_version: int,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        session = self.svc._get_session(session_id, lock=True)
        proposal_set = self._get_set(session_id, set_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version)
            if expected_edit_version != proposal_set.edit_version:
                raise IntakeError(409, "EDIT_VERSION_CONFLICT", "Draft changed")
            baseline = capture_governance_baseline(self.db)
            proposals = GovernanceProposalService(self.svc, set_id).effective_proposals()
            recon = reconcile_governance_proposals(proposals, baseline)
            row = OnboardingGovernanceReconciliationSnapshot(
                set_id=set_id,
                edit_version=proposal_set.edit_version + 1,
                baseline_projection={
                    "digest": baseline.digest,
                    "record_count": baseline.projection["record_count"],
                },
                baseline_digest=baseline.digest,
                matching_rule_version=recon["matching_rule_version"],
                matches=recon["matches"],
                capabilities=recon["capabilities"],
                questions=recon["questions"],
            )
            self.db.add(row)
            self.db.flush()
            proposal_set.edit_version += 1
            proposal_set.current_reconciliation_id = row.id
            session.row_version += 1
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {
            "reconciliation_id": str(row.id),
            "edit_version": proposal_set.edit_version,
            "row_version": session.row_version,
        }

    def _get_set(
        self,
        session_id: uuid.UUID,
        set_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> OnboardingGovernanceProposalSet:
        query = self.db.query(OnboardingGovernanceProposalSet).filter(
            OnboardingGovernanceProposalSet.id == set_id,
            OnboardingGovernanceProposalSet.session_id == session_id,
        )
        if lock:
            query = query.with_for_update().populate_existing()
        row = query.one_or_none()
        if row is None:
            raise IntakeError(404, "SET_NOT_FOUND", "Governance set not found")
        return row


def latest_inventory_set(db, session_id: uuid.UUID) -> OnboardingProposalSet | None:
    return (
        db.query(OnboardingProposalSet)
        .filter(OnboardingProposalSet.session_id == session_id)
        .order_by(OnboardingProposalSet.created_at.desc())
        .first()
    )