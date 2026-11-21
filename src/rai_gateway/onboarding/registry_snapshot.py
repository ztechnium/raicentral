"""Read-only registry snapshot for onboarding inventory reconciliation (Step 04)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from rai_gateway.models.registry import (
    AISystem,
    Agent,
    AgentVersion,
    AgentVersionDependency,
    DataSource,
    ExternalService,
    MCPServer,
    ModelResource,
    ToolResource,
)
from rai_gateway.onboarding.service import canonical_digest_obj

MATCHING_RULE_VERSION = "2026-11-21"
MAX_RECORDS = 20_000


def _field_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _row_system(row: AISystem) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "system_key": row.system_key,
        "name": row.name,
        "description": row.description,
        "business_purpose": row.business_purpose,
        "business_unit": row.business_unit,
        "owner_id": row.owner_id,
        "technical_owner_id": row.technical_owner_id,
        "status": row.status,
        "criticality": row.criticality,
        "risk_tier": row.risk_tier,
        "data_classification": row.data_classification,
        "digest": None,
    }


def _row_agent(row: Agent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "agent_key": row.agent_key,
        "ai_system_id": str(row.ai_system_id),
        "name": row.name,
        "description": row.description,
        "business_purpose": row.business_purpose,
        "owner_id": row.owner_id,
        "status": row.status,
        "current_version_id": (
            str(row.current_version_id) if row.current_version_id else None
        ),
        "digest": None,
    }


def _row_version(row: AgentVersion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "agent_id": str(row.agent_id),
        "version_number": row.version_number,
        "change_summary": row.change_summary,
        "lifecycle_status": row.lifecycle_status,
        "configuration_digest": row.configuration_digest,
        "digest": None,
    }


def _row_model(row: ModelResource) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "scope": row.scope,
        "provider": row.provider,
        "model_name": row.model_name,
        "model_version": row.model_version,
        "deployment_reference": row.deployment_reference,
        "hosting_type": row.hosting_type,
        "status": row.status,
        "digest": None,
    }


def _row_tool(row: ToolResource) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "scope": row.scope,
        "tool_key": row.tool_key,
        "name": row.name,
        "description": row.description,
        "category": row.category,
        "provider": row.provider,
        "mcp_server_id": str(row.mcp_server_id) if row.mcp_server_id else None,
        "sensitivity": row.sensitivity,
        "consequential_action": row.consequential_action,
        "status": row.status,
        "digest": None,
    }


def _row_mcp(row: MCPServer) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "scope": row.scope,
        "server_key": row.server_key,
        "name": row.name,
        "description": row.description,
        "provider": row.provider,
        "environment": row.environment,
        "endpoint_reference": row.endpoint_reference,
        "trust_classification": row.trust_classification,
        "status": row.status,
        "digest": None,
    }


def _row_service(row: ExternalService) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "scope": row.scope,
        "service_key": row.service_key,
        "name": row.name,
        "description": row.description,
        "provider": row.provider,
        "environment": row.environment,
        "endpoint_reference": row.endpoint_reference,
        "sensitivity": row.sensitivity,
        "trust_classification": row.trust_classification,
        "status": row.status,
        "digest": None,
    }


def _row_data(row: DataSource) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "scope": row.scope,
        "data_source_key": row.data_source_key,
        "name": row.name,
        "description": row.description,
        "owner": row.owner,
        "classification": row.classification,
        "contains_personal_data": row.contains_personal_data,
        "contains_sensitive_data": row.contains_sensitive_data,
        "jurisdiction": row.jurisdiction,
        "status": row.status,
        "digest": None,
    }


def _row_dependency(row: AgentVersionDependency) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "agent_version_id": str(row.agent_version_id),
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id),
        "usage_purpose": row.usage_purpose,
        "access_mode": row.access_mode,
        "capability_declaration": row.capability_declaration,
        "required": row.required,
        "digest": None,
    }


@dataclass(frozen=True)
class RegistrySnapshot:
    captured_at: str
    matching_rule_version: str
    digest: str
    projection: dict[str, Any]
    record_count: int


def capture_snapshot(db: Session) -> RegistrySnapshot:
    """Capture registry state under a single REPEATABLE READ transaction."""
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    systems = [_row_system(r) for r in db.query(AISystem).order_by(AISystem.system_key)]
    agents = [_row_agent(r) for r in db.query(Agent).order_by(Agent.agent_key)]
    versions = [
        _row_version(r)
        for r in db.query(AgentVersion).order_by(
            AgentVersion.agent_id, AgentVersion.version_number
        )
    ]
    models = [
        _row_model(r)
        for r in db.query(ModelResource).order_by(
            ModelResource.provider, ModelResource.model_name
        )
    ]
    tools = [
        _row_tool(r)
        for r in db.query(ToolResource).order_by(
            ToolResource.scope, ToolResource.tool_key
        )
    ]
    mcps = [
        _row_mcp(r)
        for r in db.query(MCPServer).order_by(MCPServer.scope, MCPServer.server_key)
    ]
    services = [
        _row_service(r)
        for r in db.query(ExternalService).order_by(
            ExternalService.scope, ExternalService.service_key
        )
    ]
    sources = [
        _row_data(r)
        for r in db.query(DataSource).order_by(
            DataSource.scope, DataSource.data_source_key
        )
    ]
    dependencies = [
        _row_dependency(r)
        for r in db.query(AgentVersionDependency).order_by(
            AgentVersionDependency.agent_version_id
        )
    ]
    total = (
        len(systems)
        + len(agents)
        + len(versions)
        + len(models)
        + len(tools)
        + len(mcps)
        + len(services)
        + len(sources)
        + len(dependencies)
    )
    if total > MAX_RECORDS:
        raise ValueError("REGISTRY_SNAPSHOT_TOO_LARGE")

    for bucket in (
        systems,
        agents,
        versions,
        models,
        tools,
        mcps,
        services,
        sources,
        dependencies,
    ):
        for row in bucket:
            row["digest"] = _field_digest({k: v for k, v in row.items() if k != "digest"})

    from datetime import datetime, timezone

    captured_at = datetime.now(timezone.utc).isoformat()
    projection = {
        "ai_systems": systems,
        "agents": agents,
        "agent_versions": versions,
        "models": models,
        "tools": tools,
        "mcp_servers": mcps,
        "external_services": services,
        "data_sources": sources,
        "dependencies": dependencies,
        "indexes": {
            "system_by_key": {r["system_key"]: r["id"] for r in systems},
            "agent_by_key": {r["agent_key"]: r for r in agents},
            "tool_by_scope_key": {
                f"{r['scope']}:{r['tool_key']}": r["id"] for r in tools
            },
            "mcp_by_scope_key": {
                f"{r['scope']}:{r['server_key']}": r["id"] for r in mcps
            },
            "service_by_scope_key": {
                f"{r['scope']}:{r['service_key']}": r["id"] for r in services
            },
            "data_by_scope_key": {
                f"{r['scope']}:{r['data_source_key']}": r["id"] for r in sources
            },
        },
    }
    digest = canonical_digest_obj(
        {
            "captured_at": captured_at,
            "matching_rule_version": MATCHING_RULE_VERSION,
            "projection": projection,
        }
    )
    db.rollback()  # read-only; release snapshot transaction
    return RegistrySnapshot(
        captured_at=captured_at,
        matching_rule_version=MATCHING_RULE_VERSION,
        digest=digest,
        projection=projection,
        record_count=total,
    )


def resolve_claimed_registry_id(
    snapshot: RegistrySnapshot, entity_type: str, record_id: str
) -> dict[str, Any] | None:
    try:
        rid = uuid.UUID(record_id)
    except ValueError:
        return None
    key = {
        "ai_system": "ai_systems",
        "agent": "agents",
        "agent_version": "agent_versions",
        "model": "models",
        "tool": "tools",
        "mcp_server": "mcp_servers",
        "external_service": "external_services",
        "data_source": "data_sources",
        "dependency": "dependencies",
    }.get(entity_type)
    if key is None:
        return None
    for row in snapshot.projection.get(key, []):
        if row.get("id") == str(rid):
            return row
    return None