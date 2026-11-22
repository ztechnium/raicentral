"""Incremental update coordinator: baselines, delta plans, findings (Step 08)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import (
    OnboardingApplication,
    OnboardingAppliedBinding,
    OnboardingBaseline,
    OnboardingChangeFinding,
    OnboardingDeltaPlan,
    OnboardingGovernanceClause,
    OnboardingGovernanceProposalSet,
    OnboardingImpactEdge,
    OnboardingInventoryProposal,
    OnboardingProposalSet,
    OnboardingRun,
    OnboardingSession,
    OnboardingSourceLineage,
    OnboardingUpdateDecision,
    OnboardingUpdateReconciliation,
    OnboardingVerificationAttempt,
    OnboardingVerificationDispatch,
    OnboardingVerificationImpact,
    OnboardingVerificationPlan,
)
from rai_gateway.onboarding.change_sets import ChangeSetService
from rai_gateway.onboarding.delta_baseline import (
    capture_current_domain,
    list_baseline_choices,
)
from rai_gateway.onboarding.delta_reconciliation import reconcile_views
from rai_gateway.onboarding.service import (
    DELTA_JOB_TYPE,
    STAGE_ANALYZE_DELTA,
    Access,
    IntakeError,
    IntakeService,
    canonical_digest_obj,
    utcnow,
)
from rai_gateway.sdk.jobs import JobQueue


def _require_update_enabled() -> None:
    if not settings.onboarding_update_workflow_enabled:
        raise IntakeError(
            503,
            "UPDATE_WORKFLOW_DISABLED",
            "Incremental update workflow is not enabled in this deployment",
            retryable=False,
        )


class DeltaUpdateService:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db: Session = svc.db

    def list_baselines(self, session_id: uuid.UUID, access: Access) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        _require_update_enabled()
        self.svc._get_session(session_id)
        confirmed = (
            self.db.query(OnboardingBaseline)
            .filter(
                OnboardingBaseline.session_id == session_id,
                OnboardingBaseline.status == "CONFIRMED",
            )
            .order_by(OnboardingBaseline.confirmed_at.desc())
            .all()
        )
        return {
            "choices": list_baseline_choices(self.db, session_id),
            "confirmed": [
                {
                    "id": str(b.id),
                    "baseline_kind": b.baseline_kind,
                    "domain_digest": b.domain_digest,
                    "confirmed_at": b.confirmed_at.isoformat() if b.confirmed_at else None,
                }
                for b in confirmed
            ],
        }

    def review_baseline(
        self,
        session_id: uuid.UUID,
        body: dict[str, Any],
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        _require_update_enabled()
        self.svc._get_session(session_id)
        kind = body.get("baseline_kind")
        projection, digest = capture_current_domain(self.db)
        baseline_id = uuid.uuid4()
        row = OnboardingBaseline(
            id=baseline_id,
            session_id=session_id,
            baseline_kind=kind or "REVIEWED_CURRENT_STATE",
            application_receipt_id=body.get("application_receipt_id"),
            domain_projection=projection,
            domain_digest=digest,
            scope_notes=body.get("scope_notes"),
            status="PREVIEW",
            confirmed_by=self.svc.actor,
        )
        self.db.add(row)
        self.db.commit()
        return {
            "baseline_id": str(baseline_id),
            "baseline_kind": row.baseline_kind,
            "domain_digest": digest,
            "status": row.status,
            "record_count": projection.get("record_count", 0),
        }

    def confirm_baseline(
        self,
        session_id: uuid.UUID,
        baseline_id: uuid.UUID,
        *,
        expected_row_version: int,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        _require_update_enabled()
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            row = self.db.get(OnboardingBaseline, baseline_id)
            if row is None or row.session_id != session_id:
                raise IntakeError(404, "BASELINE_NOT_FOUND", "Baseline not found")
            if row.status == "CONFIRMED":
                self.db.rollback()
                return {"baseline_id": str(row.id), "status": row.status}
            row.status = "CONFIRMED"
            row.confirmed_by = self.svc.actor
            row.confirmed_at = utcnow()
            session.confirmed_baseline_id = baseline_id
            session.update_status = "BASELINE_CONFIRMED"
            session.row_version += 1
            session.updated_by = self.svc.actor
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {"baseline_id": str(baseline_id), "status": "CONFIRMED"}

    def create_update_session(
        self,
        *,
        title: str,
        baseline_session_id: uuid.UUID,
        baseline_id: uuid.UUID | None,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        _require_update_enabled()
        self.svc._get_session(baseline_session_id)
        if baseline_id:
            bl = self.db.get(OnboardingBaseline, baseline_id)
            if bl is None or bl.session_id != baseline_session_id:
                raise IntakeError(404, "BASELINE_NOT_FOUND", "Baseline not found")
        scope = self.svc._scope("create_update_session", None)
        fingerprint = self.svc._fingerprint(
            {
                "op": "create_update_session",
                "title": title,
                "baseline_session_id": str(baseline_session_id),
            }
        )
        receipt = self.svc._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is None:
            session_id = uuid.uuid4()
            try:
                self.db.add(
                    OnboardingSession(
                        id=session_id,
                        title=self.svc._clean_title(title),
                        lifecycle="OPEN",
                        session_kind="UPDATE",
                        baseline_session_id=baseline_session_id,
                        confirmed_baseline_id=baseline_id,
                        update_status="SOURCE_UPDATED",
                        row_version=1,
                        input_revision=0,
                        created_by=self.svc.actor,
                        updated_by=self.svc.actor,
                    )
                )
                self.svc._add_receipt(
                    scope, idempotency_key, fingerprint, "SESSION", session_id
                )
                self.svc._audit(
                    "onboarding.update_session.create",
                    {
                        "session_id": str(session_id),
                        "baseline_session_id": str(baseline_session_id),
                    },
                    "UPDATE_SESSION_CREATED",
                )
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        else:
            session_id = receipt.resource_id
        return self.svc.session_view(session_id, access)

    def record_lineage(
        self,
        session_id: uuid.UUID,
        body: dict[str, Any],
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_manage:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        _require_update_enabled()
        session = self.svc._get_session(session_id, lock=True)
        expected = body.get("expected_row_version")
        self.svc._check_mutable(session, int(expected))
        prev_id = uuid.UUID(str(body["previous_revision_id"]))
        new_id = uuid.UUID(str(body["new_revision_id"]))
        if prev_id == new_id:
            raise IntakeError(422, "LINEAGE_CYCLE", "Cannot relate a revision to itself")
        row = OnboardingSourceLineage(
            session_id=session_id,
            previous_revision_id=prev_id,
            new_revision_id=new_id,
            relationship=body["relationship"],
            effective_notes=body.get("effective_notes"),
            confirmed_by=self.svc.actor,
        )
        self.db.add(row)
        session.row_version += 1
        session.updated_by = self.svc.actor
        try:
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            raise IntakeError(409, "LINEAGE_CONFLICT", "Lineage already recorded") from exc
        return {"id": str(row.id), "relationship": row.relationship}

    def preview_delta_plan(
        self,
        session_id: uuid.UUID,
        baseline_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        _require_update_enabled()
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(409, "REVISION_CONFLICT", "Input revision changed")
            baseline = self.db.get(OnboardingBaseline, baseline_id)
            if baseline is None:
                raise IntakeError(404, "BASELINE_NOT_FOUND", "Baseline not found")
            body = {
                "baseline_id": str(baseline_id),
                "baseline_digest": baseline.domain_digest,
                "baseline_session_id": str(session.baseline_session_id or session_id),
                "input_revision": session.input_revision,
                "compiler_version": "step08-v1",
            }
            digest = canonical_digest_obj(body)
            plan_id = uuid.uuid4()
            plan = OnboardingDeltaPlan(
                id=plan_id,
                session_id=session_id,
                baseline_id=baseline_id,
                body=body,
                digest=digest,
                status="PREPARED",
                created_by=self.svc.actor,
            )
            self.db.add(plan)
            session.update_status = "ANALYSIS_NEEDED"
            session.row_version += 1
            session.updated_by = self.svc.actor
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {"delta_plan_id": str(plan_id), "digest": digest, "status": "PREPARED"}

    def enqueue_delta_run(
        self,
        session_id: uuid.UUID,
        plan_id: uuid.UUID,
        *,
        expected_plan_digest: str,
        expected_row_version: int,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        _require_update_enabled()
        session = self.svc._get_session(session_id, lock=True)
        scope = self.svc._scope("delta_run", session_id)
        fingerprint = self.svc._fingerprint({"plan_id": str(plan_id), "digest": expected_plan_digest})
        receipt = self.svc._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is not None:
            self.db.rollback()
            return self.svc._run_result(session_id, receipt.resource_id, access, replayed=True)
        try:
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            plan = self.db.get(OnboardingDeltaPlan, plan_id)
            if plan is None or plan.session_id != session_id:
                raise IntakeError(404, "PLAN_NOT_FOUND", "Delta plan not found")
            if plan.digest != expected_plan_digest:
                raise IntakeError(409, "PLAN_STALE", "Delta plan is stale; preview again")
            if self.svc._active_run(session_id) is not None:
                raise IntakeError(409, "RUN_ACTIVE", "A session run is already active")
            run = self._create_delta_run(session, plan)
            self.svc._add_receipt(scope, idempotency_key, fingerprint, "RUN", run.id)
            plan.status = "RUNNING"
            session.update_status = "ANALYSIS_NEEDED"
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.svc._run_result(session_id, run.id, access, replayed=False)

    def _create_delta_run(
        self, session: OnboardingSession, plan: OnboardingDeltaPlan
    ) -> OnboardingRun:
        run_id = uuid.uuid4()
        number = (
            self.db.query(func.max(OnboardingRun.run_number))
            .filter(OnboardingRun.session_id == session.id)
            .scalar()
            or 0
        ) + 1
        job = JobQueue(self.db).enqueue(
            job_type=DELTA_JOB_TYPE,
            payload={
                "run_id": str(run_id),
                "session_id": str(session.id),
                "delta_plan_id": str(plan.id),
                "plan_digest": plan.digest,
                "requested_by": self.svc.actor,
            },
            idempotency_key=f"onboarding:delta:{run_id}",
            created_by=self.svc.actor,
            max_attempts=settings.onboarding_job_max_attempts,
            allow_reserved=True,
        )
        run = OnboardingRun(
            id=run_id,
            session_id=session.id,
            run_number=number,
            stage=STAGE_ANALYZE_DELTA,
            input_revision=session.input_revision,
            manifest={"delta": True},
            manifest_digest=canonical_digest_obj({"delta": True}),
            delta_plan_id=plan.id,
            plan=plan.body,
            plan_digest=plan.digest,
            job_id=uuid.UUID(job["id"]),
            created_by=self.svc.actor,
        )
        self.db.add(run)
        return run

    def get_summary(self, session_id: uuid.UUID, access: Access) -> dict[str, Any]:
        if not access.can_read:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        session = self.svc._get_session(session_id)
        plan = (
            self.db.query(OnboardingDeltaPlan)
            .filter(OnboardingDeltaPlan.session_id == session_id)
            .order_by(OnboardingDeltaPlan.created_at.desc())
            .first()
        )
        if plan is None:
            return {"status": session.update_status or "SOURCE_UPDATED", "findings": 0}
        findings = (
            self.db.query(OnboardingChangeFinding)
            .filter(OnboardingChangeFinding.delta_plan_id == plan.id)
            .count()
        )
        counts: dict[str, int] = {}
        for row in (
            self.db.query(OnboardingChangeFinding.change_type, func.count())
            .filter(OnboardingChangeFinding.delta_plan_id == plan.id)
            .group_by(OnboardingChangeFinding.change_type)
            .all()
        ):
            counts[row[0]] = row[1]
        impacts = (
            self.db.query(OnboardingVerificationImpact)
            .filter(
                OnboardingVerificationImpact.delta_plan_id == plan.id,
                OnboardingVerificationImpact.applicability.in_(("STALE", "REVIEW_REQUIRED")),
            )
            .count()
        )
        return {
            "baseline_id": str(plan.baseline_id),
            "delta_plan_id": str(plan.id),
            "source_delta_counts": counts,
            "status": session.update_status or "REVIEW_REQUIRED",
            "verification_scenarios_needing_review": impacts,
            "finding_count": findings,
            "applied_domain_changes": 0,
        }

    def list_findings(
        self, session_id: uuid.UUID, plan_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        plan = self.db.get(OnboardingDeltaPlan, plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Delta plan not found")
        rows = (
            self.db.query(OnboardingChangeFinding)
            .filter(OnboardingChangeFinding.delta_plan_id == plan_id)
            .order_by(OnboardingChangeFinding.ordinal)
            .all()
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "ordinal": r.ordinal,
                    "change_type": r.change_type,
                    "identity": r.identity,
                    "summary": r.summary,
                    "impact_level": r.impact_level,
                    "resolution_status": r.resolution_status,
                }
                for r in rows
            ]
        }

    def list_impacts(
        self, session_id: uuid.UUID, plan_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        plan = self.db.get(OnboardingDeltaPlan, plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Delta plan not found")
        rows = (
            self.db.query(OnboardingImpactEdge)
            .filter(OnboardingImpactEdge.delta_plan_id == plan_id)
            .order_by(OnboardingImpactEdge.ordinal)
            .limit(500)
            .all()
        )
        return {
            "items": [
                {
                    "ordinal": r.ordinal,
                    "finding_ordinal": r.finding_ordinal,
                    "edge_type": r.edge_type,
                    "reference_kind": r.reference_kind,
                    "reference_id": r.reference_id,
                    "confidence": r.confidence,
                    "reason": r.reason,
                }
                for r in rows
            ]
        }

    def get_reconciliation(
        self, session_id: uuid.UUID, plan_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        plan = self.db.get(OnboardingDeltaPlan, plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Delta plan not found")
        row = (
            self.db.query(OnboardingUpdateReconciliation)
            .filter(OnboardingUpdateReconciliation.delta_plan_id == plan_id)
            .order_by(OnboardingUpdateReconciliation.edit_version.desc())
            .first()
        )
        if row is None:
            return {"status": "PENDING"}
        return {
            "reconciliation_id": str(row.id),
            "edit_version": row.edit_version,
            "difference": row.difference,
            "captured_at": row.captured_at.isoformat() if row.captured_at else None,
        }

    def refresh_reconciliation(
        self,
        session_id: uuid.UUID,
        plan_id: uuid.UUID,
        *,
        expected_version: int,
        access: Access,
    ) -> dict[str, Any]:
        """Domain drift refresh without LLM."""
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        plan = self.db.get(OnboardingDeltaPlan, plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Delta plan not found")
        baseline = self.db.get(OnboardingBaseline, plan.baseline_id)
        if baseline is None:
            raise IntakeError(404, "BASELINE_NOT_FOUND", "Baseline missing")
        projection, digest = capture_current_domain(self.db)
        prior = (
            self.db.query(OnboardingUpdateReconciliation)
            .filter(OnboardingUpdateReconciliation.delta_plan_id == plan_id)
            .order_by(OnboardingUpdateReconciliation.edit_version.desc())
            .first()
        )
        if prior and prior.edit_version != expected_version:
            raise IntakeError(409, "RECONCILIATION_STALE", "Reconciliation version changed")
        difference = reconcile_views(
            baseline_projection=baseline.domain_projection,
            baseline_digest=baseline.domain_digest,
            current_projection=projection,
            source_diffs=[],
        )
        version = (prior.edit_version if prior else 0) + 1
        row = OnboardingUpdateReconciliation(
            delta_plan_id=plan_id,
            edit_version=version,
            baseline_digest=baseline.domain_digest,
            current_domain_digest=digest,
            source_digest=plan.digest,
            difference=difference,
        )
        self.db.add(row)
        self.db.commit()
        return {"edit_version": version, "difference": difference}

    def save_decision(
        self,
        session_id: uuid.UUID,
        body: dict[str, Any],
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_governance:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        finding_id = uuid.UUID(str(body["finding_id"]))
        finding = self.db.get(OnboardingChangeFinding, finding_id)
        if finding is None:
            raise IntakeError(404, "FINDING_NOT_FOUND", "Finding not found")
        plan = self.db.get(OnboardingDeltaPlan, finding.delta_plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "FINDING_NOT_FOUND", "Finding not found")
        expected = int(body["expected_edit_revision"])
        count = (
            self.db.query(OnboardingUpdateDecision)
            .filter(OnboardingUpdateDecision.session_id == session_id)
            .count()
        )
        if count != expected:
            raise IntakeError(409, "REVISION_CONFLICT", "Decision revision conflict")
        row = OnboardingUpdateDecision(
            session_id=session_id,
            finding_id=finding_id,
            decision=body["decision"],
            notes=body.get("notes"),
            actor=self.svc.actor,
            edit_revision=expected + 1,
        )
        self.db.add(row)
        finding.resolution_status = "RESOLVED"
        self.db.commit()
        return {"decision_id": str(row.id), "edit_revision": row.edit_revision}

    def list_verification_impact(
        self, session_id: uuid.UUID, plan_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_read:
            raise IntakeError(403, "NOT_ALLOWED", "Missing permission")
        plan = self.db.get(OnboardingDeltaPlan, plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Delta plan not found")
        rows = (
            self.db.query(OnboardingVerificationImpact)
            .filter(OnboardingVerificationImpact.delta_plan_id == plan_id)
            .order_by(OnboardingVerificationImpact.ordinal)
            .all()
        )
        return {
            "items": [
                {
                    "scenario_key": r.scenario_key,
                    "applicability": r.applicability,
                    "reason": r.reason,
                    "target_digest": r.target_digest,
                }
                for r in rows
            ]
        }

    def prepare_review(
        self,
        session_id: uuid.UUID,
        body: dict[str, Any],
        access: Access,
    ) -> dict[str, Any]:
        """Hand off to Step 06 change-set preparation; no domain writes here."""
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        selection = body.get("selection") or {}
        return ChangeSetService(self.svc).prepare_change_set(
            session_id,
            selection,
            access,
            expected_row_version=int(body["expected_row_version"]),
            requested_input_revision=int(body["requested_input_revision"]),
        )

    @staticmethod
    def load_session_assertion_sources(
        db: Session, session_id: uuid.UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        inv_set = (
            db.query(OnboardingProposalSet)
            .filter(OnboardingProposalSet.session_id == session_id)
            .order_by(OnboardingProposalSet.created_at.desc())
            .first()
        )
        inventory: list[dict[str, Any]] = []
        if inv_set is not None:
            for proposal in (
                db.query(OnboardingInventoryProposal)
                .filter(OnboardingInventoryProposal.set_id == inv_set.id)
                .order_by(OnboardingInventoryProposal.created_at)
                .all()
            ):
                inventory.append(
                    {
                        "id": str(proposal.id),
                        "entity_type": proposal.entity_type,
                        "generated": proposal.generated,
                    }
                )
        gov_set = (
            db.query(OnboardingGovernanceProposalSet)
            .filter(OnboardingGovernanceProposalSet.session_id == session_id)
            .order_by(OnboardingGovernanceProposalSet.created_at.desc())
            .first()
        )
        clauses: list[dict[str, Any]] = []
        if gov_set is not None:
            rows = (
                db.query(OnboardingGovernanceClause)
                .filter(OnboardingGovernanceClause.set_id == gov_set.id)
                .order_by(OnboardingGovernanceClause.ordinal)
                .all()
            )
            clauses = [
                {
                    "id": str(r.id),
                    "ordinal": r.ordinal,
                    "requirement": r.requirement,
                    "outcome": r.outcome,
                    "evidence": r.evidence,
                }
                for r in rows
            ]
        return inventory, clauses

    @staticmethod
    def load_applied_bindings(db: Session, baseline_session_id: uuid.UUID) -> list[dict[str, Any]]:
        app = (
            db.query(OnboardingApplication)
            .filter(
                OnboardingApplication.session_id == baseline_session_id,
                OnboardingApplication.status == "SUCCEEDED",
            )
            .order_by(OnboardingApplication.created_at.desc())
            .first()
        )
        if app is None:
            return []
        rows = (
            db.query(OnboardingAppliedBinding)
            .filter(OnboardingAppliedBinding.application_id == app.id)
            .all()
        )
        return [
            {
                "target_kind": r.target_kind,
                "target_id": str(r.target_id),
                "binding_key": r.binding_key,
            }
            for r in rows
        ]

    @staticmethod
    def load_verification_dispatches(
        db: Session, baseline_session_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        plan = (
            db.query(OnboardingVerificationPlan)
            .filter(OnboardingVerificationPlan.session_id == baseline_session_id)
            .order_by(OnboardingVerificationPlan.created_at.desc())
            .first()
        )
        if plan is None:
            return []
        attempt = (
            db.query(OnboardingVerificationAttempt)
            .filter(OnboardingVerificationAttempt.plan_id == plan.id)
            .order_by(OnboardingVerificationAttempt.attempt_number.desc())
            .first()
        )
        if attempt is None:
            return []
        dispatches = (
            db.query(OnboardingVerificationDispatch)
            .filter(OnboardingVerificationDispatch.attempt_id == attempt.id)
            .order_by(OnboardingVerificationDispatch.ordinal)
            .all()
        )
        return [
            {
                "attempt_id": str(attempt.id),
                "scenario_key": d.scenario_key,
                "evidence": d.evidence,
            }
            for d in dispatches
        ]
