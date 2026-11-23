"""Inventory discovery HTTP/service layer (Step 04)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func

from rai_gateway.models.onboarding import (
    OnboardingClarificationAnswer,
    OnboardingDiscoveryPlan,
    OnboardingInventoryProposal,
    OnboardingProposalEdit,
    OnboardingProposalSet,
    OnboardingReconciliationSnapshot,
    OnboardingRun,
    OnboardingSession,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.discovery_planner import build_discovery_plan, eligible_extraction
from rai_gateway.onboarding.proposal_service import ProposalService
from rai_gateway.onboarding.registry_snapshot import capture_snapshot
from rai_gateway.onboarding.reconciliation import reconcile_proposals
from rai_gateway.onboarding.service import (
    DISCOVER_JOB_TYPE,
    STAGE_DISCOVER,
    Access,
    IntakeError,
    IntakeService,
    canonical_digest_obj,
    utcnow,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue
from rai_gateway.core.config import settings


_PREREQUISITE_MESSAGES = {
    "AI_TEST_REQUIRED": (
        "Run a passing AI connection test for the current AI configuration first "
        "(AI setup > Test AI connection)."
    ),
    "AI_CONFIGURATION_DISABLED": "The AI connection is disabled. Enable it in AI setup.",
    "AI_CONFIGURATION_INVALID": (
        "The AI connection is not configured or its target is unavailable. Check AI setup."
    ),
    "EXTRACTION_REQUIRED": "Extract the sources first.",
    "EXTRACTION_INCOMPLETE": "Extraction has not finished successfully yet.",
    "EXTRACTION_COVERAGE_INCOMPLETE": "Extraction did not cover every source. Fix or exclude the failing ones.",
    "NO_EXTRACTED_BLOCKS": "Extraction produced no readable text.",
    "PARTITION_TOO_LARGE": "A source block is too large to analyze.",
    "PLAN_TOO_LARGE": "The sources are too large to analyze in one run. Use fewer or smaller sources.",
}


class DiscoveryService:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    def preview(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        requested_clarification_revision: int,
        extraction_run_id: uuid.UUID,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_inventory:
            raise IntakeError(403, "NOT_ALLOWED", "Missing registry or source permission")
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(409, "REVISION_CONFLICT", "Input revision changed")
            if requested_clarification_revision != session.clarification_revision:
                raise IntakeError(
                    409, "CLARIFICATION_CONFLICT", "Clarification revision changed"
                )
            extraction = self.db.get(OnboardingRun, extraction_run_id)
            if (
                extraction is None
                or extraction.session_id != session_id
                or extraction.stage != "EXTRACT_SOURCES"
            ):
                raise IntakeError(404, "EXTRACTION_NOT_FOUND", "Extraction run not found")
            try:
                body, digest = build_discovery_plan(
                    self.db,
                    session_id=session_id,
                    input_revision=session.input_revision,
                    clarification_revision=session.clarification_revision,
                    extraction_run=extraction,
                    actor=self.svc.actor,
                )
            except ValueError as exc:
                code = str(exc)
                raise IntakeError(
                    422,
                    code,
                    _PREREQUISITE_MESSAGES.get(
                        code,
                        "Inventory discovery cannot start with the current prerequisites",
                    ),
                ) from exc
            plan = OnboardingDiscoveryPlan(
                session_id=session_id,
                plan_version=body["plan_version"],
                body=body,
                digest=digest,
                created_by=self.svc.actor,
            )
            self.db.add(plan)
            self.db.flush()
            self.svc._audit(
                "onboarding.inventory.preview",
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
            "ai": {
                "model": body["ai_configuration"]["model"],
                "target_key": body["ai_configuration"]["target_key"],
            },
            "extraction_run_id": str(extraction_run_id),
            "fixture_mode": body.get("fixture_mode"),
        }

    def analyze(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        requested_clarification_revision: int,
        plan_id: uuid.UUID,
        expected_plan_digest: str,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_inventory:
            raise IntakeError(403, "NOT_ALLOWED", "Missing registry or source permission")
        scope = self.svc._scope("inventory_analyze", session_id)
        fingerprint = self.svc._fingerprint(
            {
                "op": "analyze",
                "plan_id": str(plan_id),
                "plan_digest": expected_plan_digest,
                "input_revision": requested_input_revision,
                "clarification_revision": requested_clarification_revision,
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
            if requested_input_revision != session.input_revision:
                raise IntakeError(409, "REVISION_CONFLICT", "Input revision changed")
            if requested_clarification_revision != session.clarification_revision:
                raise IntakeError(
                    409, "CLARIFICATION_CONFLICT", "Clarification revision changed"
                )
            plan = self.db.get(OnboardingDiscoveryPlan, plan_id)
            if plan is None or plan.session_id != session_id:
                raise IntakeError(404, "PLAN_NOT_FOUND", "Discovery plan not found")
            if plan.digest != expected_plan_digest:
                raise IntakeError(
                    409, "PLAN_STALE", "Discovery plan is stale; preview again"
                )
            if self.svc._active_run(session_id) is not None:
                raise IntakeError(409, "RUN_ACTIVE", "A session run is already active")
            run = self._create_run(session, plan)
            self.svc._add_receipt(scope, idempotency_key, fingerprint, "RUN", run.id)
            self.svc._audit(
                "onboarding.inventory.analyze",
                {"session_id": str(session_id), "run_id": str(run.id)},
                "DISCOVERY_REQUESTED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.svc._run_result(session_id, run.id, access, replayed=False)

    def _create_run(
        self, session: OnboardingSession, plan: OnboardingDiscoveryPlan
    ) -> OnboardingRun:
        body = plan.body
        extraction_id = uuid.UUID(body["extraction_run_id"])
        run_id = uuid.uuid4()
        number = (
            self.db.query(func.max(OnboardingRun.run_number))
            .filter(OnboardingRun.session_id == session.id)
            .scalar()
            or 0
        ) + 1
        job = JobQueue(self.db).enqueue(
            job_type=DISCOVER_JOB_TYPE,
            payload={
                "run_id": str(run_id),
                "session_id": str(session.id),
                "input_revision": session.input_revision,
                "plan_id": str(plan.id),
                "plan_digest": plan.digest,
                "requested_by": self.svc.actor,
            },
            idempotency_key=f"onboarding:discover:{run_id}",
            created_by=self.svc.actor,
            max_attempts=settings.onboarding_job_max_attempts,
            allow_reserved=True,
        )
        run = OnboardingRun(
            id=run_id,
            session_id=session.id,
            run_number=number,
            stage=STAGE_DISCOVER,
            input_revision=session.input_revision,
            manifest={"discovery": True},
            manifest_digest=canonical_digest_obj({"discovery": True}),
            extraction_run_id=extraction_id,
            discovery_plan_id=plan.id,
            plan=plan.body,
            plan_digest=plan.digest,
            job_id=uuid.UUID(job["id"]),
            created_by=self.svc.actor,
        )
        self.db.add(run)
        return run

    def list_proposal_sets(
        self, session_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_inventory:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        rows = (
            self.db.query(OnboardingProposalSet)
            .filter(OnboardingProposalSet.session_id == session_id)
            .order_by(OnboardingProposalSet.created_at.desc())
            .all()
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "discovery_run_id": str(r.discovery_run_id),
                    "edit_version": r.edit_version,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
            "total": len(rows),
        }

    def get_proposal_set(
        self, session_id: uuid.UUID, set_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_inventory:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        row = self._get_set(session_id, set_id)
        proposals = (
            self.db.query(OnboardingInventoryProposal)
            .filter(OnboardingInventoryProposal.set_id == set_id)
            .count()
        )
        return {
            "id": str(row.id),
            "session_id": str(session_id),
            "discovery_run_id": str(row.discovery_run_id),
            "edit_version": row.edit_version,
            "content_digest": row.content_digest,
            "proposal_count": proposals,
            "current_reconciliation_id": (
                str(row.current_reconciliation_id)
                if row.current_reconciliation_id
                else None
            ),
        }

    def list_proposals(
        self, session_id: uuid.UUID, set_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_inventory:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        self._get_set(session_id, set_id)
        svc = ProposalService(self.svc, set_id)
        return {"items": svc.effective_proposals()}

    def get_proposal(
        self,
        session_id: uuid.UUID,
        set_id: uuid.UUID,
        proposal_id: uuid.UUID,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_inventory:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        self._get_set(session_id, set_id)
        svc = ProposalService(self.svc, set_id)
        item = svc.get_proposal(proposal_id)
        if item is None:
            raise IntakeError(404, "PROPOSAL_NOT_FOUND", "Proposal not found")
        return item

    def refresh_matches(
        self,
        session_id: uuid.UUID,
        set_id: uuid.UUID,
        *,
        expected_edit_version: int,
        expected_row_version: int,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_inventory:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        session = self.svc._get_session(session_id, lock=True)
        proposal_set = self._get_set(session_id, set_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version)
            if expected_edit_version != proposal_set.edit_version:
                raise IntakeError(409, "EDIT_VERSION_CONFLICT", "Draft changed")
            snapshot = capture_snapshot(self.db)
            proposals = ProposalService(self.svc, set_id).raw_proposals()
            recon = reconcile_proposals(
                [{"id": str(p.id), "entity_type": p.entity_type, "generated": p.generated} for p in proposals],
                snapshot,
            )
            row = OnboardingReconciliationSnapshot(
                set_id=set_id,
                edit_version=proposal_set.edit_version + 1,
                registry_projection={"digest": snapshot.digest},
                registry_digest=snapshot.digest,
                matching_rule_version=recon["matching_rule_version"],
                matches=recon["matches"],
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
    ) -> OnboardingProposalSet:
        query = self.db.query(OnboardingProposalSet).filter(
            OnboardingProposalSet.id == set_id,
            OnboardingProposalSet.session_id == session_id,
        )
        if lock:
            query = query.with_for_update().populate_existing()
        row = query.one_or_none()
        if row is None:
            raise IntakeError(404, "SET_NOT_FOUND", "Proposal set not found")
        return row