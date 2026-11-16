"""Content Rule handling for provider-compatible calls, per agent version.

``agent_versions.content_rule_mode``:

* ``OFF``     - Content Rules are ignored for this version.
* ``OBSERVE`` - nothing is delayed or blocked. After the response is complete (streamed
  or not) the prompt and response are evaluated in the background and the result is
  recorded on the trajectory as an event, with risk signals/findings for violations.
* ``ENFORCE`` - (default when unset) the prompt is evaluated *before* anything is sent
  upstream, and a violated DENY rule blocks the call. Non-streaming responses are
  evaluated before they reach the client. Streamed responses cannot be held back, so
  they are observed after completion instead.

The evaluator only detects. RAI maps the rule's configured effect: a DENY rule blocks
only when the evaluator's confidence reaches ``minimum_confidence_to_intervene``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

OFF, OBSERVE, ENFORCE = "OFF", "OBSERVE", "ENFORCE"


@dataclass(slots=True)
class ContentGateResult:
    blocked: bool = False
    unavailable: bool = False
    rule_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    evaluated: bool = False
    content_type: str = ""
    evaluation: dict[str, Any] | None = None


def _receipt(session: Session, receipt_id: UUID) -> Any:
    from rai_gateway.models.runtime_enforcement import RuntimeEnforcementReceipt

    return (
        session.query(RuntimeEnforcementReceipt)
        .filter(RuntimeEnforcementReceipt.id == receipt_id)
        .first()
    )


def resolve_mode(session: Session, receipt_id: UUID) -> str:
    """Content Rule mode of the receipt's agent version (default ENFORCE)."""
    try:
        from rai_gateway.models.registry import AgentVersion

        receipt = _receipt(session, receipt_id)
        if receipt is None or receipt.agent_version_id is None:
            return ENFORCE
        version = (
            session.query(AgentVersion)
            .filter(AgentVersion.id == receipt.agent_version_id)
            .first()
        )
        mode = (version.content_rule_mode or "").upper() if version else ""
        return mode if mode in {OFF, OBSERVE, ENFORCE} else ENFORCE
    except Exception:  # noqa: BLE001
        logger.exception("Could not resolve content rule mode for %s", receipt_id)
        return ENFORCE


def evaluate_content_gate(
    session: Session,
    receipt_id: UUID,
    *,
    text: str,
    content_type: str,
    mode_hint: str | None = None,
) -> ContentGateResult:
    """Evaluate content rules and decide whether to block. Never raises.

    Nothing is recorded here; call :func:`record_evaluations` after the receipt has
    been finalized so the trajectory never sees a half-finished receipt.
    """
    from rai_gateway.risk_intelligence.content_evaluation import ContentEvaluationService
    from rai_gateway.risk_intelligence.domain import (
        ContentEvaluationMode,
        ContentEvaluationStatus,
    )

    result = ContentGateResult(content_type=content_type)
    try:
        receipt = _receipt(session, receipt_id)
        if receipt is None or not receipt.ai_system_id or not text.strip():
            return result
        eval_mode = {
            "INPUT": ContentEvaluationMode.PRE_INPUT_ENFORCED,
            "OUTPUT": ContentEvaluationMode.PRE_OUTPUT_ENFORCED,
        }.get(content_type, ContentEvaluationMode.OFFLINE_EVALUATED)
        if mode_hint == OBSERVE:
            eval_mode = (
                ContentEvaluationMode.POST_OUTPUT_OBSERVED
                if content_type == "OUTPUT"
                else ContentEvaluationMode.OFFLINE_EVALUATED
            )
        evaluation = ContentEvaluationService(session).evaluate_content(
            ai_system_id=receipt.ai_system_id,
            content=text,
            content_type=content_type,
            evaluation_mode=eval_mode.value,
        )
        status = evaluation.get("status")
        if status == ContentEvaluationStatus.FAILED.value:
            if evaluation.get("failure_type") == "EVALUATOR_NOT_CONFIGURED":
                return result  # no evaluator for this system: nothing to enforce
            result.evaluated = True
            result.unavailable = bool(evaluation.get("fail_closed"))
            result.evaluation = evaluation  # recorded so the failure is visible
            session.commit()
            return result
        result.evaluated = status in (
            ContentEvaluationStatus.COMPLETED.value,
            ContentEvaluationStatus.CACHED.value,
        )
        result.evaluation = evaluation
        for hint in evaluation.get("intervention_hints") or []:
            if hint.get("suggested_intervention") == "DENY":
                result.blocked = True
                result.rule_ids.append(str(hint.get("rule_id")))
                if hint.get("reason"):
                    result.reasons.append(str(hint["reason"]))
        session.commit()
    except Exception:  # noqa: BLE001 - enforcement point must not crash the call
        session.rollback()
        logger.exception("Content gate evaluation failed for receipt %s", receipt_id)
        result.unavailable = _fail_closed(session, receipt_id)
    return result


def record_evaluations(
    session: Session,
    receipt_id: UUID,
    evaluations: list[tuple[str, dict[str, Any], bool]],
) -> None:
    """Attach evaluation results to the trajectory (event + risk signals/findings).

    ``evaluations`` is ``[(content_type, evaluation, blocked)]``. Best effort.
    """
    if not evaluations:
        return
    try:
        from rai_gateway.evidence.domain import EvidenceEventType, EvidenceSourceType
        from rai_gateway.evidence.service import TrajectoryService
        from rai_gateway.risk_intelligence.risk_engine import RiskEngine

        receipt = _receipt(session, receipt_id)
        if receipt is None:
            return
        evidence = TrajectoryService(session)
        trajectory = evidence.project_receipt(receipt_id)
        if trajectory is None:
            return
        for content_type, evaluation, blocked in evaluations:
            risk_result = None
            if evaluation.get("intervention_hints"):
                try:
                    risk_result = RiskEngine(session).process_content_evaluation(
                        ai_system_id=receipt.ai_system_id,
                        trajectory_id=trajectory.id,
                        runtime_identity_id=receipt.runtime_identity_id,
                        agent_id=receipt.agent_id,
                        agent_version_id=receipt.agent_version_id,
                        evaluation_result=evaluation,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Recording content-rule risk signals failed")
            rules = _rule_rows(session, evaluation)
            violated = [r for r in rules if r["violated"]]
            failed = evaluation.get("status") == "FAILED"
            if failed:
                verdict = "EVALUATOR_FAILED"
                summary = (
                    f"{content_type.title()} not evaluated: "
                    f"{evaluation.get('error') or evaluation.get('failure_type')}"
                )
            elif blocked:
                verdict = "BLOCKED"
                names = ", ".join(r["title"] or r["rule_id"] for r in violated)
                summary = f"{content_type.title()} blocked by Content Rule: {names}"
            elif violated:
                verdict = "VIOLATION_OBSERVED"
                names = ", ".join(r["title"] or r["rule_id"] for r in violated)
                summary = f"{content_type.title()} violated Content Rule (not blocked): {names}"
            else:
                verdict = "PASSED"
                summary = f"{content_type.title()} passed {len(rules)} Content Rule(s)"
            evidence.append_event(
                trajectory,
                event_type=EvidenceEventType.CONTENT_EVALUATION_RESULT.value,
                source_type=EvidenceSourceType.DERIVED.value,
                source_reference=f"receipt:{receipt_id}:content:{content_type.lower()}",
                payload={
                    "content_type": content_type,
                    "decision": verdict,
                    "summary": summary,
                    "blocked": blocked,
                    "provider": evaluation.get("provider"),
                    "model": evaluation.get("model"),
                    "reasons": "; ".join(r["reason"] for r in violated if r["reason"]),
                    "rules": rules,
                    "evaluation": {
                        "id": evaluation.get("id"),
                        "status": evaluation.get("status"),
                        "failure_type": evaluation.get("failure_type"),
                        "error": evaluation.get("error"),
                        "provider": evaluation.get("provider"),
                        "model": evaluation.get("model"),
                        "cached": evaluation.get("cached"),
                        "evaluations": evaluation.get("evaluations"),
                        "intervention_hints": evaluation.get("intervention_hints"),
                    },
                    "risk": risk_result,
                },
                occurred_at=receipt.started_at,
                correlation_id=receipt.correlation_id,
                content_handling_mode="METADATA_ONLY",
            )
            if blocked:
                _record_block(evidence, trajectory, receipt, content_type, summary, violated)
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.exception("Recording content evaluation evidence failed for %s", receipt_id)


def _rule_rows(session: Session, evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    """Flat per-rule rows (title, effect, verdict, confidence) for the inspector."""
    from rai_gateway.models.policy_engine import ContentRule

    items = [e for e in (evaluation.get("evaluations") or []) if isinstance(e, dict)]
    if not items:
        return []
    ids = []
    for item in items:
        try:
            ids.append(UUID(str(item.get("rule_id"))))
        except (TypeError, ValueError):
            continue
    meta: dict[str, Any] = {}
    if ids:
        for rule in session.query(ContentRule).filter(ContentRule.id.in_(ids)).all():
            meta[str(rule.id)] = rule
    rows = []
    for item in items:
        rule = meta.get(str(item.get("rule_id")))
        rows.append(
            {
                "rule_id": str(item.get("rule_id")),
                "title": getattr(rule, "title", None),
                "effect": getattr(rule, "effect", None),
                "violated": bool(item.get("violated")),
                "confidence": item.get("confidence"),
                "severity": item.get("severity"),
                "reason": item.get("reason"),
                "evidence_spans": item.get("evidence_spans") or [],
            }
        )
    return rows


def _record_block(
    evidence: Any,
    trajectory: Any,
    receipt: Any,
    content_type: str,
    summary: str,
    violated: list[dict[str, Any]],
) -> None:
    """A Content Rule block is a decision: show it as DENY and open a finding.

    The receipt's own decision (tool/policy rules) is authoritative and immutable, so
    the content decision is recorded as its own authoritative event.
    """
    from rai_gateway.evidence.domain import (
        EvidenceEventType,
        EvidenceFindingType,
        EvidenceSourceType,
        FindingSeverity,
    )

    evidence.append_event(
        trajectory,
        event_type=EvidenceEventType.ACTION_DENIED.value,
        source_type=EvidenceSourceType.RAI_AUTHORITATIVE.value,
        source_reference=f"receipt:{receipt.id}:content_decision:{content_type.lower()}",
        payload={
            "enforcement_decision": "DENY",
            "reason_code": "CONTENT_POLICY_DENIED",
            "human_explanation": summary,
            "decided_by": "CONTENT_RULE",
            "content_type": content_type,
            "rule_ids": ", ".join(r["rule_id"] for r in violated),
            "reasons": "; ".join(r["reason"] for r in violated if r["reason"]),
        },
        occurred_at=receipt.started_at,
        correlation_id=receipt.correlation_id,
        trace_id=receipt.trace_id,
    )
    evidence.ensure_finding(
        trajectory,
        finding_type=EvidenceFindingType.POLICY_DENIAL.value,
        severity=FindingSeverity.HIGH.value,
        title="Blocked by Content Rule",
        description=summary,
    )


def observe_content(
    session: Session,
    receipt_id: UUID,
    *,
    prompt: str | None,
    response: str | None,
    streamed: bool,
) -> None:
    """Post-completion evaluation for OBSERVE (and streamed responses under ENFORCE)."""
    mode = resolve_mode(session, receipt_id)
    if mode == OFF:
        return
    # ENFORCE already checked the prompt (and non-streamed response) inline.
    targets: list[tuple[str, str]] = []
    if mode == OBSERVE and prompt:
        targets.append(("INPUT", prompt))
    if response and (mode == OBSERVE or (mode == ENFORCE and streamed)):
        targets.append(("OUTPUT", response))
    collected: list[tuple[str, dict[str, Any], bool]] = []
    for content_type, text in targets:
        gate = evaluate_content_gate(
            session,
            receipt_id,
            text=text,
            content_type=content_type,
            mode_hint=OBSERVE,
        )
        if gate.evaluation is not None:
            # Observed, never blocked: the call already completed.
            collected.append((content_type, gate.evaluation, False))
    record_evaluations(session, receipt_id, collected)


def _fail_closed(session: Session, receipt_id: UUID) -> bool:
    try:
        from rai_gateway.risk_intelligence.repository import RiskIntelligenceRepository

        receipt = _receipt(session, receipt_id)
        if receipt is None or not receipt.ai_system_id:
            return False
        config = RiskIntelligenceRepository(session).get_evaluator_config(
            receipt.ai_system_id
        )
        return bool(config and config.enabled and config.fail_closed)
    except Exception:  # noqa: BLE001
        return False
