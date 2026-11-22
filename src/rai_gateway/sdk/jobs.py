"""Background job queue seam (DB-backed, worker-process friendly)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from rai_gateway.models.productization import BackgroundJob
from rai_gateway.sdk.domain import BackgroundJobStatus

RESERVED_JOB_PREFIX = "ONBOARDING_"
RESERVED_KEY_PREFIX = "onboarding:"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_reserved_job(job_type: str | None, idempotency_key: str | None = None) -> bool:
    """Internal namespaces that only their owning service may create."""
    return bool(
        (job_type or "").upper().startswith(RESERVED_JOB_PREFIX)
        or (idempotency_key or "").lower().startswith(RESERVED_KEY_PREFIX)
    )


class JobQueue:
    def __init__(self, db: Session) -> None:
        self._db = db

    def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        created_by: str | None = None,
        max_attempts: int = 5,
        allow_reserved: bool = False,
    ) -> dict[str, Any]:
        if not allow_reserved and is_reserved_job(job_type, idempotency_key):
            raise ValueError("Job type or idempotency key is reserved")
        if idempotency_key:
            existing = (
                self._db.query(BackgroundJob)
                .filter(BackgroundJob.idempotency_key == idempotency_key)
                .first()
            )
            if existing:
                return self._serialize(existing)

        job = BackgroundJob(
            job_type=job_type,
            status=BackgroundJobStatus.PENDING.value,
            idempotency_key=idempotency_key,
            payload=payload or {},
            max_attempts=max_attempts,
            next_attempt_at=_utcnow(),
            created_by=created_by,
        )
        self._db.add(job)
        self._db.flush()
        return self._serialize(job)

    def claim_next(self, *, job_types: list[str] | None = None) -> BackgroundJob | None:
        now = _utcnow()
        q = self._db.query(BackgroundJob).filter(
            BackgroundJob.status == BackgroundJobStatus.PENDING.value,
            (BackgroundJob.next_attempt_at.is_(None))
            | (BackgroundJob.next_attempt_at <= now),
        )
        if job_types:
            q = q.filter(BackgroundJob.job_type.in_(job_types))
        job = (
            q.order_by(BackgroundJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if not job:
            return None
        job.status = BackgroundJobStatus.RUNNING.value
        job.started_at = now
        job.attempt_count = (job.attempt_count or 0) + 1
        self._db.flush()
        return job

    def claim_leased(
        self,
        *,
        job_types: list[str],
        worker_id: str,
        lease_seconds: float,
    ) -> BackgroundJob | None:
        """Claim eligible or lease-expired work for handlers that adopt leases.

        Only the explicitly supplied job types are considered, so historical jobs
        whose handlers do not use leases are never reclaimed.
        """
        if not job_types:
            return None
        now = _utcnow()
        eligible = (
            (BackgroundJob.status == BackgroundJobStatus.PENDING.value)
            & (
                BackgroundJob.next_attempt_at.is_(None)
                | (BackgroundJob.next_attempt_at <= now)
            )
        ) | (
            (BackgroundJob.status == BackgroundJobStatus.RUNNING.value)
            & (BackgroundJob.lease_expires_at < now)
            & (BackgroundJob.attempt_count < BackgroundJob.max_attempts)
        )
        job = (
            self._db.query(BackgroundJob)
            .filter(BackgroundJob.job_type.in_(job_types), eligible)
            .order_by(BackgroundJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if not job:
            return None
        job.status = BackgroundJobStatus.RUNNING.value
        job.started_at = now
        job.attempt_count = (job.attempt_count or 0) + 1
        job.worker_id = worker_id
        job.lease_token = secrets.token_hex(16)
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.heartbeat_at = now
        self._db.flush()
        return job

    def renew_lease(
        self, *, job_id: Any, lease_token: str, lease_seconds: float
    ) -> bool:
        """Extend a lease only while status, token and expiry still match."""
        now = _utcnow()
        result = self._db.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == job_id,
                BackgroundJob.status == BackgroundJobStatus.RUNNING.value,
                BackgroundJob.lease_token == lease_token,
                BackgroundJob.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def complete(
        self, job: BackgroundJob, result: dict[str, Any] | None = None
    ) -> None:
        job.status = BackgroundJobStatus.SUCCEEDED.value
        job.result = result
        job.completed_at = _utcnow()
        job.last_error = None
        self._db.flush()

    def fail(self, job: BackgroundJob, error: str) -> None:
        job.last_error = error[:2000]
        if (job.attempt_count or 0) >= (job.max_attempts or 5):
            job.status = BackgroundJobStatus.DEAD_LETTER.value
            job.completed_at = _utcnow()
        else:
            job.status = BackgroundJobStatus.PENDING.value
            job.next_attempt_at = _utcnow() + timedelta(
                seconds=min(3600, 2 ** (job.attempt_count or 1))
            )
        self._db.flush()

    def list_jobs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        q = self._db.query(BackgroundJob)
        if status:
            q = q.filter(BackgroundJob.status == status)
        rows = q.order_by(BackgroundJob.created_at.desc()).limit(limit).all()
        return [self._serialize(r) for r in rows]

    def _serialize(self, job: BackgroundJob) -> dict[str, Any]:
        if is_reserved_job(job.job_type, job.idempotency_key):
            # Internal jobs expose state only; never payloads, results, errors or keys.
            return {
                "id": str(job.id),
                "job_type": job.job_type,
                "status": job.status,
                "idempotency_key": None,
                "payload": None,
                "result": None,
                "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
                "last_error": None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "completed_at": (
                    job.completed_at.isoformat() if job.completed_at else None
                ),
            }
        return {
            "id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "idempotency_key": job.idempotency_key,
            "payload": job.payload,
            "result": job.result,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "last_error": job.last_error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
