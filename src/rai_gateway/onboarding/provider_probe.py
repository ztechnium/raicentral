"""Worker-side synthetic provider probe (Step 03).

Binding is probe -> job -> frozen configuration, not session -> manifest. The only
data sent is a fixed synthetic instruction and a fresh nonce. Lock order everywhere:
configuration row, probe, job. ``dispatch_started_at`` is the dispatch authorization
point; after it, a lost response is OUTCOME_UNKNOWN and is never resent automatically.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.database import AdminAudit
from rai_gateway.models.onboarding import (
    OnboardingAIConfiguration,
    OnboardingProviderProbe,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding import ai_adapter
from rai_gateway.onboarding.ai_configuration import (
    current_effective,
    resolve_secret,
)
from rai_gateway.onboarding.service import PROBE_JOB_TYPE
from rai_gateway.provider_gateway.credentials.secret_resolver import (
    SecretResolutionError,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    db: Session, action: str, probe: OnboardingProviderProbe, reason: str
) -> None:
    db.add(
        AdminAudit(
            trace_id=uuid.uuid4().hex,
            actor_user_id="onboarding-worker",
            actor_role="onboarding_worker",
            action=action,
            target={"probe_id": str(probe.id)},
            reason=reason,
        )
    )


def _lock_chain(
    db: Session, probe_id: uuid.UUID
) -> tuple[
    OnboardingAIConfiguration | None,
    OnboardingProviderProbe | None,
    BackgroundJob | None,
]:
    cfg = (
        db.query(OnboardingAIConfiguration)
        .filter(OnboardingAIConfiguration.singleton_key == "default")
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    probe = (
        db.query(OnboardingProviderProbe)
        .filter(OnboardingProviderProbe.id == probe_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    job = None
    if probe is not None:
        job = (
            db.query(BackgroundJob)
            .filter(BackgroundJob.id == probe.job_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
    return cfg, probe, job


def _owned(
    job: BackgroundJob | None, probe: OnboardingProviderProbe | None, token: str
) -> bool:
    return bool(
        job is not None
        and probe is not None
        and probe.ended_at is None
        and job.status == BackgroundJobStatus.RUNNING.value
        and job.lease_token == token
        and job.lease_expires_at is not None
        and job.lease_expires_at > _now()
    )


def _close(
    db: Session,
    probe_id: uuid.UUID,
    token: str,
    *,
    outcome: str,
    error_code: str | None,
    latency_ms: int | None = None,
    usage: dict[str, int] | None = None,
) -> bool:
    """Atomically finalize probe outcome, job terminal state and ended_at."""
    try:
        _cfg, probe, job = _lock_chain(db, probe_id)
        if not _owned(job, probe, token):
            db.rollback()
            return False
        assert probe is not None and job is not None
        probe.outcome = outcome
        probe.error_code = error_code
        probe.latency_ms = latency_ms
        probe.usage = usage
        probe.ended_at = _now()
        job.status = (
            BackgroundJobStatus.SUCCEEDED.value
            if outcome == "PASSED"
            else BackgroundJobStatus.FAILED.value
        )
        job.completed_at = _now()
        job.lease_token = None
        job.lease_expires_at = None
        job.last_error = None if outcome == "PASSED" else (error_code or outcome)
        _audit(db, "onboarding.ai.test_finished", probe, outcome)
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def reap_probe(db: Session, job_id: uuid.UUID) -> bool | None:
    """Terminalize an expired probe job. None when this job is not a probe's."""
    probe_id = (
        db.query(OnboardingProviderProbe.id)
        .filter(OnboardingProviderProbe.job_id == job_id)
        .scalar()
    )
    if probe_id is None:
        return None
    try:
        _cfg, probe, job = _lock_chain(db, probe_id)
        if (
            probe is None
            or job is None
            or job.status != BackgroundJobStatus.RUNNING.value
            or job.lease_expires_at is None
            or job.lease_expires_at >= _now()
        ):
            db.rollback()
            return False
        if probe.ended_at is None:
            # A dispatched request may have reached the provider: unknown, not failed.
            probe.outcome = "OUTCOME_UNKNOWN" if probe.dispatch_started_at else "FAILED"
            probe.error_code = (
                "WORKER_LOST_AFTER_DISPATCH"
                if probe.dispatch_started_at
                else "WORKER_LOST"
            )
            probe.ended_at = _now()
            _audit(db, "onboarding.ai.test_finished", probe, probe.outcome)
        job.status = BackgroundJobStatus.DEAD_LETTER.value
        job.last_error = "LEASE_EXPIRED"
        job.completed_at = _now()
        job.lease_token = None
        job.lease_expires_at = None
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def process_probe_job(session_factory, job_id: uuid.UUID, token: str) -> str:
    db = session_factory()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        payload = job.payload if isinstance(job.payload, dict) else {}
        probe = (
            db.query(OnboardingProviderProbe)
            .filter(OnboardingProviderProbe.job_id == job.id)
            .one_or_none()
        )
        if (
            probe is None
            or job.job_type != PROBE_JOB_TYPE
            or payload.get("probe_id") != str(probe.id)
            or payload.get("configuration_version") != probe.configuration_version
            or payload.get("target_fingerprint") != probe.target_fingerprint
        ):
            from rai_gateway.onboarding.preparation import _fail_unbound

            _fail_unbound(db, job_id, token, "INVALID_RUN_BINDING")
            return "invalid_binding"
        probe_id = probe.id
        nonce = probe.nonce
        snapshot = dict(probe.snapshot)
        db.rollback()

        # --- Pre-dispatch validation: no secret resolution or network before this passes.
        cfg = (
            db.query(OnboardingAIConfiguration)
            .filter(OnboardingAIConfiguration.singleton_key == "default")
            .one_or_none()
        )
        target, current_snapshot, fingerprint = current_effective(cfg)
        db.rollback()
        if target is None or cfg is None:
            return _pre_dispatch_fail(db, probe_id, token, "CONFIGURATION_UNAVAILABLE")
        if cfg.config_version != probe.configuration_version:
            return _pre_dispatch_fail(db, probe_id, token, "CONFIGURATION_CHANGED")
        if fingerprint != probe.target_fingerprint:
            # API and worker disagree on the effective target policy.
            return _pre_dispatch_fail(db, probe_id, token, "TARGET_POLICY_MISMATCH")
        try:
            secret = resolve_secret(target)
        except SecretResolutionError:
            return _pre_dispatch_fail(db, probe_id, token, "CREDENTIAL_UNAVAILABLE")
        try:
            prepared = ai_adapter.prepare_request(snapshot, secret, nonce)
        except ai_adapter.DestinationRejected as exc:
            return _pre_dispatch_fail(db, probe_id, token, exc.code)

        # --- Dispatch marker = dispatch authorization point.
        if not _mark_dispatch(
            db, probe_id, token, probe.configuration_version, fingerprint
        ):
            return "lost_ownership"
        result = ai_adapter.send(prepared, nonce)
        ok = _close(
            db,
            probe_id,
            token,
            outcome=result.outcome,
            error_code=result.error_code,
            latency_ms=result.latency_ms,
            usage=result.usage,
        )
        if not ok:
            return "lost_ownership"
        return "succeeded" if result.outcome == "PASSED" else "failed"
    finally:
        db.close()


def _pre_dispatch_fail(db: Session, probe_id: uuid.UUID, token: str, code: str) -> str:
    ok = _close(db, probe_id, token, outcome="FAILED", error_code=code)
    return "failed" if ok else "lost_ownership"


def _mark_dispatch(
    db: Session,
    probe_id: uuid.UUID,
    token: str,
    configuration_version: int,
    fingerprint: str | None,
) -> bool:
    """Lock and recheck configuration + job; commit the marker, or close pre-dispatch."""
    try:
        cfg, probe, job = _lock_chain(db, probe_id)
        if not _owned(job, probe, token):
            db.rollback()
            return False
        assert probe is not None and job is not None
        _target, _snap, current_fp = current_effective(cfg)
        if (
            cfg is None
            or not cfg.enabled
            or cfg.config_version != configuration_version
            or current_fp != fingerprint
        ):
            # Disabled/changed before authorization: never send.
            probe.outcome = "FAILED"
            probe.error_code = "CONFIGURATION_CHANGED_BEFORE_DISPATCH"
            probe.ended_at = _now()
            job.status = BackgroundJobStatus.FAILED.value
            job.completed_at = _now()
            job.lease_token = None
            job.lease_expires_at = None
            job.last_error = probe.error_code
            _audit(db, "onboarding.ai.test_finished", probe, "FAILED")
            db.commit()
            return False
        JobQueue(db).renew_lease(
            job_id=job.id,
            lease_token=token,
            lease_seconds=settings.onboarding_job_lease_seconds,
        )
        probe.dispatch_started_at = _now()
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


__all__ = ["process_probe_job", "reap_probe", "Any"]
