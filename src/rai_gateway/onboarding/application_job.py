"""Worker entry for onboarding change-set application (Step 06)."""

from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingApplication,
    OnboardingChangeSet,
    OnboardingRun,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.application import ApplicationCoordinator
from rai_gateway.onboarding.application_adapters import ApplyAdapterError
from rai_gateway.onboarding.preparation import (
    TerminalError,
    publish_failure,
    publish_success,
)
from rai_gateway.onboarding.service import APPLY_JOB_TYPE, STAGE_APPLY, IntakeService
from rai_gateway.onboarding.storage import OnboardingSourceStore, get_source_store

SessionFactory = Callable[[], Session]


def _validate_binding(db: Session, job: BackgroundJob) -> tuple[OnboardingRun, OnboardingApplication]:
    payload = job.payload if isinstance(job.payload, dict) else {}
    try:
        run_id = uuid.UUID(str(payload.get("run_id")))
        application_id = uuid.UUID(str(payload.get("application_id")))
    except ValueError as exc:
        raise TerminalError("INVALID_RUN_BINDING") from exc
    run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one_or_none()
    app = db.get(OnboardingApplication, application_id)
    if (
        run is None
        or app is None
        or run.job_id != job.id
        or job.job_type != APPLY_JOB_TYPE
        or run.stage != STAGE_APPLY
        or payload.get("session_id") != str(run.session_id)
        or payload.get("payload_digest") != run.manifest_digest
        or str(app.id) != payload.get("application_id")
    ):
        raise TerminalError("INVALID_RUN_BINDING")
    return run, app


def process_apply_job(
    session_factory: SessionFactory,
    _store: OnboardingSourceStore,
    job_id: uuid.UUID,
    token: str,
) -> str:
    db = session_factory()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        try:
            run, app = _validate_binding(db, job)
        except TerminalError as exc:
            from rai_gateway.onboarding.preparation import _fail_unbound

            _fail_unbound(db, job_id, token, exc.code)
            return "invalid_binding"
        run_id = run.id
        application_id = app.id
        actor = (job.payload or {}).get("actor") or "onboarding-worker"
        db.rollback()
    finally:
        db.close()

    db = session_factory()
    try:
        coordinator = ApplicationCoordinator(
            IntakeService(db, get_source_store(), actor=actor)
        )
        try:
            outcome = coordinator.execute_application_job(
                db, application_id=application_id, actor=actor
            )
        except ApplyAdapterError as exc:
            db.rollback()
            app = db.get(OnboardingApplication, application_id)
            if app:
                app.status = "FAILED"
                cs = db.get(OnboardingChangeSet, app.change_set_id)
                if cs and cs.status == "APPLYING":
                    cs.status = "PREPARED"
                db.commit()
            ok = publish_failure(
                db, run_id, job_id, token, code=exc.code, terminal=True
            )
            return "failed" if ok else "lost_ownership"
        except Exception:
            db.rollback()
            app = db.get(OnboardingApplication, application_id)
            if app:
                app.status = "FAILED"
                cs = db.get(OnboardingChangeSet, app.change_set_id)
                if cs and cs.status == "APPLYING":
                    cs.status = "PREPARED"
                db.commit()
            ok = publish_failure(
                db, run_id, job_id, token, code="INTERNAL_ERROR", terminal=True
            )
            return "failed" if ok else "lost_ownership"
        ok = publish_success(db, run_id, job_id, token, outcome)
        return "succeeded" if ok else "lost_ownership"
    finally:
        db.close()
