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
from rai_gateway.onboarding import ai_calls, discovery_ai
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
    rows = (
        db.query(OnboardingInventoryProposal)
        .filter(OnboardingInventoryProposal.set_id == ps.id)
        .all()
    )
    linked = discovery_ai.link_parents(
        [(str(r.id), r.entity_type, r.generated) for r in rows],
        snapshot.projection["indexes"]["system_by_key"],
    )
    for row in rows:
        if str(row.id) in linked:
            row.generated = {
                **row.generated,
                "relationships": {
                    **(row.generated.get("relationships") or {}),
                    **linked[str(row.id)],
                },
            }
    renamed = discovery_ai.deconflict_agent_keys(
        [(str(r.id), r.entity_type, r.generated) for r in rows],
        snapshot.projection["indexes"]["agent_by_key"],
    )
    for row in rows:
        if str(row.id) in renamed:
            row.generated = renamed[str(row.id)]
    db.flush()
    proposals = [
        {
            "id": str(p.id),
            "entity_type": p.entity_type,
            "generated": p.generated,
        }
        for p in rows
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


def _fail(db: Session, run_id: uuid.UUID, job_id: uuid.UUID, token: str, code: str) -> str:
    db.rollback()
    ok = publish_failure(db, run_id, job_id, token, code=code, terminal=True)
    return "failed" if ok else "lost_ownership"


def _run_live(
    db: Session,
    run: OnboardingRun,
    job_id: uuid.UUID,
    token: str,
    plan: dict[str, Any],
) -> str:
    """Analyze the extracted sources with the configured AI, one bounded call per partition."""
    run_id = run.id
    limits = plan.get("limits") or {}
    try:
        ctx = ai_calls.resolve_context(db, plan)
        snapshot = capture_snapshot(db)
        parsed = ai_calls.execute_partitions(
            db,
            plan,
            ctx,
            run_id=run_id,
            job_id=job_id,
            token=token,
            call_model=OnboardingDiscoveryCall,
            system=discovery_ai.system_prompt(),
            schema=discovery_ai.OUTPUT_SCHEMA,
            schema_name="onboarding_inventory",
            user_prompt=discovery_ai.build_user_prompt,
            parse=discovery_ai.parse_partition,
            summarize=lambda r: {
                "proposal_count": len(r.proposals),
                "dropped_unverified": r.dropped_unverified,
                "dropped_invalid": r.dropped_invalid,
            },
        )
    except ai_calls.LiveFailure as exc:
        return _fail(db, run_id, job_id, token, exc.code)

    proposals = discovery_ai.merge_proposals([p.proposals for p in parsed])
    if len(proposals) > int(limits.get("max_proposals", 500)):
        return _fail(db, run_id, job_id, token, "TOO_MANY_PROPOSALS")
    outcome = {
        "proposals": proposals,
        "partitions_processed": len(plan.get("partitions") or []),
    }
    try:
        ps = _publish_set(db, run, outcome, snapshot)
        result_body = {
            "proposal_set_id": str(ps.id),
            "proposal_count": len(proposals),
            "partitions_processed": outcome["partitions_processed"],
        }
        ok = publish_success(db, run_id, job_id, token, result_body)
        return "succeeded" if ok else "lost_ownership"
    except Exception:
        return _fail(db, run_id, job_id, token, "INTERNAL_ERROR")


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
            return _run_live(db, run, job_id, token, plan)
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