"""Background worker for cloud governance sync jobs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.cloud_governance.domain import CloudJobType
from rai_gateway.cloud_governance.drift import run_drift_check
from rai_gateway.cloud_governance.service import CloudGovernanceService
from rai_gateway.sdk.jobs import JobQueue

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
            db.commit()
            processed += 1
    db.commit()
    return processed
