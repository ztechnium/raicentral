"""Discovery worker orchestration (Step 04)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingDiscoveryCall,
    OnboardingExtractedBlock,
    OnboardingInventoryProposal,
    OnboardingProposalSet,
    OnboardingReconciliationSnapshot,
    OnboardingRun,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.preparation import (
    TerminalError,
    publish_failure,
    publish_success,
)
from rai_gateway.onboarding.registry_snapshot import RegistrySnapshot, capture_snapshot
from rai_gateway.onboarding.reconciliation import (
    proposals_from_fixture_partition,
    reconcile_proposals,
)
from rai_gateway.onboarding.service import DISCOVER_JOB_TYPE, STAGE_DISCOVER, canonical_digest_obj

SessionFactory = Callable[[], Session]


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        or job.job_type != DISCOVER_JOB_TYPE
        or run.stage != STAGE_DISCOVER
        or payload.get("session_id") != str(run.session_id)
        or payload.get("plan_digest") != run.plan_digest
    ):
        raise TerminalError("INVALID_RUN_BINDING")
    return run


def _partition_text(db: Session, block_id: str) -> str:
    block = db.get(OnboardingExtractedBlock, uuid.UUID(block_id))
    if block is None:
        return ""
    return block.text


def _execute_plan(db: Session, run: OnboardingRun) -> dict[str, Any]:
    plan = run.plan or {}
    partitions = plan.get("partitions") or []
    fixture_mode = bool(plan.get("fixture_mode"))
    proposals: list[dict[str, Any]] = []
    for part in partitions:
        texts = [_partition_text(db, bid) for bid in part.get("primary_block_ids") or []]
        merged = "\n".join(texts)
        if fixture_mode:
            proposals.extend(proposals_from_fixture_partition(merged))
    return {"proposals": proposals, "partitions_processed": len(partitions)}


def _publish_set(
    db: Session,
    run: OnboardingRun,
    outcome: dict[str, Any],
    snapshot: RegistrySnapshot,
) -> OnboardingProposalSet:
    digest = canonical_digest_obj(outcome["proposals"])
    ps = OnboardingProposalSet(
        session_id=run.session_id,
        discovery_run_id=run.id,
        content_digest=digest,
        provenance={
            "extraction_run_id": str(run.extraction_run_id),
            "plan_digest": run.plan_digest,
            "registry_snapshot_digest": snapshot.digest,
        },
        edit_version=1,
    )
    db.add(ps)
    db.flush()
    for raw in outcome["proposals"]:
        db.add(
            OnboardingInventoryProposal(
                set_id=ps.id,
                entity_type=raw["entity_type"],
                generated=raw["generated"],
            )
        )
    db.flush()
    proposals = [
        {
            "id": str(p.id),
            "entity_type": p.entity_type,
            "generated": p.generated,
        }
        for p in db.query(OnboardingInventoryProposal)
        .filter(OnboardingInventoryProposal.set_id == ps.id)
        .all()
    ]
    recon_body = reconcile_proposals(proposals, snapshot)
    snap = OnboardingReconciliationSnapshot(
        set_id=ps.id,
        edit_version=1,
        registry_projection={"digest": snapshot.digest},
        registry_digest=snapshot.digest,
        matching_rule_version=recon_body["matching_rule_version"],
        matches=recon_body["matches"],
        questions=recon_body["questions"],
    )
    db.add(snap)
    db.flush()
    ps.current_reconciliation_id = snap.id
    return ps


def process_discovery_job(
    session_factory: SessionFactory,
    job_id: uuid.UUID,
    token: str,
) -> str:
    db = session_factory()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        try:
            run = _validate_binding(db, job)
        except TerminalError as exc:
            from rai_gateway.onboarding.preparation import _fail_unbound

            _fail_unbound(db, job_id, token, exc.code)
            return "invalid_binding"
        run_id = run.id
        plan = run.plan or {}
        db.rollback()
    finally:
        db.close()

    db = session_factory()
    try:
        run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one()
        if not plan.get("fixture_mode"):
            ok = publish_failure(
                db, run_id, job_id, token, code="AI_PROVIDER_REQUIRED", terminal=True
            )
            return "failed" if ok else "lost_ownership"
        snapshot = capture_snapshot(db)
        outcome = _execute_plan(db, run)
        for ordinal, part in enumerate(plan.get("partitions") or []):
            db.add(
                OnboardingDiscoveryCall(
                    run_id=run_id,
                    ordinal=ordinal,
                    request_digest=part.get("request_digest", ""),
                    dispatch_started_at=_now(),
                    outcome="SUCCEEDED",
                    result={"proposal_count": len(outcome["proposals"])},
                )
            )
        ps = _publish_set(db, run, outcome, snapshot)
        result = {
            "proposal_set_id": str(ps.id),
            "proposal_count": len(outcome["proposals"]),
            "partitions_processed": outcome["partitions_processed"],
        }
        ok = publish_success(db, run_id, job_id, token, result)
        return "succeeded" if ok else "lost_ownership"
    except Exception:
        db.rollback()
        ok = publish_failure(
            db, run_id, job_id, token, code="INTERNAL_ERROR", terminal=True
        )
        return "failed" if ok else "lost_ownership"
    finally:
        db.close()