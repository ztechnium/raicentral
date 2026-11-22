"""Worker for onboarding delta analysis runs (Step 08)."""

from __future__ import annotations

import uuid
from typing import Callable

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingBaseline,
    OnboardingChangeFinding,
    OnboardingDeltaPlan,
    OnboardingImpactEdge,
    OnboardingRun,
    OnboardingSession,
    OnboardingUpdateReconciliation,
    OnboardingVerificationImpact,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.delta_assertions import (
    compare_assertion_sets,
    governance_assertions,
    inventory_assertions,
    summarize_diff_counts,
)
from rai_gateway.onboarding.delta_baseline import capture_current_domain
from rai_gateway.onboarding.delta_coordinator import DeltaUpdateService
from rai_gateway.onboarding.delta_impact import build_impact_edges
from rai_gateway.onboarding.delta_reconciliation import reconcile_views
from rai_gateway.onboarding.delta_verification_impact import (
    compute_verification_impacts,
)
from rai_gateway.onboarding.preparation import (
    TerminalError,
    publish_failure,
    publish_success,
)
from rai_gateway.onboarding.service import (
    DELTA_JOB_TYPE,
    STAGE_ANALYZE_DELTA,
    canonical_digest_obj,
)

SessionFactory = Callable[[], Session]


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
        or job.job_type != DELTA_JOB_TYPE
        or run.stage != STAGE_ANALYZE_DELTA
        or payload.get("session_id") != str(run.session_id)
        or payload.get("plan_digest") != run.plan_digest
    ):
        raise TerminalError("INVALID_RUN_BINDING")
    return run


def _analyze(db: Session, run: OnboardingRun, plan: OnboardingDeltaPlan) -> dict:
    session = db.get(OnboardingSession, run.session_id)
    if session is None:
        raise TerminalError("SESSION_NOT_FOUND")
    baseline = db.get(OnboardingBaseline, plan.baseline_id)
    if baseline is None:
        raise TerminalError("BASELINE_NOT_FOUND")
    baseline_session_id = session.baseline_session_id or session.id
    base_inv, base_clauses = DeltaUpdateService.load_session_assertion_sources(
        db, baseline_session_id
    )
    cur_inv, cur_clauses = DeltaUpdateService.load_session_assertion_sources(db, session.id)
    baseline_assertions = inventory_assertions(base_inv) + governance_assertions(base_clauses)
    current_assertions = inventory_assertions(cur_inv) + governance_assertions(cur_clauses)
    diffs = compare_assertion_sets(baseline_assertions, current_assertions)
    bindings = DeltaUpdateService.load_applied_bindings(db, baseline_session_id)
    edges, truncated = build_impact_edges(diffs, bindings=bindings)
    projection, current_digest = capture_current_domain(db)
    recon = reconcile_views(
        baseline_projection=baseline.domain_projection,
        baseline_digest=baseline.domain_digest,
        current_projection=projection,
        source_diffs=diffs,
    )
    dispatches = DeltaUpdateService.load_verification_dispatches(db, baseline_session_id)
    ver_impacts = compute_verification_impacts(
        dispatches=dispatches,
        material_diffs=[d for d in diffs if d.change_type != "UNCHANGED"],
    )
    for ordinal, diff in enumerate(diffs):
        before = diff.before or {}
        after = diff.after or {}
        db.add(
            OnboardingChangeFinding(
                delta_plan_id=plan.id,
                ordinal=ordinal,
                change_type=diff.change_type,
                identity=diff.identity,
                before_digest=before.get("digest"),
                after_digest=after.get("digest"),
                summary=diff.reason,
                impact_level="HIGH" if diff.change_type in ("MODIFIED", "CONFLICT") else "MEDIUM",
                provenance={"source": before.get("source") or after.get("source")},
            )
        )
    for edge in edges:
        db.add(
            OnboardingImpactEdge(
                delta_plan_id=plan.id,
                finding_ordinal=edge["finding_ordinal"],
                ordinal=edge["ordinal"],
                edge_type=edge["edge_type"],
                reference_kind=edge["reference_kind"],
                reference_id=edge["reference_id"],
                direct=edge.get("direct", True),
                confidence=edge["confidence"],
                reason=edge["reason"],
            )
        )
    db.add(
        OnboardingUpdateReconciliation(
            delta_plan_id=plan.id,
            edit_version=1,
            baseline_digest=baseline.domain_digest,
            current_domain_digest=current_digest,
            source_digest=plan.digest,
            difference=recon,
        )
    )
    for row in ver_impacts:
        db.add(
            OnboardingVerificationImpact(
                delta_plan_id=plan.id,
                ordinal=row["ordinal"],
                verification_attempt_id=(
                    uuid.UUID(str(row["verification_attempt_id"]))
                    if row.get("verification_attempt_id")
                    else None
                ),
                scenario_key=row["scenario_key"],
                target_digest=row["target_digest"],
                applicability=row["applicability"],
                reason=row["reason"],
            )
        )
    plan.status = "SUCCEEDED"
    session.update_status = "PROPOSALS_READY" if any(d.change_type != "UNCHANGED" for d in diffs) else "REVIEW_REQUIRED"
    return {
        "finding_count": len(diffs),
        "source_delta_counts": summarize_diff_counts(diffs),
        "impact_edge_count": len(edges),
        "impact_truncated": truncated,
        "reconciliation_situation": recon.get("situation"),
        "digest": canonical_digest_obj(
            {"findings": len(diffs), "edges": len(edges), "plan_id": str(plan.id)}
        ),
    }


def process_delta_job(session_factory: SessionFactory, job_id: uuid.UUID, token: str) -> str:
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
        plan_id = run.delta_plan_id
        db.rollback()
    finally:
        db.close()

    db = session_factory()
    try:
        run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one()
        plan = db.get(OnboardingDeltaPlan, plan_id)
        if plan is None:
            ok = publish_failure(
                db, run_id, job_id, token, code="PLAN_NOT_FOUND", terminal=True
            )
            return "failed" if ok else "lost_ownership"
        result = _analyze(db, run, plan)
        ok = publish_success(db, run_id, job_id, token, result)
        return "succeeded" if ok else "lost_ownership"
    except Exception:
        db.rollback()
        ok = publish_failure(db, run_id, job_id, token, code="INTERNAL_ERROR", terminal=True)
        return "failed" if ok else "lost_ownership"
    finally:
        db.close()
