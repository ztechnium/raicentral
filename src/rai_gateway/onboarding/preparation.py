"""Prepare-sources job execution (Step 02).

Checks that the manifest's immutable original revisions still exist, match their
saved size/digest and stay within limits. It does not parse business content or
call an AI provider. Execution is at-least-once with ownership-checked
publication: every state change re-verifies the lease token under locks taken in
the consistent order session -> job.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.database import AdminAudit
from rai_gateway.models.onboarding import (
    OnboardingRun,
    OnboardingSession,
    OnboardingSourceRevision,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.service import (
    ACTIVATE_JOB_TYPE,
    APPLY_JOB_TYPE,
    DISCOVER_JOB_TYPE,
    EXTRACT_JOB_TYPE,
    GOVERNANCE_JOB_TYPE,
    JOB_TYPE,
    PROBE_JOB_TYPE,
    STAGE_EXTRACT,
    VERIFY_JOB_TYPE,
    canonical_digest,
)
from rai_gateway.onboarding.storage import (
    SUPPORTED_FAMILIES,
    OnboardingSourceStore,
    SourceIntegrityError,
    SourceStorageError,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue

SUPPORTED_JOB_TYPES = [
    JOB_TYPE,
    EXTRACT_JOB_TYPE,
    DISCOVER_JOB_TYPE,
    GOVERNANCE_JOB_TYPE,
    APPLY_JOB_TYPE,
    ACTIVATE_JOB_TYPE,
    VERIFY_JOB_TYPE,
    PROBE_JOB_TYPE,
]
SessionFactory = Callable[[], Session]


class TerminalError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TemporaryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, action: str, target: dict[str, Any], reason: str) -> None:
    db.add(
        AdminAudit(
            trace_id=uuid.uuid4().hex,
            actor_user_id="onboarding-worker",
            actor_role="onboarding_worker",
            action=action,
            target=target,
            reason=reason,
        )
    )


def _lock_owned(
    db: Session, run_id: uuid.UUID, job_id: uuid.UUID, token: str
) -> tuple[OnboardingRun, OnboardingSession, BackgroundJob] | None:
    """Lock session -> job and verify this attempt still owns the work."""
    run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one_or_none()
    if run is None or run.job_id != job_id:
        return None
    session = (
        db.query(OnboardingSession)
        .filter(OnboardingSession.id == run.session_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    job = (
        db.query(BackgroundJob)
        .filter(BackgroundJob.id == job_id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    db.refresh(run)
    if (
        run.ended_at is not None
        or job.status != BackgroundJobStatus.RUNNING.value
        or job.lease_token != token
        or job.lease_expires_at is None
        or job.lease_expires_at <= _now()
    ):
        return None
    return run, session, job


def _is_latest(db: Session, run: OnboardingRun) -> bool:
    latest = (
        db.query(func.max(OnboardingRun.run_number))
        .filter(
            OnboardingRun.session_id == run.session_id,
            OnboardingRun.stage == run.stage,
        )
        .scalar()
    )
    return latest == run.run_number


def publish_success(
    db: Session,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    token: str,
    result: dict[str, Any],
) -> bool:
    """Atomically finish job + run. False when this attempt lost ownership."""
    try:
        owned = _lock_owned(db, run_id, job_id, token)
        if owned is None:
            db.rollback()
            return False
        run, session, job = owned
        if (
            session.input_revision != run.input_revision
            or session.lifecycle != "OPEN"
            or not _is_latest(db, run)
        ):
            db.rollback()
            return False
        job.status = BackgroundJobStatus.SUCCEEDED.value
        job.result = result
        job.completed_at = _now()
        job.last_error = None
        job.lease_token = None
        job.lease_expires_at = None
        run.ended_at = _now()
        _audit(
            db,
            "onboarding.run.prepared",
            {"session_id": str(run.session_id), "run_id": str(run.id)},
            "SOURCES_SAVED",
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def publish_failure(
    db: Session,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    token: str,
    *,
    code: str,
    terminal: bool,
) -> bool:
    try:
        owned = _lock_owned(db, run_id, job_id, token)
        if owned is None:
            db.rollback()
            return False
        run, _session, job = owned
        job.last_error = code
        job.lease_token = None
        job.lease_expires_at = None
        exhausted = (job.attempt_count or 0) >= (job.max_attempts or 5)
        if terminal:
            job.status = BackgroundJobStatus.FAILED.value
        elif exhausted:
            job.status = BackgroundJobStatus.DEAD_LETTER.value
        else:
            job.status = BackgroundJobStatus.PENDING.value
            job.next_attempt_at = _now() + timedelta(
                seconds=min(300, 2 ** (job.attempt_count or 1))
            )
            db.commit()  # run stays active while a retry is scheduled
            return True
        job.completed_at = _now()
        run.ended_at = _now()
        if run.stage == STAGE_EXTRACT:
            from rai_gateway.onboarding.extraction import finalize_pending_sources

            finalize_pending_sources(db, run.id, "RUN_ABORTED")
        _audit(
            db,
            "onboarding.run.failed",
            {"session_id": str(run.session_id), "run_id": str(run.id)},
            code,
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def reap_expired(db: Session) -> int:
    """Dead-letter expired leases that have no attempts left; close their runs."""
    now = _now()
    candidates = (
        db.query(BackgroundJob.id)
        .filter(
            BackgroundJob.job_type.in_(SUPPORTED_JOB_TYPES),
            BackgroundJob.status == BackgroundJobStatus.RUNNING.value,
            BackgroundJob.lease_expires_at < now,
            BackgroundJob.attempt_count >= BackgroundJob.max_attempts,
        )
        .all()
    )
    reaped = 0
    for (job_id,) in candidates:
        run = (
            db.query(OnboardingRun).filter(OnboardingRun.job_id == job_id).one_or_none()
        )
        try:
            if run is None:
                from rai_gateway.onboarding.provider_probe import reap_probe

                handled = reap_probe(db, job_id)
                if handled is not None:  # a provider probe owns this job
                    reaped += int(handled)
                    continue
            if run is not None:
                db.query(OnboardingSession).filter(
                    OnboardingSession.id == run.session_id
                ).with_for_update().one()
            job = (
                db.query(BackgroundJob)
                .filter(BackgroundJob.id == job_id)
                .with_for_update(skip_locked=True)
                .populate_existing()
                .one_or_none()
            )
            if (
                job is None
                or job.status != BackgroundJobStatus.RUNNING.value
                or job.lease_expires_at is None
                or job.lease_expires_at >= _now()
            ):
                db.rollback()
                continue
            job.status = BackgroundJobStatus.DEAD_LETTER.value
            job.last_error = "LEASE_EXPIRED"
            job.completed_at = _now()
            job.lease_token = None
            job.lease_expires_at = None
            if run is not None and run.ended_at is None:
                db.refresh(run)
                run.ended_at = _now()
                if run.stage == STAGE_EXTRACT:
                    from rai_gateway.onboarding.extraction import (
                        finalize_pending_sources,
                    )

                    finalize_pending_sources(db, run.id, "RUN_ABORTED")
                _audit(
                    db,
                    "onboarding.run.failed",
                    {"session_id": str(run.session_id), "run_id": str(run.id)},
                    "LEASE_EXPIRED",
                )
            db.commit()
            reaped += 1
        except Exception:
            db.rollback()
            raise
    return reaped


def _set_progress(
    db: Session, job_id: uuid.UUID, token: str, progress: dict[str, Any]
) -> None:
    outcome = db.execute(
        update(BackgroundJob)
        .where(
            BackgroundJob.id == job_id,
            BackgroundJob.lease_token == token,
            BackgroundJob.status == BackgroundJobStatus.RUNNING.value,
        )
        .values(result={"progress": progress})
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if outcome.rowcount != 1:  # cancelled, reclaimed or otherwise no longer ours
        raise _LostOwnership()


def _validate_binding(db: Session, job: BackgroundJob) -> OnboardingRun:
    payload = job.payload if isinstance(job.payload, dict) else {}
    try:
        run_id = uuid.UUID(str(payload.get("run_id")))
    except ValueError as exc:
        raise TerminalError("INVALID_RUN_BINDING") from exc
    run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one_or_none()
    if (
        run is None
        or run.job_id != job.id
        or job.job_type != JOB_TYPE
        or run.stage != "PREPARE_SOURCES"
        or payload.get("session_id") != str(run.session_id)
        or payload.get("input_revision") != run.input_revision
        or payload.get("manifest_digest") != run.manifest_digest
    ):
        raise TerminalError("INVALID_RUN_BINDING")
    entries = run.manifest.get("entries", []) if isinstance(run.manifest, dict) else []
    if canonical_digest(entries) != run.manifest_digest:
        raise TerminalError("INVALID_RUN_BINDING")
    return run


def process_claimed_job(
    session_factory: SessionFactory,
    store: OnboardingSourceStore,
    job_id: uuid.UUID,
    token: str,
) -> str:
    """Run one claimed attempt. Returns an outcome label (for logs/tests)."""
    db = session_factory()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        job_type = job.job_type
        db.rollback()
        if job_type == EXTRACT_JOB_TYPE:
            from rai_gateway.onboarding.extraction import process_extraction_job

            return process_extraction_job(session_factory, store, job_id, token)
        if job_type == PROBE_JOB_TYPE:
            from rai_gateway.onboarding.provider_probe import process_probe_job

            return process_probe_job(session_factory, job_id, token)
        if job_type == DISCOVER_JOB_TYPE:
            from rai_gateway.onboarding.discovery import process_discovery_job

            return process_discovery_job(session_factory, job_id, token)
        if job_type == GOVERNANCE_JOB_TYPE:
            from rai_gateway.onboarding.governance_generation import process_governance_job

            return process_governance_job(session_factory, job_id, token)
        if job_type == APPLY_JOB_TYPE:
            from rai_gateway.onboarding.application_job import process_apply_job

            return process_apply_job(session_factory, store, job_id, token)
        if job_type == ACTIVATE_JOB_TYPE:
            from rai_gateway.onboarding.activation_job import process_activate_job

            return process_activate_job(session_factory, store, job_id, token)
        if job_type == VERIFY_JOB_TYPE:
            from rai_gateway.onboarding.verification_job import process_verify_job

            return process_verify_job(session_factory, store, job_id, token)
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        try:
            run = _validate_binding(db, job)
        except TerminalError as exc:
            _fail_unbound(db, job_id, token, exc.code)
            return "invalid_binding"
        run_id = run.id
        entries = list(run.manifest["entries"])
        input_revision = run.input_revision
        manifest_digest = run.manifest_digest
        db.rollback()  # no locks while reading originals

        try:
            result = _check_sources(
                db, store, job_id, token, entries, input_revision, manifest_digest
            )
        except _LostOwnership:
            db.rollback()
            return "lost_ownership"
        except TerminalError as exc:
            ok = publish_failure(
                db, run_id, job_id, token, code=exc.code, terminal=True
            )
            return "failed" if ok else "lost_ownership"
        except (TemporaryError, SourceStorageError, OperationalError, OSError):
            db.rollback()
            ok = publish_failure(
                db, run_id, job_id, token, code="STORAGE_UNAVAILABLE", terminal=False
            )
            return "retry" if ok else "lost_ownership"
        ok = publish_success(db, run_id, job_id, token, result)
        return "succeeded" if ok else "lost_ownership"
    finally:
        db.close()


class _LostOwnership(Exception):
    pass


def _fail_unbound(db: Session, job_id: uuid.UUID, token: str, code: str) -> None:
    db.rollback()
    db.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job_id, BackgroundJob.lease_token == token)
        .values(
            status=BackgroundJobStatus.DEAD_LETTER.value,
            last_error=code,
            completed_at=_now(),
            lease_token=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()


def _check_sources(
    db: Session,
    store: OnboardingSourceStore,
    job_id: uuid.UUID,
    token: str,
    entries: list[dict[str, Any]],
    input_revision: int,
    manifest_digest: str,
) -> dict[str, Any]:
    queue = JobQueue(db)
    total = len(entries)
    checked = 0
    checked_bytes = 0
    last_renew = time.monotonic()
    _set_progress(
        db,
        job_id,
        token,
        {"stage": "CHECKING_SOURCES", "sources_checked": 0, "sources_total": total},
    )
    for entry in entries:
        if time.monotonic() - last_renew >= settings.onboarding_job_renew_seconds:
            if not queue.renew_lease(
                job_id=job_id,
                lease_token=token,
                lease_seconds=settings.onboarding_job_lease_seconds,
            ):
                db.rollback()
                raise _LostOwnership()
            db.commit()
            last_renew = time.monotonic()
        revision = db.get(
            OnboardingSourceRevision, uuid.UUID(entry["source_revision_id"])
        )
        if (
            revision is None
            or revision.sha256 != entry["sha256"]
            or int(revision.byte_length) != entry["byte_length"]
        ):
            raise TerminalError("SOURCE_INTEGRITY_FAILED")
        if revision.media_family not in (*SUPPORTED_FAMILIES, "TEXT"):
            raise TerminalError("UNSUPPORTED_MEDIA")
        if int(revision.byte_length) > settings.onboarding_max_file_bytes:
            raise TerminalError("SOURCE_TOO_LARGE")
        storage_key = revision.storage_key
        db.rollback()
        try:
            store.verify(
                storage_key, sha256=entry["sha256"], byte_length=entry["byte_length"]
            )
        except SourceIntegrityError as exc:
            raise TerminalError("SOURCE_INTEGRITY_FAILED") from exc
        except SourceStorageError as exc:
            if "missing" in str(exc).lower():
                raise TerminalError("SOURCE_MISSING") from exc
            raise
        checked += 1
        checked_bytes += entry["byte_length"]
        _set_progress(
            db,
            job_id,
            token,
            {
                "stage": "CHECKING_SOURCES",
                "sources_checked": checked,
                "sources_total": total,
            },
        )
    return {
        "input_revision": input_revision,
        "manifest_digest": manifest_digest,
        "sources_total": total,
        "sources_checked": checked,
        "bytes_checked": checked_bytes,
        "checked_at": _now().isoformat(),
    }


def cleanup_orphans(
    session_factory: SessionFactory, store: OnboardingSourceStore
) -> dict[str, int]:
    """Remove stale staging files and finalized objects with no revision row."""

    def referenced(key: str) -> bool:
        db = session_factory()
        try:
            return (
                db.query(OnboardingSourceRevision.id)
                .filter(OnboardingSourceRevision.storage_key == key)
                .first()
                is not None
            )
        finally:
            db.close()

    return store.cleanup_orphans(
        is_referenced=referenced,
        min_age_seconds=settings.onboarding_orphan_min_age_seconds,
    )


def run_pass(
    session_factory: SessionFactory,
    store: OnboardingSourceStore,
    *,
    worker_id: str,
    max_jobs: int = 25,
    cleanup: bool = False,
) -> dict[str, int]:
    """One bounded pass: reap, claim and process supported work, optional cleanup."""
    stats = {"reaped": 0, "processed": 0, "succeeded": 0, "failed": 0, "cleaned": 0}
    db = session_factory()
    try:
        stats["reaped"] = reap_expired(db)
    finally:
        db.close()
    for _ in range(max_jobs):
        db = session_factory()
        try:
            claimed = JobQueue(db).claim_leased(
                job_types=SUPPORTED_JOB_TYPES,
                worker_id=worker_id,
                lease_seconds=settings.onboarding_job_lease_seconds,
            )
            if claimed is None:
                db.rollback()
                break
            job_id, token = claimed.id, claimed.lease_token
            db.commit()  # claim is durable before any original is read
        finally:
            db.close()
        outcome = process_claimed_job(session_factory, store, job_id, token)
        stats["processed"] += 1
        if outcome == "succeeded":
            stats["succeeded"] += 1
        elif outcome in ("failed", "invalid_binding"):
            stats["failed"] += 1
    if cleanup:
        removed = cleanup_orphans(session_factory, store)
        stats["cleaned"] = removed["staging_removed"] + removed["objects_removed"]
    return stats
