"""Worker entry for onboarding control activation (Step 07 stub)."""

from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import OnboardingActivationPlan, OnboardingRun
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.activation import ActivationCoordinator
from rai_gateway.onboarding.preparation import TerminalError, publish_failure, publish_success
from rai_gateway.onboarding.service import ACTIVATE_JOB_TYPE, STAGE_ACTIVATE, IntakeService
from rai_gateway.onboarding.storage import OnboardingSourceStore, get_source_store

SessionFactory = Callable[[], Session]


def _validate_binding(db: Session, job: BackgroundJob) -> tuple[OnboardingRun, OnboardingActivationPlan]:
    payload = job.payload if isinstance(job.payload, dict) else {}
    try:
        run_id = uuid.UUID(str(payload.get("run_id")))
        plan_id = uuid.UUID(str(payload.get("plan_id")))
    except ValueError as exc:
        raise TerminalError("INVALID_RUN_BINDING") from exc
    run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one_or_none()
    plan = db.get(OnboardingActivationPlan, plan_id)
    if (
        run is None
        or plan is None
        or run.job_id != job.id
        or job.job_type != ACTIVATE_JOB_TYPE
        or run.stage != STAGE_ACTIVATE
        or payload.get("session_id") != str(run.session_id)
        or payload.get("payload_digest") != run.manifest_digest
        or run.activation_plan_id != plan.id
    ):
        raise TerminalError("INVALID_RUN_BINDING")
    return run, plan


def process_activate_job(
    session_factory: SessionFactory,
    _store: OnboardingSourceStore,
    job_id: uuid.UUID,
    token: str,
) -> str:
    db = session_factory()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        try:
            run, plan = _validate_binding(db, job)
        except TerminalError as exc:
            from rai_gateway.onboarding.preparation import _fail_unbound

            _fail_unbound(db, job_id, token, exc.code)
            return "invalid_binding"
        run_id = run.id
        plan_id = plan.id
        actor = (job.payload or {}).get("actor") or "onboarding-worker"
        db.rollback()
    finally:
        db.close()

    db = session_factory()
    try:
        coordinator = ActivationCoordinator(
            IntakeService(db, get_source_store(), actor=actor)
        )
        try:
            outcome = coordinator.execute_activation_job(db, plan_id=plan_id, actor=actor)
            db.commit()
        except Exception:
            db.rollback()
            plan = db.get(OnboardingActivationPlan, plan_id)
            if plan:
                plan.status = "FAILED"
                db.commit()
            ok = publish_failure(
                db, run_id, job_id, token, code="INTERNAL_ERROR", terminal=True
            )
            return "failed" if ok else "lost_ownership"
        ok = publish_success(db, run_id, job_id, token, outcome)
        return "succeeded" if ok else "lost_ownership"
    finally:
        db.close()