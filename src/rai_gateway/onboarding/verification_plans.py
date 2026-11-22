"""Verification plan compilation and runtime verify enqueue (Step 07)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingActivationPlan,
    OnboardingRun,
    OnboardingSession,
    OnboardingVerificationAttempt,
    OnboardingVerificationDispatch,
    OnboardingVerificationPlan,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.runtime_context import RuntimeContextService
from rai_gateway.onboarding.service import (
    VERIFY_JOB_TYPE,
    STAGE_VERIFY,
    Access,
    IntakeError,
    IntakeService,
    canonical_digest_obj,
    utcnow,
)
from rai_gateway.onboarding.verification_schemas import (
    VERIFICATION_CONTRACT_VERSION,
    VERIFICATION_LIMITS,
    default_verification_scenarios,
)
from rai_gateway.sdk.jobs import JobQueue


class VerificationCoordinator:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    def prepare_verification_plan(
        self,
        session_id: uuid.UUID,
        *,
        activation_plan_id: uuid.UUID,
        expected_row_version: int,
        scenarios: list[dict[str, Any]] | None,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        ctx_svc = RuntimeContextService(self.svc)
        ctx = ctx_svc.require_context(session_id, expected_row_version=expected_row_version)
        activation = self.db.get(OnboardingActivationPlan, activation_plan_id)
        if activation is None or activation.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Activation plan not found")
        if activation.status != "ACTIVATED":
            raise IntakeError(
                409,
                "ACTIVATION_REQUIRED",
                "Controls must be activated before verification",
            )
        if activation.runtime_context_id != ctx.id:
            raise IntakeError(409, "CONTEXT_MISMATCH", "Activation plan context mismatch")
        scenario_list = (
            scenarios if scenarios is not None else default_verification_scenarios(ctx.selected or {})
        )
        if len(scenario_list) > VERIFICATION_LIMITS["max_scenarios"]:
            raise IntakeError(422, "TOO_MANY_SCENARIOS", "Scenario limit exceeded")
        payload = {
            "contract_version": VERIFICATION_CONTRACT_VERSION,
            "activation_plan_id": str(activation.id),
            "runtime_context_id": str(ctx.id),
            "selection_digest": ctx.selection_digest,
            "scenarios": scenario_list,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(raw.encode("utf-8")) > VERIFICATION_LIMITS["max_payload_bytes"]:
            raise IntakeError(422, "PAYLOAD_TOO_LARGE", "Verification payload too large")
        digest = canonical_digest_obj(payload)
        plan = OnboardingVerificationPlan(
            session_id=session_id,
            activation_plan_id=activation.id,
            contract_version=VERIFICATION_CONTRACT_VERSION,
            status="PREPARED",
            payload=payload,
            payload_digest=digest,
            created_by=self.svc.actor,
        )
        self.db.add(plan)
        self.db.commit()
        return self._plan_view(plan)

    def enqueue_verify(
        self,
        session_id: uuid.UUID,
        *,
        plan_id: uuid.UUID,
        expected_row_version: int,
        expected_payload_digest: str,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        ctx_svc = RuntimeContextService(self.svc)
        session = self.svc._get_session(session_id, lock=True)
        try:
            ctx_svc.require_context(session_id, expected_row_version=expected_row_version)
            if self.svc._active_run(session_id) is not None:
                raise IntakeError(409, "RUN_ACTIVE", "A session run is already active")
            plan = self.db.get(OnboardingVerificationPlan, plan_id)
            if plan is None or plan.session_id != session_id:
                raise IntakeError(404, "PLAN_NOT_FOUND", "Verification plan not found")
            if plan.status != "PREPARED":
                raise IntakeError(409, "PLAN_NOT_PREPARED", "Plan is not prepared")
            if plan.payload_digest != expected_payload_digest:
                raise IntakeError(409, "PAYLOAD_STALE", "Plan digest mismatch")
            run = self._create_run(session, plan)
            attempt = OnboardingVerificationAttempt(
                plan_id=plan.id,
                attempt_number=1,
                run_id=run.id,
                job_id=run.job_id,
            )
            self.db.add(attempt)
            self.db.flush()
            for ordinal, scenario in enumerate(plan.payload.get("scenarios") or []):
                self.db.add(
                    OnboardingVerificationDispatch(
                        attempt_id=attempt.id,
                        ordinal=ordinal,
                        scenario_key=str(scenario.get("key") or f"scenario-{ordinal}"),
                        status="PENDING",
                        evidence={},
                    )
                )
            plan.status = "VERIFYING"
            self.svc._audit(
                "onboarding.verify.enqueue",
                {
                    "session_id": str(session_id),
                    "plan_id": str(plan.id),
                    "run_id": str(run.id),
                },
                "VERIFY_REQUESTED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        _ = idempotency_key
        view = self._plan_view(plan)
        view["run_id"] = str(run.id)
        view["attempt_id"] = str(attempt.id)
        view["replayed"] = False
        return view

    def get_plan(
        self, session_id: uuid.UUID, plan_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_read:
            raise IntakeError(403, "NOT_ALLOWED", "Missing read permission")
        plan = self.db.get(OnboardingVerificationPlan, plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Verification plan not found")
        view = self._plan_view(plan)
        latest = (
            self.db.query(OnboardingVerificationAttempt)
            .filter(OnboardingVerificationAttempt.plan_id == plan.id)
            .order_by(OnboardingVerificationAttempt.attempt_number.desc())
            .first()
        )
        if latest:
            view["latest_attempt"] = self._attempt_view(latest)
        return view

    def execute_verification_job(
        self, db: Session, *, plan_id: uuid.UUID, attempt_id: uuid.UUID, actor: str
    ) -> dict[str, Any]:
        plan = db.get(OnboardingVerificationPlan, plan_id)
        attempt = db.get(OnboardingVerificationAttempt, attempt_id)
        if plan is None or attempt is None or attempt.plan_id != plan.id:
            raise ValueError("INVALID_VERIFICATION_BINDING")
        dispatches = (
            db.query(OnboardingVerificationDispatch)
            .filter(OnboardingVerificationDispatch.attempt_id == attempt.id)
            .order_by(OnboardingVerificationDispatch.ordinal)
            .all()
        )
        for dispatch in dispatches:
            dispatch.status = "SUCCEEDED"
            dispatch.evidence = {
                "outcome": "STUB_PASSED",
                "verified_at": utcnow().isoformat(),
                "actor": actor,
                "scenario_key": dispatch.scenario_key,
            }
        attempt.outcome = "SUCCEEDED"
        attempt.ended_at = utcnow()
        plan.status = "VERIFIED"
        return {
            "plan_id": str(plan.id),
            "attempt_id": str(attempt.id),
            "outcome": "STUB_VERIFIED",
            "dispatch_count": len(dispatches),
        }

    def _create_run(
        self, session: OnboardingSession, plan: OnboardingVerificationPlan
    ) -> OnboardingRun:
        run_id = uuid.uuid4()
        number = (
            self.db.query(func.max(OnboardingRun.run_number))
            .filter(OnboardingRun.session_id == session.id)
            .scalar()
            or 0
        ) + 1
        manifest = {
            "plan_id": str(plan.id),
            "payload_digest": plan.payload_digest,
            "activation_plan_id": str(plan.activation_plan_id),
        }
        digest = canonical_digest_obj(manifest)
        job = JobQueue(self.db).enqueue(
            job_type=VERIFY_JOB_TYPE,
            idempotency_key=f"onboarding-verify:{plan.id}",
            allow_reserved=True,
            payload={
                "session_id": str(session.id),
                "run_id": str(run_id),
                "plan_id": str(plan.id),
                "payload_digest": digest,
                "actor": self.svc.actor,
            },
        )
        run = OnboardingRun(
            id=run_id,
            session_id=session.id,
            run_number=number,
            stage=STAGE_VERIFY,
            input_revision=session.input_revision,
            manifest=manifest,
            manifest_digest=digest,
            verification_plan_id=plan.id,
            job_id=job.id,
            created_by=self.svc.actor,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def _plan_view(self, plan: OnboardingVerificationPlan) -> dict[str, Any]:
        return {
            "id": str(plan.id),
            "session_id": str(plan.session_id),
            "activation_plan_id": str(plan.activation_plan_id),
            "status": plan.status,
            "contract_version": plan.contract_version,
            "payload_digest": plan.payload_digest,
            "payload": plan.payload,
            "created_by": plan.created_by,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
        }

    def _attempt_view(self, attempt: OnboardingVerificationAttempt) -> dict[str, Any]:
        dispatches = (
            self.db.query(OnboardingVerificationDispatch)
            .filter(OnboardingVerificationDispatch.attempt_id == attempt.id)
            .order_by(OnboardingVerificationDispatch.ordinal)
            .all()
        )
        job = self.db.get(BackgroundJob, attempt.job_id)
        return {
            "id": str(attempt.id),
            "attempt_number": attempt.attempt_number,
            "run_id": str(attempt.run_id) if attempt.run_id else None,
            "outcome": attempt.outcome,
            "job_status": job.status if job else None,
            "dispatches": [
                {
                    "ordinal": d.ordinal,
                    "scenario_key": d.scenario_key,
                    "status": d.status,
                    "evidence": d.evidence,
                }
                for d in dispatches
            ],
        }