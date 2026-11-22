"""Post-admission assertions and attempt aggregation for Step 07."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import OnboardingVerificationDispatch
from rai_gateway.onboarding.service import utcnow
from rai_gateway.onboarding.verification_capabilities import adapter_status


def evaluate_dispatch(db: Session, dispatch: OnboardingVerificationDispatch) -> None:
    scenario = dispatch.scenario or {}
    if adapter_status(scenario) == "BLOCKED":
        dispatch.verdict = dispatch.verdict or "BLOCKED"
        dispatch.admission_state = "SKIPPED"
        dispatch.bootstrap_state = "SKIPPED"
        dispatch.completed_at = utcnow()
        dispatch.evidence = {
            "claim": scenario.get("claim", "DIAGNOSTIC"),
            "reason": scenario.get("blocked_code") or "adapter_blocked",
        }
        db.flush()
        return
    if not dispatch.decision_id:
        dispatch.verdict = "NOT_EXERCISED"
        dispatch.admission_state = "OUTCOME_UNKNOWN"
        dispatch.bootstrap_state = "OUTCOME_UNKNOWN"
        dispatch.completed_at = utcnow()
        dispatch.evidence = {
            "claim": "DIAGNOSTIC",
            "reason": "awaiting_harness_or_live_request",
        }
        db.flush()
        return
    from rai_gateway.models.productization import SdkDecisionRecord

    decision = db.get(SdkDecisionRecord, dispatch.decision_id)
    if decision is None:
        dispatch.verdict = "INCONCLUSIVE"
        dispatch.evidence = {"reason": "decision_missing"}
    else:
        expected = (scenario.get("expected_decision") or "").upper()
        actual = (decision.decision or "").upper()
        dispatch.verdict = "PASS" if expected and actual == expected else "FAIL"
        dispatch.evidence = {
            "claim": "SERVER_DECISION",
            "expected_decision": expected,
            "actual_decision": actual,
            "decision_id": str(decision.id),
        }
    dispatch.admission_state = "COMPLETED"
    dispatch.bootstrap_state = "COMPLETED"
    dispatch.completed_at = utcnow()
    db.flush()


def aggregate_attempt(dispatches: list[OnboardingVerificationDispatch]) -> dict[str, Any]:
    counts = {
        "selected": len(dispatches),
        "passed": 0,
        "failed": 0,
        "blocked": 0,
        "not_exercised": 0,
        "inconclusive": 0,
        "skipped": 0,
    }
    for dispatch in dispatches:
        verdict = (dispatch.verdict or "PENDING").upper()
        if verdict == "PASS":
            counts["passed"] += 1
        elif verdict == "FAIL":
            counts["failed"] += 1
        elif verdict == "BLOCKED":
            counts["blocked"] += 1
        elif verdict == "NOT_EXERCISED":
            counts["not_exercised"] += 1
        elif verdict == "INCONCLUSIVE":
            counts["inconclusive"] += 1
        else:
            counts["skipped"] += 1
    actionable = [d for d in dispatches if (d.verdict or "").upper() not in ("BLOCKED", "SKIPPED")]
    all_pass = bool(actionable) and all((d.verdict or "").upper() == "PASS" for d in actionable)
    terminal = "SUCCEEDED" if all_pass and counts["failed"] == 0 else "FAILED"
    if counts["selected"] == counts["blocked"] + counts["skipped"]:
        terminal = "FAILED"
    return {**counts, "terminal_status": terminal, "claim": "SERVER_DECISION"}