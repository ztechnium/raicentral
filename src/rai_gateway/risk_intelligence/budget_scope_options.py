"""Selectable records for the budget form's "Applies to" scope.

Each option is ``{value, label, detail}``; ``value`` is what the budget stores as
``scope_id``. When an AI system is given, only records related to it are returned
(for models and tools: the ones its agent versions depend on). Without one, all
records of that type are returned.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.models.identity import AgentRuntimeIdentity, DelegationGrant
from rai_gateway.models.registry import (
    Agent,
    AgentVersion,
    AgentVersionDependency,
    AISystem,
    ModelResource,
    ToolResource,
)

SUPPORTED_SCOPES = {
    "AI_SYSTEM",
    "AGENT",
    "AGENT_VERSION",
    "RUNTIME_IDENTITY",
    "DELEGATION",
    "MODEL",
    "TOOL",
}


def _opt(value: Any, label: str, detail: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"value": str(value), "label": label, "detail": detail, **extra}


def _system_version_ids(db: Session, ai_system_id: UUID) -> list[UUID]:
    rows = (
        db.query(AgentVersion.id)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .filter(Agent.ai_system_id == ai_system_id)
        .all()
    )
    return [r[0] for r in rows]


def _dependency_ids(db: Session, ai_system_id: UUID, resource_type: str) -> set[UUID]:
    version_ids = _system_version_ids(db, ai_system_id)
    if not version_ids:
        return set()
    rows = (
        db.query(AgentVersionDependency.resource_id)
        .filter(
            AgentVersionDependency.agent_version_id.in_(version_ids),
            AgentVersionDependency.resource_type.ilike(resource_type),
        )
        .all()
    )
    return {r[0] for r in rows}


def list_scope_options(
    db: Session, *, scope_type: str, ai_system_id: UUID | None = None
) -> list[dict[str, Any]]:
    scope = scope_type.upper()
    if scope not in SUPPORTED_SCOPES:
        return []

    if scope == "AI_SYSTEM":
        rows = db.query(AISystem).order_by(AISystem.name).all()
        return [
            _opt(r.id, r.name, getattr(r, "system_key", None))
            for r in rows
            if ai_system_id is None or r.id == ai_system_id
        ]

    if scope == "AGENT":
        q = db.query(Agent).order_by(Agent.name)
        if ai_system_id:
            q = q.filter(Agent.ai_system_id == ai_system_id)
        return [_opt(r.id, r.name, r.agent_key) for r in q.all()]

    if scope == "AGENT_VERSION":
        q = (
            db.query(AgentVersion, Agent)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .order_by(Agent.name, AgentVersion.version_number)
        )
        if ai_system_id:
            q = q.filter(Agent.ai_system_id == ai_system_id)
        return [
            _opt(v.id, f"{a.name} v{v.version_number}", v.lifecycle_status)
            for v, a in q.all()
        ]

    if scope == "RUNTIME_IDENTITY":
        q = (
            db.query(AgentRuntimeIdentity, Agent)
            .join(Agent, Agent.id == AgentRuntimeIdentity.agent_id)
            .order_by(AgentRuntimeIdentity.name)
        )
        if ai_system_id:
            q = q.filter(Agent.ai_system_id == ai_system_id)
        return [
            _opt(
                i.id,
                i.name,
                " · ".join(x for x in (a.name, i.environment, i.status) if x),
            )
            for i, a in q.all()
        ]

    if scope == "DELEGATION":
        q = (
            db.query(DelegationGrant, AgentRuntimeIdentity, Agent)
            .join(
                AgentRuntimeIdentity,
                AgentRuntimeIdentity.id == DelegationGrant.delegate_runtime_identity_id,
            )
            .join(Agent, Agent.id == AgentRuntimeIdentity.agent_id)
            .order_by(DelegationGrant.created_at.desc())
        )
        if ai_system_id:
            q = q.filter(Agent.ai_system_id == ai_system_id)
        return [
            _opt(
                d.id,
                f"{d.delegator_type}:{d.delegator_id} → {i.name}",
                d.status,
            )
            for d, i, _a in q.all()
        ]

    if scope == "MODEL":
        q = db.query(ModelResource).order_by(ModelResource.model_name)
        if ai_system_id:
            ids = _dependency_ids(db, ai_system_id, "model")
            if not ids:
                return []
            q = q.filter(ModelResource.id.in_(ids))
        return [
            _opt(
                m.id,
                m.model_name,
                " · ".join(x for x in (m.provider, m.model_version) if x),
            )
            for m in q.all()
        ]

    # TOOL
    q = db.query(ToolResource).order_by(ToolResource.name)
    if ai_system_id:
        ids = _dependency_ids(db, ai_system_id, "tool")
        if not ids:
            return []
        q = q.filter(ToolResource.id.in_(ids))
    return [
        _opt(t.id, t.name, t.tool_key, tool_name=t.name) for t in q.all()
    ]
