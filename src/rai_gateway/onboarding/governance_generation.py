"""Governance worker orchestration (Step 05)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingExtractedBlock,
    OnboardingGovernanceCall,
    OnboardingGovernanceClause,
    OnboardingGovernanceProposal,
    OnboardingGovernanceProposalSet,
    OnboardingGovernanceReconciliationSnapshot,
    OnboardingRun,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.governance_proposals import (
    clauses_from_fixture_partition,
    proposals_from_clauses,
)
from rai_gateway.onboarding.governance_reconciliation import reconcile_governance_proposals
from rai_gateway.onboarding.governance_snapshot import capture_governance_baseline
from rai_gateway.onboarding.preparation import (
    TerminalError,
    publish_failure,
    publish_success,
)
from rai_gateway.onboarding.service import (
    GOVERNANCE_JOB_TYPE,
    STAGE_GOVERNANCE,
    canonical_digest_obj,
)

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
        or job.job_type != GOVERNANCE_JOB_TYPE
        or run.stage != STAGE_GOVERNANCE
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
    clauses: list[dict[str, Any]] = []
    ordinal_base = 0
    for part in partitions:
        texts = [_partition_text(db, bid) for bid in part.get("primary_block_ids") or []]
        merged = "\n".join(texts)
        part_clauses = clauses_from_fixture_partition(merged)
        for c in part_clauses:
            c["ordinal"] = ordinal_base + c["ordinal"]
        ordinal_base += len(part_clauses)
        clauses.extend(part_clauses)
    proposals = proposals_from_clauses(clauses)
    return {
        "clauses": clauses,
        "proposals": proposals,
        "partitions_processed": len(partitions),
    }


def _publish_set(
    db: Session,
    run: OnboardingRun,
    outcome: dict[str, Any],
    baseline_digest: str,
) -> OnboardingGovernanceProposalSet:
    content_digest = canonical_digest_obj(outcome["proposals"])
    clause_digest = canonical_digest_obj(outcome["clauses"])
    inventory_set_id = run.inventory_proposal_set_id
    assert inventory_set_id is not None
    ps = OnboardingGovernanceProposalSet(
        session_id=run.session_id,
        governance_run_id=run.id,
        inventory_proposal_set_id=inventory_set_id,
        content_digest=content_digest,
        clause_digest=clause_digest,
        provenance={
            "extraction_run_id": str(run.extraction_run_id),
            "inventory_proposal_set_id": str(inventory_set_id),
            "plan_digest": run.plan_digest,
            "baseline_digest": baseline_digest,
        },
        edit_version=1,
    )
    db.add(ps)
    db.flush()
    for clause in outcome["clauses"]:
        db.add(
            OnboardingGovernanceClause(
                set_id=ps.id,
                ordinal=clause["ordinal"],
                requirement=clause,
                outcome=clause.get("outcome", "mapped_proposal"),
                evidence=clause.get("evidence") or [],
            )
        )
    proposal_rows: list[OnboardingGovernanceProposal] = []
    for raw in outcome["proposals"]:
        row = OnboardingGovernanceProposal(
            set_id=ps.id,
            proposal_family=raw["proposal_family"],
            generated=raw["generated"],
        )
        db.add(row)
        proposal_rows.append(row)
    db.flush()
    proposals_with_ids = [
        {
            "id": str(p.id),
            "proposal_family": p.proposal_family,
            "generated": p.generated,
        }
        for p in proposal_rows
    ]
    baseline = capture_governance_baseline(db)
    recon_body = reconcile_governance_proposals(proposals_with_ids, baseline)
    snap = OnboardingGovernanceReconciliationSnapshot(
        set_id=ps.id,
        edit_version=1,
        baseline_projection={"digest": baseline.digest, "record_count": baseline.projection["record_count"]},
        baseline_digest=baseline.digest,
        matching_rule_version=recon_body["matching_rule_version"],
        matches=recon_body["matches"],
        capabilities=recon_body["capabilities"],
        questions=recon_body["questions"],
    )
    db.add(snap)
    db.flush()
    ps.current_reconciliation_id = snap.id
    return ps


def process_governance_job(
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
        baseline = capture_governance_baseline(db)
        outcome = _execute_plan(db, run)
        for ordinal, part in enumerate(plan.get("partitions") or []):
            db.add(
                OnboardingGovernanceCall(
                    run_id=run_id,
                    ordinal=ordinal,
                    request_digest=part.get("request_digest", ""),
                    dispatch_started_at=_now(),
                    outcome="SUCCEEDED",
                    result={
                        "clause_count": len(outcome["clauses"]),
                        "proposal_count": len(outcome["proposals"]),
                    },
                )
            )
        ps = _publish_set(db, run, outcome, baseline.digest)
        result = {
            "governance_set_id": str(ps.id),
            "clause_count": len(outcome["clauses"]),
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