"""Background worker for cloud governance sync jobs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.cloud_governance.domain import CloudJobType, SyncRunStatus
from rai_gateway.cloud_governance.drift import run_drift_check
from rai_gateway.cloud_governance.service import CloudGovernanceService
from rai_gateway.models.cloud_governance import CloudSyncRun
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue


def _mark_sync_run_failed(db: Session, job, error: str) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    sync_run_id = payload.get("sync_run_id")
    if not sync_run_id:
        run = (
            db.query(CloudSyncRun)
            .filter(CloudSyncRun.background_job_id == job.id)
            .order_by(CloudSyncRun.started_at.desc())
            .first()
        )
    else:
        run = db.query(CloudSyncRun).filter(CloudSyncRun.id == UUID(sync_run_id)).first()
    if run is None:
        return
    run.status = SyncRunStatus.FAILED.value
    run.partial_errors = [{"message": error[:500]}]
    db.flush()

CLOUD_JOB_TYPES = [
    CloudJobType.CLOUD_ASSET_SYNC.value,
    CloudJobType.CLOUD_EVIDENCE_COLLECT.value,
    CloudJobType.CLOUD_DRIFT_CHECK.value,
]


def process_cloud_jobs(db: Session, *, limit: int = 5) -> int:
    queue = JobQueue(db)
    processed = 0
    for _ in range(limit):
        job = queue.claim_next(job_types=CLOUD_JOB_TYPES)
        if job is None:
            break
        try:
            if job.job_type == CloudJobType.CLOUD_ASSET_SYNC.value:
                connection_id = UUID((job.payload or {})["connection_id"])
                result = CloudGovernanceService(db).run_asset_sync(
                    connection_id, job=job
                )
                queue.complete(job, result=result)
            elif job.job_type == CloudJobType.CLOUD_DRIFT_CHECK.value:
                connection_id = UUID((job.payload or {})["connection_id"])
                result = run_drift_check(db, connection_id)
                queue.complete(job, result=result)
            elif job.job_type == CloudJobType.CLOUD_EVIDENCE_COLLECT.value:
                connection_id = UUID((job.payload or {})["connection_id"])
                queue.complete(
                    job,
                    result={
                        "connection_id": str(connection_id),
                        "collected": True,
                        "note": "evidence persisted at verify/sync/apply",
                    },
                )
            else:
                queue.complete(job, result={"skipped": True, "reason": "unknown_type"})
            processed += 1
        except Exception as exc:  # noqa: BLE001
            queue.fail(job, str(exc))
            if (
                job.job_type == CloudJobType.CLOUD_ASSET_SYNC.value
                and job.status == BackgroundJobStatus.DEAD_LETTER.value
            ):
                _mark_sync_run_failed(db, job, str(exc))
            db.commit()
            processed += 1
    db.commit()
    return processed
