"""Durable preparation jobs: claims, leases, recovery, cancel, retry, convergence."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from onboarding_support import PDF, fresh

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import (
    OnboardingRun,
    OnboardingSession,
    OnboardingSource,
    OnboardingSourceRevision,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.models.registry import Agent, AgentVersion, AISystem
from rai_gateway.onboarding import preparation
from rai_gateway.onboarding.service import JOB_TYPE, Access, IntakeError, IntakeService
from rai_gateway.onboarding.storage import FilesystemSourceStore, SourceStorageError
from rai_gateway.sdk.jobs import JobQueue

ADMIN_ACCESS = Access(
    can_read=True,
    can_content=True,
    can_manage=True,
    can_inventory=True,
    can_governance=True,
)


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=5)


@pytest.fixture()
def store(storage_root):
    return FilesystemSourceStore(storage_root)


@pytest.fixture()
def prepared(api):
    """A session with a prompt and a PDF, ready to prepare."""
    session = api.create().json()
    prompt = api.text(session, "Please onboard our AI estate").json()
    upload = api.upload(prompt["session"], "policy.pdf", PDF).json()
    return upload["session"]


def worker_pass(session_factory, store, **kwargs):
    return preparation.run_pass(
        session_factory, store, worker_id="test-worker", **kwargs
    )


def job_of(session_factory, run_id: str) -> BackgroundJob:
    db = session_factory()
    try:
        run = db.get(OnboardingRun, uuid.UUID(run_id))
        job = db.get(BackgroundJob, run.job_id)
        db.expunge(job)
        return job
    finally:
        db.close()


def tamper(session_factory, storage_root, source_id: str, data: bytes | None) -> None:
    db = session_factory()
    try:
        source = db.get(OnboardingSource, uuid.UUID(source_id))
        revision = db.get(OnboardingSourceRevision, source.current_revision_id)
        path = storage_root / "objects" / revision.storage_key
    finally:
        db.close()
    if data is None:
        path.unlink()
    else:
        path.write_bytes(data)


def test_prepare_queues_a_durable_job_and_a_worker_completes_it(
    api, prepared, session_factory, store
):
    response = api.prepare(prepared)
    assert response.status_code == 202, response.text
    run = response.json()["run"]
    assert run["state"] == "QUEUED" and response.json()["session"]["view"] == "QUEUED"
    assert response.json()["session"]["row_version"] == prepared["row_version"]

    # Saved state is visible to a different session (restart / separate worker).
    other = session_factory()
    try:
        assert other.query(OnboardingRun).count() == 1
        job = (
            other.query(BackgroundJob).filter(BackgroundJob.job_type == JOB_TYPE).one()
        )
        assert job.status == "PENDING" and job.payload["run_id"] == run["id"]
        assert "policy" not in str(job.payload) and "onboard" not in str(job.payload)
    finally:
        other.close()

    stats = worker_pass(session_factory, store)
    assert stats["processed"] == 1 and stats["succeeded"] == 1

    view = fresh(api, prepared)
    assert view["view"] == "READY_FOR_ANALYSIS"
    done = view["current_run"]
    assert done["state"] == "SUCCEEDED" and done["active"] is False
    assert done["sources_checked"] == done["sources_total"] == 2
    assert (
        done["checked_bytes"] > 0
        and done["input_revision"] == prepared["input_revision"]
    )
    assert done["manifest"]["version"] == 2  # new manifests snapshot parsing options
    assert all(
        {
            "source_id",
            "source_revision_id",
            "kind",
            "role",
            "sha256",
            "byte_length",
            "media_family",
            "parsing_options",
            "parsing_options_version",
            "options_digest",
        }
        == set(e)
        for e in done["manifest"]["entries"]
    )
    assert "analysis" not in str(view["allowed_actions"]).lower()


def test_preparation_does_not_touch_domain_records(
    api, prepared, session_factory, store, db_session
):
    before = [db_session.query(m).count() for m in (AISystem, Agent, AgentVersion)]
    api.prepare(prepared)
    worker_pass(session_factory, store)
    db_session.expire_all()
    assert [
        db_session.query(m).count() for m in (AISystem, Agent, AgentVersion)
    ] == before


def test_editing_after_success_returns_to_draft_and_keeps_history(
    api, prepared, session_factory, store
):
    api.prepare(prepared)
    worker_pass(session_factory, store)
    ready = fresh(api, prepared)
    edited = api.upload(ready, "extra.txt", b"more").json()["session"]
    assert edited["view"] == "DRAFT"
    assert edited["current_run"]["state"] == "SUCCEEDED"  # old result stays in history
    runs = api.client.get(
        f"{api.BASE}/{prepared['id']}/runs", headers=api.headers
    ).json()
    assert runs["total"] == 1


def test_prepare_requires_input_and_is_blocked_by_stale_revision(api, session):
    empty = api.prepare(session)
    assert (
        empty.status_code == 422 and empty.json()["error"]["code"] == "INPUT_REQUIRED"
    )
    uploaded = api.upload(session, "a.txt", b"hello").json()["session"]
    stale = api.prepare(uploaded, revision=0)  # late click from an older input revision
    assert (
        stale.status_code == 409
        and stale.json()["error"]["code"] == "REVISION_CONFLICT"
    )
    assert api.prepare(uploaded).status_code == 202


def test_prepare_is_idempotent_and_converges_on_one_active_run(api, prepared):
    first = api.prepare(prepared, key="prepare-key-0001")
    replay = api.prepare(prepared, key="prepare-key-0001")
    other_key = api.prepare(prepared, key="prepare-key-0002")
    assert (
        first.json()["run"]["id"]
        == replay.json()["run"]["id"]
        == other_key.json()["run"]["id"]
    )
    assert replay.json()["replayed"] is True
    runs = api.client.get(
        f"{api.BASE}/{prepared['id']}/runs", headers=api.headers
    ).json()
    assert runs["total"] == 1


def test_inputs_are_locked_while_a_run_is_active_and_cancel_unlocks(api, prepared):
    run = api.prepare(prepared).json()["run"]
    blocked = api.upload(prepared, "x.txt", b"x")
    assert (
        blocked.status_code == 409
        and blocked.json()["error"]["code"] == "PREPARATION_ACTIVE"
    )
    archive = api.client.post(
        f"{api.BASE}/{prepared['id']}/archive",
        json={"expected_row_version": prepared["row_version"]},
        headers=api.headers,
    )
    assert archive.status_code == 409
    cancelled = api.cancel(prepared["id"], run["id"])
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["run"]["state"] == "CANCELLED" and body["session"]["view"] == "DRAFT"
    assert api.cancel(prepared["id"], run["id"]).json()["replayed"] is True
    assert api.upload(body["session"], "x.txt", b"x").status_code == 201


def test_cancel_during_work_stays_terminal_even_if_worker_finishes_later(
    api, prepared, session_factory, store
):
    run = api.prepare(prepared).json()["run"]
    db = session_factory()
    claimed = JobQueue(db).claim_leased(
        job_types=[JOB_TYPE], worker_id="w1", lease_seconds=60
    )
    job_id, token = claimed.id, claimed.lease_token
    db.commit()
    db.close()
    assert api.cancel(prepared["id"], run["id"]).status_code == 200
    result = preparation.process_claimed_job(session_factory, store, job_id, token)
    assert result == "lost_ownership"
    job = job_of(session_factory, run["id"])
    assert job.status == "CANCELLED"
    run_after = api.client.get(
        f"{api.BASE}/{prepared['id']}/runs/{run['id']}", headers=api.headers
    ).json()
    assert run_after["state"] == "CANCELLED"


def test_old_run_cannot_publish_into_new_input_revision(api, prepared, session_factory):
    run = api.prepare(prepared).json()["run"]
    db = session_factory()
    claimed = JobQueue(db).claim_leased(
        job_types=[JOB_TYPE], worker_id="w1", lease_seconds=60
    )
    job_id, token = claimed.id, claimed.lease_token
    db.commit()
    db.close()
    api.cancel(prepared["id"], run["id"])
    edited = api.upload(fresh(api, prepared), "new.txt", b"new").json()["session"]
    newer = api.prepare(edited).json()["run"]
    assert newer["id"] != run["id"] and newer["run_number"] == 2
    db = session_factory()
    try:
        assert (
            preparation.publish_success(db, uuid.UUID(run["id"]), job_id, token, {})
            is False
        )
    finally:
        db.close()
    assert fresh(api, prepared)["view"] == "QUEUED"


def test_input_revision_change_blocks_publication_even_with_a_valid_lease(
    api, prepared, session_factory, store
):
    run = api.prepare(prepared).json()["run"]
    db = session_factory()
    claimed = JobQueue(db).claim_leased(
        job_types=[JOB_TYPE], worker_id="w1", lease_seconds=60
    )
    job_id, token = claimed.id, claimed.lease_token
    db.commit()
    db.query(OnboardingSession).filter(
        OnboardingSession.id == uuid.UUID(prepared["id"])
    ).update({"input_revision": OnboardingSession.input_revision + 1})
    db.commit()
    assert (
        preparation.publish_success(db, uuid.UUID(run["id"]), job_id, token, {})
        is False
    )
    db.close()
    assert job_of(session_factory, run["id"]).status == "RUNNING"


def test_two_workers_cannot_own_the_same_claim(api, prepared, session_factory):
    api.prepare(prepared)
    first_db, second_db = session_factory(), session_factory()
    try:
        first = JobQueue(first_db).claim_leased(
            job_types=[JOB_TYPE], worker_id="a", lease_seconds=60
        )
        assert first is not None  # row lock held, not yet committed
        second = JobQueue(second_db).claim_leased(
            job_types=[JOB_TYPE], worker_id="b", lease_seconds=60
        )
        assert second is None
        first_db.commit()
        assert (
            JobQueue(second_db).claim_leased(
                job_types=[JOB_TYPE], worker_id="b", lease_seconds=60
            )
            is None
        )
    finally:
        first_db.close()
        second_db.close()


def test_expired_lease_is_reclaimed_with_a_new_token_and_stale_owner_is_fenced(
    api, prepared, session_factory
):
    run = api.prepare(prepared).json()["run"]
    db = session_factory()
    first = JobQueue(db).claim_leased(
        job_types=[JOB_TYPE], worker_id="a", lease_seconds=60
    )
    job_id, old_token = first.id, first.lease_token
    db.commit()
    assert JobQueue(db).renew_lease(
        job_id=job_id, lease_token=old_token, lease_seconds=60
    )
    db.query(BackgroundJob).filter(BackgroundJob.id == job_id).update(
        {"lease_expires_at": _past()}
    )
    db.commit()
    second = JobQueue(db).claim_leased(
        job_types=[JOB_TYPE], worker_id="b", lease_seconds=60
    )
    new_token = second.lease_token
    db.commit()
    assert second.id == job_id and new_token != old_token and second.attempt_count == 2

    run_id = uuid.UUID(run["id"])
    assert (
        JobQueue(db).renew_lease(job_id=job_id, lease_token=old_token, lease_seconds=60)
        is False
    )
    assert preparation.publish_success(db, run_id, job_id, old_token, {"x": 1}) is False
    assert (
        preparation.publish_failure(
            db, run_id, job_id, old_token, code="INTERNAL_ERROR", terminal=True
        )
        is False
    )
    assert job_of(session_factory, run["id"]).status == "RUNNING"
    assert preparation.publish_success(db, run_id, job_id, new_token, {"ok": 1}) is True
    db.close()
    assert job_of(session_factory, run["id"]).status == "SUCCEEDED"


def test_an_expired_owner_cannot_renew_itself(api, prepared, session_factory):
    api.prepare(prepared)
    db = session_factory()
    claimed = JobQueue(db).claim_leased(
        job_types=[JOB_TYPE], worker_id="a", lease_seconds=60
    )
    job_id, token = claimed.id, claimed.lease_token
    db.commit()
    db.query(BackgroundJob).filter(BackgroundJob.id == job_id).update(
        {"lease_expires_at": _past()}
    )
    db.commit()
    assert (
        JobQueue(db).renew_lease(job_id=job_id, lease_token=token, lease_seconds=60)
        is False
    )
    db.close()


def test_repeated_crashes_dead_letter_and_close_the_run_so_the_user_is_not_locked_out(
    api, prepared, session_factory, store
):
    run = api.prepare(prepared).json()["run"]
    max_attempts = job_of(session_factory, run["id"]).max_attempts
    db = session_factory()
    for _ in range(max_attempts):
        claimed = JobQueue(db).claim_leased(
            job_types=[JOB_TYPE], worker_id="crash", lease_seconds=60
        )
        assert claimed is not None
        db.commit()
        db.query(BackgroundJob).filter(BackgroundJob.id == claimed.id).update(
            {"lease_expires_at": _past()}
        )
        db.commit()
    assert (
        JobQueue(db).claim_leased(job_types=[JOB_TYPE], worker_id="w", lease_seconds=60)
        is None
    )
    db.close()

    assert worker_pass(session_factory, store)["reaped"] == 1
    job = job_of(session_factory, run["id"])
    assert job.status == "DEAD_LETTER" and job.last_error == "LEASE_EXPIRED"
    view = fresh(api, prepared)
    assert (
        view["view"] == "PREPARATION_FAILED" and view["current_run"]["active"] is False
    )
    assert "retry" in view["allowed_actions"]
    assert api.prepare(view).json()["error"]["code"] == "RETRY_REQUIRED"
    retried = api.retry(view, run["id"])
    assert retried.status_code == 202
    assert retried.json()["run"]["retry_of_run_id"] == run["id"]
    assert retried.json()["run"]["attempt_count"] == 0  # fresh attempt accounting
    assert worker_pass(session_factory, store)["succeeded"] == 1
    assert fresh(api, prepared)["view"] == "READY_FOR_ANALYSIS"


def test_temporary_storage_errors_retry_with_backoff_then_dead_letter(
    api, prepared, session_factory, store, monkeypatch
):
    run = api.prepare(prepared).json()["run"]

    def unavailable(self, key, *, sha256, byte_length):
        raise SourceStorageError("Source storage is unavailable")

    monkeypatch.setattr(FilesystemSourceStore, "verify", unavailable)
    max_attempts = job_of(session_factory, run["id"]).max_attempts
    for attempt in range(1, max_attempts + 1):
        db = session_factory()
        db.query(BackgroundJob).filter(BackgroundJob.job_type == JOB_TYPE).update(
            {"next_attempt_at": _past()}
        )
        db.commit()
        db.close()
        worker_pass(session_factory, store)
        job = job_of(session_factory, run["id"])
        if attempt < max_attempts:
            assert job.status == "PENDING" and job.next_attempt_at > _past()
            view = fresh(api, prepared)
            assert view["current_run"]["state"] == "RETRY_SCHEDULED"
            assert view["current_run"]["active"] is True  # retry leaves the run active
    assert job.status == "DEAD_LETTER" and job.attempt_count == max_attempts
    assert fresh(api, prepared)["view"] == "PREPARATION_FAILED"


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda: b"tampered bytes", "SOURCE_INTEGRITY_FAILED"),
        (lambda: None, "SOURCE_MISSING"),
    ],
)
def test_changed_or_missing_originals_fail_preparation_and_never_report_ready(
    api, prepared, session_factory, store, storage_root, mutate, code
):
    api.prepare(prepared)
    original = PDF
    source_id = next(s["id"] for s in prepared["sources"] if s["kind"] == "FILE")
    tamper(session_factory, storage_root, source_id, mutate())
    worker_pass(session_factory, store)
    view = fresh(api, prepared)
    assert view["view"] == "PREPARATION_FAILED"
    assert view["current_run"]["error_code"] == code
    assert view["current_run"]["state"] == "FAILED"
    assert "ready" not in view["view"].lower().replace("analysis", "")

    # Restore the exact original: an explicit retry now succeeds as a new linked run.
    tamper(session_factory, storage_root, source_id, original)
    retried = api.retry(view, view["current_run"]["id"])
    assert retried.status_code == 202
    worker_pass(session_factory, store)
    assert fresh(api, prepared)["view"] == "READY_FOR_ANALYSIS"


def test_retry_rules_superseded_changed_inputs_and_parallel_clicks(
    api, prepared, session_factory, store, storage_root
):
    source_id = next(s["id"] for s in prepared["sources"] if s["kind"] == "FILE")
    first = api.prepare(prepared).json()["run"]
    tamper(session_factory, storage_root, source_id, b"bad")
    worker_pass(session_factory, store)
    view = fresh(api, prepared)

    a = api.retry(view, first["id"], key="retry-key-aaaa")
    b = api.retry(view, first["id"], key="retry-key-bbbb")  # parallel click, other key
    assert a.json()["run"]["id"] == b.json()["run"]["id"]
    assert a.json()["run"]["run_number"] == 2

    tamper(session_factory, storage_root, source_id, PDF)
    worker_pass(session_factory, store)  # run 2 succeeds
    superseded = api.retry(fresh(api, prepared), first["id"], key="retry-key-cccc")
    assert superseded.status_code == 409
    assert superseded.json()["error"]["code"] == "RUN_SUPERSEDED"
    assert (
        superseded.json()["error"]["details"]["current_run_id"] == a.json()["run"]["id"]
    )
    assert (
        api.client.get(f"{api.BASE}/{prepared['id']}/runs", headers=api.headers).json()[
            "total"
        ]
        == 2
    )  # nothing extra was enqueued

    # A failed run whose inputs changed must be re-prepared, not retried.
    third = api.upload(fresh(api, prepared), "n.txt", b"new").json()["session"]
    run3 = api.prepare(third).json()["run"]
    api.cancel(prepared["id"], run3["id"])
    changed = api.include(
        fresh(api, prepared), third["sources"][-1]["id"], False
    ).json()
    inputs_changed = api.retry(changed, run3["id"], key="retry-key-dddd")
    assert inputs_changed.status_code == 409
    assert inputs_changed.json()["error"]["code"] in (
        "INPUTS_CHANGED",
        "REVISION_CONFLICT",
    )


def test_injected_and_unbound_jobs_never_process_sources(
    api, prepared, session_factory, store
):
    api.prepare(prepared)
    real_run = session_factory().query(OnboardingRun).one()
    db = session_factory()
    db.add_all(
        [
            BackgroundJob(
                job_type=JOB_TYPE,
                status="PENDING",
                payload={"run_id": str(uuid.uuid4())},
                max_attempts=3,
                next_attempt_at=_past(),
                created_at=_past() - timedelta(hours=1),
            ),
            BackgroundJob(  # points at a real run that belongs to another job
                job_type=JOB_TYPE,
                status="PENDING",
                payload={
                    "run_id": str(real_run.id),
                    "session_id": str(real_run.session_id),
                    "input_revision": real_run.input_revision,
                    "manifest_digest": real_run.manifest_digest,
                },
                max_attempts=3,
                next_attempt_at=_past(),
                created_at=_past() - timedelta(hours=2),
            ),
        ]
    )
    db.commit()
    db.close()
    stats = worker_pass(session_factory, store)
    assert stats["failed"] == 2 and stats["succeeded"] == 1
    db = session_factory()
    injected = (
        db.query(BackgroundJob)
        .filter(BackgroundJob.job_type == JOB_TYPE, BackgroundJob.id != real_run.job_id)
        .all()
    )
    assert {j.status for j in injected} == {"DEAD_LETTER"}
    assert {j.last_error for j in injected} == {"INVALID_RUN_BINDING"}
    db.close()


def test_worker_ignores_other_job_types(api, session_factory, store):
    db = session_factory()
    JobQueue(db).enqueue(job_type="PORTFOLIO_REPORT", payload={})
    db.commit()
    db.close()
    assert worker_pass(session_factory, store)["processed"] == 0


def test_rolled_back_prepare_leaves_no_job_or_run(
    api, prepared, session_factory, monkeypatch
):
    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(IntakeService, "_add_receipt", boom)
    with pytest.raises(RuntimeError):
        api.prepare(prepared)
    db = session_factory()
    try:
        assert db.query(OnboardingRun).count() == 0
        assert (
            db.query(BackgroundJob).filter(BackgroundJob.job_type == JOB_TYPE).count()
            == 0
        )
    finally:
        db.close()


def _service(session_factory, store):
    return IntakeService(session_factory(), store, actor="thread-user")


def test_concurrent_prepare_requests_converge_on_one_run(
    api, prepared, session_factory, store
):
    results: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def attempt(n: int) -> None:
        service = _service(session_factory, store)
        try:
            barrier.wait()
            out = service.prepare(
                uuid.UUID(prepared["id"]),
                expected_row_version=prepared["row_version"],
                requested_input_revision=prepared["input_revision"],
                idempotency_key=f"concurrent-prep-{n}",
                access=ADMIN_ACCESS,
            )
            results.append(out["run"]["id"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            service.db.close()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert len(set(results)) == 1
    db = session_factory()
    assert db.query(OnboardingRun).count() == 1
    db.close()


def test_concurrent_identical_uploads_create_one_revision_and_quota_holds(
    api, session, session_factory, store, monkeypatch
):
    monkeypatch.setattr(settings, "onboarding_max_sources_per_session", 1)
    outcomes: list[object] = []
    barrier = threading.Barrier(4)

    def attempt(n: int) -> None:
        service = _service(session_factory, store)
        try:
            # Two identical (same key) + two competing (different keys/content).
            same = n < 2
            data = b"identical" if same else f"competing-{n}".encode()
            key = "same-upload-key-1" if same else f"other-key-{n:04d}"
            staged, family = service.stage_file(
                [data], filename="f.txt", declared_type="text/plain", deadline=None
            )
            barrier.wait()
            outcomes.append(
                service.add_source(
                    uuid.UUID(session["id"]),
                    kind="FILE",
                    role="SUPPORTING_MATERIAL",
                    staged=staged,
                    family=family,
                    display_name="f.txt",
                    declared_type="text/plain",
                    expected_row_version=session["row_version"],
                    idempotency_key=key,
                    access=ADMIN_ACCESS,
                )
            )
        except IntakeError as exc:
            outcomes.append(exc)
        finally:
            service.db.close()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    db = session_factory()
    try:
        assert db.query(OnboardingSource).count() == 1
        assert db.query(OnboardingSourceRevision).count() == 1
    finally:
        db.close()
    successes = [o for o in outcomes if isinstance(o, dict)]
    assert len({o["revision_id"] for o in successes}) == 1
    assert all(
        o.code
        in ("REVISION_CONFLICT", "SESSION_LIMIT_EXCEEDED", "IDEMPOTENCY_CONFLICT")
        for o in outcomes
        if isinstance(o, IntakeError)
    )


def test_cleanup_removes_only_unreferenced_aged_objects(
    api, prepared, session_factory, store, storage_root
):
    import os
    import time

    # A referenced original (then excluded) must survive.
    file_source = next(s for s in prepared["sources"] if s["kind"] == "FILE")
    api.include(prepared, file_source["id"], False)

    staged = store.stage([b"orphan bytes"], max_bytes=1024)
    orphan_key = store.finalize(staged)
    stale = store.stage([b"partial"], max_bytes=1024)
    young = store.stage([b"young partial"], max_bytes=1024)
    old = time.time() - settings.onboarding_orphan_min_age_seconds - 60
    os.utime(storage_root / "objects" / orphan_key, (old, old))
    os.utime(stale.path, (old, old))

    removed = preparation.cleanup_orphans(session_factory, store)
    assert removed["objects_removed"] == 1 and removed["staging_removed"] == 1
    assert not (storage_root / "objects" / orphan_key).exists()
    assert not stale.path.exists() and young.path.exists()

    db = session_factory()
    try:
        keys = [r.storage_key for r in db.query(OnboardingSourceRevision).all()]
    finally:
        db.close()
    assert keys and all((storage_root / "objects" / k).exists() for k in keys)
    # Even when aged, referenced revisions are never removed.
    for key in keys:
        os.utime(storage_root / "objects" / key, (old, old))
    assert preparation.cleanup_orphans(session_factory, store)["objects_removed"] == 0
    assert all((storage_root / "objects" / k).exists() for k in keys)


def test_storage_rejects_traversal_and_symlink_escapes(store, storage_root, tmp_path):
    store.check_writable()
    for bad in ("../outside", "aa/../../x", "/etc/passwd", "zz/" + "g" * 32):
        with pytest.raises(SourceStorageError):
            store.open_read(bad)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    staged = store.stage([b"data"], max_bytes=100)
    key = store.finalize(staged)
    target = storage_root / "objects" / key
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(SourceStorageError):
        store.open_read(key)


def test_worker_cli_dispatch_and_healthcheck(monkeypatch):
    from rai_gateway.workers import cli, onboarding

    called: dict[str, list[str]] = {}
    monkeypatch.setattr(
        onboarding, "main", lambda argv: called.setdefault("onboarding", argv) and 0
    )
    assert cli.main(["onboarding", "--once"]) == 0
    assert called["onboarding"] == ["--once"]
    monkeypatch.setattr(
        onboarding, "HEARTBEAT_FILE", onboarding.HEARTBEAT_FILE.with_name("absent.hb")
    )
    assert onboarding.healthcheck() == 1


def test_runtime_events_command_is_still_dispatched(monkeypatch):
    from rai_gateway.workers import cli, runtime_events

    seen: list[list[str]] = []
    monkeypatch.setattr(
        runtime_events, "main", lambda argv=None: seen.append(argv) or 0
    )
    assert cli.main(["runtime-events", "--once"]) == 0
    assert cli.main(["--once"]) == 0
    assert seen == [["runtime-events", "--once"], ["--once"]]


def test_worker_process_once_completes_a_committed_job_without_http_or_broker(
    api, prepared, db_engine, storage_root
):
    """Real `rai-worker onboarding --once` subprocess against the test schema."""
    import os
    import subprocess
    import sys

    run = api.prepare(prepared).json()["run"]
    assert run["state"] == "QUEUED"
    env = {
        **os.environ,
        "DATABASE_URL": f"{db_engine['base_url']}?options=-csearch_path%3D{db_engine['schema']}",
        "ONBOARDING_SOURCE_STORAGE_ROOT": str(storage_root),
    }
    done = subprocess.run(
        [sys.executable, "-m", "rai_gateway.workers.cli", "onboarding", "--once"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "'succeeded': 1" in done.stdout
    view = fresh(api, prepared)
    assert view["view"] == "READY_FOR_ANALYSIS"
    assert view["current_run"]["sources_checked"] == 2


def test_worker_refuses_to_start_when_storage_is_not_writable(tmp_path, monkeypatch):
    from rai_gateway.workers import onboarding

    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(
        settings, "onboarding_source_storage_root", str(blocker / "root")
    )
    assert onboarding.main(["--once"]) == 2
