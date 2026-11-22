"""Worker entry for onboarding runtime verification (Step 07)."""

from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import OnboardingRun, OnboardingVerificationAttempt
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.preparation import TerminalError, publish_failure, publish_success
from rai_gateway.onboarding.service import VERIFY_JOB_TYPE, STAGE_VERIFY, IntakeService
from rai_gateway.onboarding.storage import OnboardingSourceStore, get_source_store
from rai_gateway.onboarding.verification_plans import VerificationCoordinator

SessionFactory = Callable[[], Session]


def _validate_binding(
    db: Session, job: BackgroundJob
) -> tuple[OnboardingRun, OnboardingVerificationAttempt, uuid.UUID]:
    payload = job.payload if isinstance(job.payload, dict) else {}
    try:
        run_id = uuid.UUID(str(payload.get("run_id")))
        plan_id = uuid.UUID(str(payload.get("plan_id")))
    except ValueError as exc:
        raise TerminalError("INVALID_RUN_BINDING") from exc
    run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one_or_none()
    attempt = (
        db.query(OnboardingVerificationAttempt)
        .filter(OnboardingVerificationAttempt.run_id == run_id)
        .one_or_none()
    )
    if (
        run is None
        or attempt is None
        or run.job_id != job.id
        or job.job_type != VERIFY_JOB_TYPE
        or run.stage != STAGE_VERIFY
        or payload.get("session_id") != str(run.session_id)
        or run.verification_plan_id != plan_id
    ):
        raise TerminalError("INVALID_RUN_BINDING")
    return run, attempt, plan_id


def process_verify_job(
    session_factory: SessionFactory,
    _store: OnboardingSourceStore,
    job_id: uuid.UUID,
    token: str,
) -> str:
    db = session_factory()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        try:
            run, attempt, plan_id = _validate_binding(db, job)
        except TerminalError as exc:
            from rai_gateway.onboarding.preparation import _fail_unbound

            _fail_unbound(db, job_id, token, exc.code)
            return "invalid_binding"
        run_id = run.id
        attempt_id = attempt.id
        actor = (job.payload or {}).get("actor") or "onboarding-worker"
        db.rollback()
    finally:
        db.close()

    db = session_factory()
    try:
        coordinator = VerificationCoordinator(
            IntakeService(db, get_source_store(), actor=actor)
        )
        result = coordinator.execute_verification_job(
            db, plan_id=plan_id, attempt_id=attempt_id, actor=actor
        )
        run = db.get(OnboardingRun, run_id)
        if run:
            from rai_gateway.onboarding.service import utcnow

            run.ended_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        db = session_factory()
        ok = publish_failure(db, run_id, job_id, token, code="INTERNAL_ERROR", terminal=True)
        return "failed" if ok else "lost_ownership"
    finally:
        db.close()

    db = session_factory()
    try:
        ok = publish_success(db, run_id, job_id, token, result)
        return "succeeded" if ok else "lost_ownership"
    finally:
        db.close()