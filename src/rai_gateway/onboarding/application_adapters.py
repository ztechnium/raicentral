"""Session-owned registry/policy mutators for apply (Step 06). Flush only — no commit."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.models.policy_engine import ContentRule, PolicyCollection, ToolRule
from rai_gateway.models.policy_library import PolicySource, PolicySourceObligation
from rai_gateway.models.registry import (
    Agent,
    AISystem,
    DataSource,
    ExternalService,
    MCPServer,
    ModelResource,
    ToolResource,
)
from rai_gateway.models.risk_intelligence import RuntimeBudget
from rai_gateway.policy.domain import PolicyEffect

_DRAFT_SYSTEM_FIELDS = frozenset(
    {
        "name",
        "description",
        "business_purpose",
        "business_unit",
        "owner_id",
        "technical_owner_id",
        "criticality",
        "risk_tier",
        "data_classification",
    }
)


class ApplyAdapterError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def create_ai_system(db: Session, *, actor: str, claims: dict[str, Any]) -> AISystem:
    key = claims.get("system_key")
    if not isinstance(key, str) or not key.strip():
        raise ApplyAdapterError("INVALID_CLAIMS", "system_key is required")
    existing = db.query(AISystem).filter(AISystem.system_key == key).first()
    if existing:
        raise ApplyAdapterError("DUPLICATE_KEY", f"system_key '{key}' already exists")
    name = claims.get("name") or key
    system = AISystem(
        system_key=key,
        name=name,
        description=claims.get("description"),
        business_purpose=claims.get("business_purpose"),
        business_unit=claims.get("business_unit"),
        owner_id=claims.get("owner_id"),
        technical_owner_id=claims.get("technical_owner_id"),
        criticality=claims.get("criticality"),
        risk_tier=claims.get("risk_tier"),
        data_classification=claims.get("data_classification"),
        status="DRAFT",
        created_by=actor,
        updated_by=actor,
    )
    db.add(system)
    db.flush()
    return system


def create_agent(
    db: Session,
    *,
    actor: str,
    ai_system_id: uuid.UUID,
    claims: dict[str, Any],
) -> Agent:
    key = claims.get("agent_key")
    if not isinstance(key, str) or not key.strip():
        raise ApplyAdapterError("INVALID_CLAIMS", "agent_key is required")
    existing = db.query(Agent).filter(Agent.agent_key == key).first()
    if existing:
        raise ApplyAdapterError("DUPLICATE_KEY", f"agent_key '{key}' already exists")
    agent = Agent(
        agent_key=key,
        ai_system_id=ai_system_id,
        name=claims.get("name") or key,
        description=claims.get("description"),
        business_purpose=claims.get("business_purpose"),
        owner_id=claims.get("owner_id"),
        status="DRAFT",
        created_by=actor,
        updated_by=actor,
    )
    db.add(agent)
    db.flush()
    return agent


def update_ai_system_draft(
    db: Session,
    *,
    actor: str,
    system_id: uuid.UUID,
    fields: dict[str, Any],
) -> AISystem:
    system = db.get(AISystem, system_id)
    if system is None:
        raise ApplyAdapterError("NOT_FOUND", "AI system not found")
    if system.status != "DRAFT":
        raise ApplyAdapterError("IMMUTABLE_TARGET", "Only draft systems may be updated")
    for key, value in fields.items():
        if key not in _DRAFT_SYSTEM_FIELDS:
            continue
        if value is not None:
            setattr(system, key, value)
    system.updated_by = actor
    db.flush()
    return system


def reuse_target(db: Session, *, target_kind: str, target_id: uuid.UUID) -> uuid.UUID:
    """REUSE is a durable no-op that records the existing binding."""
    if target_kind == "ai_system" and db.get(AISystem, target_id) is None:
        raise ApplyAdapterError("NOT_FOUND", "AI system not found")
    if target_kind == "agent" and db.get(Agent, target_id) is None:
        raise ApplyAdapterError("NOT_FOUND", "Agent not found")
    resource = {
        **RESOURCE_MODELS,
        "tool_rule": ToolRule,
        "runtime_budget": RuntimeBudget,
        "content_rule": ContentRule,
    }.get(target_kind)
    if resource is not None and db.get(resource, target_id) is None:
        raise ApplyAdapterError("NOT_FOUND", f"{target_kind} not found")
    return target_id


# Registry resources onboarding can create. Keys are unique per (scope, key).
RESOURCE_MODELS: dict[str, Any] = {
    "model": ModelResource,
    "tool": ToolResource,
    "mcp_server": MCPServer,
    "external_service": ExternalService,
    "data_source": DataSource,
}
_RESOURCE_KEY = {
    "tool": "tool_key",
    "mcp_server": "server_key",
    "external_service": "service_key",
    "data_source": "data_source_key",
}
_SCOPE = "ORGANIZATION"


def create_resource(db: Session, *, kind: str, claims: dict[str, Any]) -> uuid.UUID:
    """Register a model, tool, MCP server, external service or data source."""
    note = {"onboarding": {k: claims[k] for k in ("status", "business_unit") if claims.get(k)}}
    if kind == "model":
        provider = claims.get("provider") or "unknown"
        name = claims.get("model_name")
        if not isinstance(name, str) or not name.strip():
            raise ApplyAdapterError("INVALID_CLAIMS", "model_name is required")
        existing = (
            db.query(ModelResource)
            .filter(
                ModelResource.scope == _SCOPE,
                ModelResource.provider == provider,
                ModelResource.model_name == name,
            )
            .first()
        )
        if existing:
            raise ApplyAdapterError("DUPLICATE_KEY", f"model '{name}' already exists")
        row = ModelResource(
            scope=_SCOPE,
            provider=provider,
            model_name=name,
            status="ACTIVE",
            metadata_=note,
        )
    else:
        field = _RESOURCE_KEY[kind]
        key = claims.get(field)
        if not isinstance(key, str) or not key.strip():
            raise ApplyAdapterError("INVALID_CLAIMS", f"{field} is required")
        model = RESOURCE_MODELS[kind]
        if db.query(model).filter(model.scope == _SCOPE, getattr(model, field) == key).first():
            raise ApplyAdapterError("DUPLICATE_KEY", f"{field} '{key}' already exists")
        values: dict[str, Any] = {
            "scope": _SCOPE,
            field: key,
            "name": claims.get("name") or key,
            "description": claims.get("description"),
            "status": "ACTIVE",
            "metadata_": note,
        }
        if kind == "data_source" and claims.get("owner_name"):
            values["owner"] = claims["owner_name"]
        row = model(**values)
    db.add(row)
    db.flush()
    return row.id


_TOOL_RULE_EFFECTS = frozenset({"DENY", "REQUIRE_APPROVAL"})


def create_tool_rule_disabled(
    db: Session,
    *,
    ai_system_id: uuid.UUID,
    tool_id: uuid.UUID,
    generated: dict[str, Any],
) -> ToolRule:
    tool = db.get(ToolResource, tool_id)
    if tool is None:
        raise ApplyAdapterError("NOT_FOUND", "Tool not found")
    effect = generated.get("effect")
    if effect not in _TOOL_RULE_EFFECTS:
        raise ApplyAdapterError("INVALID_ACTION", "Tool rule needs a DENY or REQUIRE_APPROVAL effect")
    collection = _collection_for_system(db, ai_system_id)
    text = generated.get("text") or ""
    rule = ToolRule(
        policy_collection_id=collection.id,
        title=str(generated.get("title") or "Onboarding tool rule")[:255],
        description=(
            f"{text}\n\nCreated disabled by onboarding. Conditions in the source were not "
            "mapped to tool parameters, so this rule applies to every call of the tool until "
            "conditions are added."
        ).strip(),
        mcp_server_id=tool.mcp_server_id,
        tool_id=tool.id,
        match_mode="ALL",
        effect=effect,
        enabled=False,
        required_approver_role=generated.get("approval_role"),
    )
    db.add(rule)
    db.flush()
    return rule


def create_budget_disabled(
    db: Session,
    *,
    ai_system_id: uuid.UUID,
    generated: dict[str, Any],
) -> RuntimeBudget:
    metric = generated.get("metric")
    limit = generated.get("limit_value")
    seconds = generated.get("window_seconds")
    if (
        metric not in _BUDGET_METRICS
        or not isinstance(limit, (int, float))
        or isinstance(limit, bool)
        or limit <= 0
        or not isinstance(seconds, int)
        or seconds <= 0
    ):
        raise ApplyAdapterError("INVALID_ACTION", "Budget needs a metric, a limit and a fixed window")
    budget = RuntimeBudget(
        ai_system_id=ai_system_id,
        name=str(generated.get("title") or "Onboarding budget")[:255],
        scope_type="AI_SYSTEM",
        metric=metric,
        limit_value=float(limit),
        window_seconds=seconds,
        warning_threshold=0.8,
        effect_on_exceed="DENY",
        enforcement_mode=generated.get("enforcement_mode") or "MANDATORY",
        enabled=False,
    )
    db.add(budget)
    db.flush()
    return budget


_BUDGET_METRICS = frozenset({"ACTION_COUNT", "TOOL_COUNT", "AMOUNT", "TOKEN_USAGE", "COST"})


def create_policy_source_structured(
    db: Session,
    *,
    actor: str,
    generated: dict[str, Any],
) -> PolicySource:
    title = generated.get("title") or "Onboarding policy source"
    source = PolicySource(
        scope="ORGANIZATION",
        title=str(title),
        source_type="MANUAL_TEXT",
        authority=generated.get("authority"),
        owner=generated.get("owner") or actor,
        jurisdiction=generated.get("jurisdiction"),
        policy_type=generated.get("policy_type"),
        version=generated.get("version"),
        status="ACTIVE",
        content_text=generated.get("content_text") or generated.get("text"),
        content_digest=generated.get("content_digest"),
        ingestion_status="READY",
    )
    db.add(source)
    db.flush()
    for ob in generated.get("obligations") or []:
        if not isinstance(ob, dict):
            continue
        db.add(
            PolicySourceObligation(
                policy_source_id=source.id,
                section_ref=ob.get("section_ref"),
                topic=ob.get("topic"),
                text=ob.get("text"),
                status=ob.get("status") or "draft",
                structured=ob.get("structured"),
            )
        )
    db.flush()
    return source


def _collection_for_system(db: Session, ai_system_id: uuid.UUID) -> PolicyCollection:
    existing = (
        db.query(PolicyCollection)
        .filter(PolicyCollection.ai_system_id == ai_system_id)
        .first()
    )
    if existing:
        return existing
    collection = PolicyCollection(
        ai_system_id=ai_system_id,
        name="Default Policy Collection",
        status="ACTIVE",
    )
    db.add(collection)
    db.flush()
    return collection


def create_content_rule_disabled(
    db: Session,
    *,
    ai_system_id: uuid.UUID,
    generated: dict[str, Any],
) -> ContentRule:
    collection = _collection_for_system(db, ai_system_id)
    effect = generated.get("effect") or PolicyEffect.DENY.value
    title = generated.get("title") or "Onboarding content rule"
    content = generated.get("content") or generated.get("text") or title
    rule = ContentRule(
        policy_collection_id=collection.id,
        title=str(title)[:255],
        content=str(content),
        effect=effect,
        enabled=False,
        applies_to=generated.get("applies_to") or "BOTH",
    )
    db.add(rule)
    db.flush()
    return rule
