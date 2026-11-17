"""Background job queue behavior for cloud sync."""

from __future__ import annotations

import uuid

from rai_gateway.models.productization import BackgroundJob
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue


def test_enqueue_new_job_after_dead_letter_clears_old_key(db_session):
    queue = JobQueue(db_session)
    key = f"cloud-sync:{uuid.uuid4()}:dead"
    job = BackgroundJob(
        job_type="CLOUD_ASSET_SYNC",
        status=BackgroundJobStatus.DEAD_LETTER.value,
        idempotency_key=key,
        payload={},
        attempt_count=5,
        max_attempts=5,
    )
    db_session.add(job)
    db_session.commit()

    created = queue.enqueue(
        job_type="CLOUD_ASSET_SYNC",
        payload={"connection_id": str(uuid.uuid4())},
        idempotency_key=key,
    )
    assert created["status"] == BackgroundJobStatus.PENDING.value
    db_session.refresh(job)
    assert job.idempotency_key is None
