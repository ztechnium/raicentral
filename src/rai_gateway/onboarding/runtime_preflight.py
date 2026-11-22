"""Read-only runtime readiness inspection (no assurance side effects)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.evaluations.service import EvaluationService
from rai_gateway.evaluations.domain import AssuranceStatus
from rai_gateway.models.evaluations import AssuranceRecord
from rai_gateway.models.identity import AgentRuntimeIdentity, RuntimeCredential
from rai_gateway.models.productization import SdkIntegration
from rai_gateway.registry.eligibility import EligibilityService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def assurance_observation(
    db: Session, *, agent_version_id: UUID, environment: str = "PRODUCTION"
) -> dict[str, Any]:
    """Assurance eligibility without refresh_assurance_expiry commits."""
    from rai_gateway.models.registry import Agent, AgentVersion

    version = db.query(AgentVersion).filter(AgentVersion.id == agent_version_id).first()
    if version is None:
        return {"ok": False, "reason": "version_not_found"}
    agent = db.query(Agent).filter(Agent.id == version.agent_id).first()
    if agent is None:
        return {"ok": False, "reason": "agent_not_found"}
    svc = EvaluationService(db)
    requirements = svc.resolve_requirements(
        ai_system_id=agent.ai_system_id, environment=environment
    )
    required = [r for r in requirements if getattr(r, "required", True)]
    if not required:
        return {"ok": True, "reason": "no_required_evaluations", "required_count": 0}
    current = (
        db.query(AssuranceRecord)
        .filter(
            AssuranceRecord.agent_version_id == agent_version_id,
            AssuranceRecord.environment == environment,
            AssuranceRecord.superseded_at.is_(None),
            AssuranceRecord.status.in_(["VALID", "CONDITIONAL"]),
        )
        .order_by(AssuranceRecord.created_at.desc())
        .first()
    )
    if current is None:
        return {"ok": False, "reason": "assurance_missing", "required_count": len(required)}
    if current.valid_until and current.valid_until <= _utcnow():
        return {"ok": False, "reason": "assurance_expired", "required_count": len(required)}
    return {"ok": True, "reason": "assurance_valid", "required_count": len(required)}


def inspect_runtime_readiness(
    db: Session,
    *,
    ai_system_id: UUID,
    agent_id: UUID,
    agent_version_id: UUID,
    runtime_identity_id: UUID | None,
    sdk_integration_id: UUID | None,
) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    eligibility = EligibilityService(db).evaluate(
        system_id=ai_system_id,
        agent_id=agent_id,
        version_id=agent_version_id,
        environment="PRODUCTION",
        skip_assurance=True,
    )
    if not eligibility.eligible:
        blockers.append({"code": "LIFECYCLE", "message": eligibility.reason})

    assurance = assurance_observation(db, agent_version_id=agent_version_id)
    if not assurance.get("ok"):
        blockers.append(
            {"code": "ASSURANCE", "message": str(assurance.get("reason", "assurance"))}
        )

    runtime_ok = False
    credential_ok = False
    integration_ok = False
    if runtime_identity_id:
        runtime = db.get(AgentRuntimeIdentity, runtime_identity_id)
        if runtime is None:
            blockers.append({"code": "RUNTIME", "message": "runtime_identity_not_found"})
        elif runtime.status != "ACTIVE":
            blockers.append({"code": "RUNTIME", "message": f"runtime_status_{runtime.status}"})
        else:
            runtime_ok = True
            active_cred = (
                db.query(RuntimeCredential)
                .filter(
                    RuntimeCredential.runtime_identity_id == runtime_identity_id,
                    RuntimeCredential.status == "ACTIVE",
                )
                .count()
            )
            credential_ok = active_cred > 0
            if not credential_ok:
                blockers.append({"code": "CREDENTIAL", "message": "no_active_credential"})

    if sdk_integration_id:
        integration = db.get(SdkIntegration, sdk_integration_id)
        if integration is None:
            blockers.append({"code": "INTEGRATION", "message": "integration_not_found"})
        elif integration.runtime_identity_id != runtime_identity_id:
            blockers.append({"code": "INTEGRATION", "message": "integration_runtime_mismatch"})
        elif (
            (integration.auth_method or "").upper() != "API_KEY"
            or (integration.enforcement_mode or "").upper() != "HOOK_ENFORCED"
            or not integration.fail_closed
        ):
            blockers.append(
                {"code": "INTEGRATION", "message": "unsupported_sdk_configuration"}
            )
        else:
            integration_ok = True

    return {
        "eligible_for_connection": len(blockers) == 0,
        "blockers": blockers,
        "signals": {
            "lifecycle_eligible": eligibility.eligible,
            "runtime_configured": runtime_ok,
            "credential_present": credential_ok,
            "integration_supported": integration_ok,
        },
    }