"""Control activation plans and enqueue (Step 07)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingActivationPlan,
    OnboardingActivationReceipt,
    OnboardingRun,
    OnboardingSession,
)
from rai_gateway.onboarding.runtime_context import RuntimeContextService
from rai_gateway.onboarding.service import (
    ACTIVATE_JOB_TYPE,
    STAGE_ACTIVATE,
    Access,
    IntakeError,
    IntakeService,
    canonical_digest_obj,
    sha256_hex,
    utcnow,
)
from rai_gateway.onboarding.verification_schemas import RUNTIME_CONTRACT_VERSION
from rai_gateway.sdk.jobs import JobQueue


class ActivationCoordinator:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db
        self.context_svc = RuntimeContextService(svc)

    def prepare_activation_plan(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        expected_selection_digest: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        ctx = self.context_svc.require_context(session_id, expected_row_version=expected_row_version)
        if ctx.selection_digest != expected_selection_digest:
            raise IntakeError(409, "SELECTION_STALE", "Runtime selection digest mismatch")
        payload = {
            "contract_version": RUNTIME_CONTRACT_VERSION,
            "runtime_context_id": str(ctx.id),
            "selection_digest": ctx.selection_digest,
            "delegation_mode": ctx.delegation_mode,
            "selected": ctx.selected,
            "controls_to_activate": (ctx.selected or {}).get("controls") or {},
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(raw.encode("utf-8")) > 256 * 1024:
            raise IntakeError(422, "PAYLOAD_TOO_LARGE", "Activation payload too large")
        digest = canonical_digest_obj(payload)
        plan = OnboardingActivationPlan(
            session_id=session_id,
            runtime_context_id=ctx.id,
            contract_version=RUNTIME_CONTRACT_VERSION,
            status="PREPARED",
            payload=payload,
            payload_digest=digest,
            created_by=self.svc.actor,
        )
        self.db.add(plan)
        self.db.commit()
        return self._plan_view(plan)

    def enqueue_activate(
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
        scope = self.svc._scope("activate_controls", session_id)
        fingerprint = sha256_hex(f"{plan_id}:{expected_payload_digest}")
        receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is not None:
            plan = self.db.get(OnboardingActivationPlan, receipt.plan_id)
            assert plan is not None
            return self._enqueue_view(plan, replayed=True)
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.context_svc.require_context(
                session_id, expected_row_version=expected_row_version
            )
            if self.svc._active_run(session_id) is not None:
                raise IntakeError(409, "RUN_ACTIVE", "A session run is already active")
            plan = self.db.get(OnboardingActivationPlan, plan_id)
            if plan is None or plan.session_id != session_id:
                raise IntakeError(404, "PLAN_NOT_FOUND", "Activation plan not found")
            if plan.status != "PREPARED":
                raise IntakeError(409, "PLAN_NOT_PREPARED", "Plan is not prepared")
            if plan.payload_digest != expected_payload_digest:
                raise IntakeError(409, "PAYLOAD_STALE", "Plan digest mismatch")
            run = self._create_run(session, plan)
            plan.status = "ACTIVATING"
            self._add_receipt(scope, idempotency_key, fingerprint, plan.id)
            self.svc._audit(
                "onboarding.activate.enqueue",
                {
                    "session_id": str(session_id),
                    "plan_id": str(plan.id),
                    "run_id": str(run.id),
                },
                "ACTIVATE_REQUESTED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self._enqueue_view(plan, replayed=False)

    def get_plan(
        self, session_id: uuid.UUID, plan_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_read:
            raise IntakeError(403, "NOT_ALLOWED", "Missing read permission")
        plan = self.db.get(OnboardingActivationPlan, plan_id)
        if plan is None or plan.session_id != session_id:
            raise IntakeError(404, "PLAN_NOT_FOUND", "Activation plan not found")
        return self._plan_view(plan)

    def execute_activation_job(
        self, db: Session, *, plan_id: uuid.UUID, actor: str
    ) -> dict[str, Any]:
        plan = db.get(OnboardingActivationPlan, plan_id)
        if plan is None:
            raise ValueError("PLAN_NOT_FOUND")
        plan.status = "ACTIVATED"
        return {
            "plan_id": str(plan.id),
            "outcome": "STUB_ACTIVATED",
            "activated_at": utcnow().isoformat(),
            "actor": actor,
        }

    def _create_run(
        self, session: OnboardingSession, plan: OnboardingActivationPlan
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
            "runtime_context_id": str(plan.runtime_context_id),
        }
        digest = canonical_digest_obj(manifest)
        job = JobQueue(self.db).enqueue(
            job_type=ACTIVATE_JOB_TYPE,
            idempotency_key=f"onboarding-activate:{plan.id}",
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
            stage=STAGE_ACTIVATE,
            input_revision=session.input_revision,
            manifest=manifest,
            manifest_digest=digest,
            activation_plan_id=plan.id,
            job_id=job.id,
            created_by=self.svc.actor,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def _plan_view(self, plan: OnboardingActivationPlan) -> dict[str, Any]:
        return {
            "id": str(plan.id),
            "session_id": str(plan.session_id),
            "runtime_context_id": str(plan.runtime_context_id),
            "status": plan.status,
            "contract_version": plan.contract_version,
            "payload_digest": plan.payload_digest,
            "payload": plan.payload,
            "created_by": plan.created_by,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
        }

    def _enqueue_view(
        self, plan: OnboardingActivationPlan, *, replayed: bool
    ) -> dict[str, Any]:
        body = self._plan_view(plan)
        body["replayed"] = replayed
        return body

    def _lookup_receipt(
        self, scope: str, key: str, fingerprint: str
    ) -> OnboardingActivationReceipt | None:
        receipt = (
            self.db.query(OnboardingActivationReceipt)
            .filter(
                OnboardingActivationReceipt.scope == scope,
                OnboardingActivationReceipt.key_digest == sha256_hex(key),
            )
            .one_or_none()
        )
        if receipt is None:
            return None
        if receipt.fingerprint != fingerprint:
            raise IntakeError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This idempotency key was already used for a different request",
            )
        return receipt

    def _add_receipt(
        self, scope: str, key: str, fingerprint: str, plan_id: uuid.UUID
    ) -> None:
        self.db.add(
            OnboardingActivationReceipt(
                plan_id=plan_id,
                scope=scope,
                key_digest=sha256_hex(key),
                fingerprint=fingerprint,
            )
        )