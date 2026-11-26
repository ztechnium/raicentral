"""First-run setup for a new instance: status, choices, and creation.

Creation runs in three stages: ``apply_resources``, ``apply_systems`` and then
``first_run_policies.apply_policies``. Each stage is one database transaction and is
safe to retry. The registry, identity and SDK services
commit internally, so a stage runs them on a session whose commits only release
savepoints; nothing is durable until the whole stage succeeds.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from rai_gateway.core import ai_provider
from rai_gateway.core.authorization import is_email_verified_for_mutation
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.identity.errors import IdentityError
from rai_gateway.identity.service import CredentialService, RuntimeIdentityService
from rai_gateway.models.identity import AgentRuntimeIdentity
from rai_gateway.models.policy_engine import ContentRule, ToolRule
from rai_gateway.models.productization import SdkIntegration
from rai_gateway.models.registry import (
    Agent,
    AgentVersion,
    AISystem,
    DataSource,
    ExternalService,
    MCPServer,
    ModelResource,
    ToolResource,
)
from rai_gateway.models.risk_intelligence import RuntimeBudget
from rai_gateway.public_product.onboarding import OnboardingService
from rai_gateway.registry.errors import RegistryError
from rai_gateway.registry.seed_mcp_catalog import (
    MCPSeedCatalogService,
    load_seed_catalog,
)
from rai_gateway.registry.service import RegistryService
from rai_gateway.risk_intelligence.repository import RiskIntelligenceRepository
from rai_gateway.sdk.errors import SdkError
from rai_gateway.sdk.service import SdkService

# Everything first-run creates runs here. Registry approval is not environment-scoped.
ENVIRONMENT = "development"
SETUP_PERMISSIONS = ("sdk:read", "sdk:manage", "registry:write")
POLICY_PERMISSIONS = ("policies:write", "risk:manage")
_SOURCE = "first_run"
# Catalog templates that choosing them turns on. Disabled or deprecated stays so.
_ACTIVATABLE_STATUSES = frozenset({"", "SEEDED", "DISCOVERED", "UNCLASSIFIED"})
_LOCK_KEY = "rai.first_run"
_MODEL_CATALOG_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "onboarding_model_catalog.json"
)
_DEPENDENCY_ORDER = ("mcp_server", "tool", "external_service", "data_source")
_USAGE = {
    "model": "inference",
    "mcp_server": "tools",
    "tool": "action",
    "external_service": "integration",
    "data_source": "retrieval",
}


class FirstRunError(Exception):
    def __init__(
        self, message: str, *, http_status: int = 400, code: str = "INVALID_REQUEST"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code


# --------------------------------------------------------------------- requests


def _stripped(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class ModelChoice(BaseModel):
    provider: Literal["google_ai", "anthropic", "openai"]
    model_name: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/\-]*$"
    )


class SeedServerChoice(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    # None selects every tool of the server.
    tool_keys: list[str] | None = Field(default=None, max_length=200)


class CustomResource(BaseModel):
    ref: str = Field(min_length=1, max_length=64)
    kind: Literal["mcp_server", "tool", "external_service", "data_source"]
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    endpoint: str | None = Field(default=None, max_length=500)
    # Tool names, for an MCP server only.
    tools: list[str] = Field(default_factory=list, max_length=50)

    _name = field_validator("name")(_stripped)


class ResourcesRequest(BaseModel):
    models: list[ModelChoice] = Field(min_length=1, max_length=100)
    seed_servers: list[SeedServerChoice] = Field(default_factory=list, max_length=50)
    custom: list[CustomResource] = Field(default_factory=list, max_length=50)


class DependencyChoice(BaseModel):
    resource_type: Literal["mcp_server", "tool", "external_service", "data_source"]
    resource_id: UUID


class SystemChoice(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    model_ids: list[UUID] = Field(min_length=1, max_length=50)
    dependencies: list[DependencyChoice] = Field(default_factory=list, max_length=300)

    _name = field_validator("name")(_stripped)


class SystemsRequest(BaseModel):
    # Identifies one setup draft, so a retry re-issues secrets instead of duplicating.
    request_id: UUID
    systems: list[SystemChoice] = Field(min_length=1, max_length=20)


# ------------------------------------------------------------------------ names


def agent_name(system_name: str) -> str:
    return f"{system_name} - agent 01"


def version_label(system_name: str) -> str:
    return f"{agent_name(system_name)} - V1.0.0"


def _digest(value: str) -> str:
    return hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:8]


def derive_key(name: str, *, prefix: str) -> str:
    """ASCII key for a display name; a stable hash when the name has no ASCII letters."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48].strip("-")
    return slug or f"{prefix}-{_digest(name)}"


# ------------------------------------------------------------------ transaction


@contextmanager
def atomic_session(db: Session) -> Iterator[Session]:
    """A session whose ``commit()`` calls become durable only if the block succeeds."""
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)
    with engine.connect() as connection:
        transaction = connection.begin()
        # One first-run write at a time, so concurrent retries cannot create twins.
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": _LOCK_KEY}
        )
        session = Session(
            bind=connection,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
            session.commit()
            session.close()
            transaction.commit()
        except BaseException:
            session.close()
            transaction.rollback()
            raise


# ----------------------------------------------------------------------- status


def _exists(db: Session, column: Any) -> bool:
    return db.query(column).limit(1).first() is not None


def setup_inventory(db: Session) -> dict[str, bool]:
    return {
        "ai_systems": _exists(db, AISystem.id),
        "runtime_identities": _exists(db, AgentRuntimeIdentity.id),
        "policies": any(
            _exists(db, model.id) for model in (ContentRule, ToolRule, RuntimeBudget)
        ),
    }


def get_status(db: Session, tenant: TenantContext) -> dict[str, Any]:
    record = OnboardingService(db).get_record()
    inventory = setup_inventory(db)
    is_new = not any(inventory.values())
    if record is not None and record.welcome_completed_at is not None:
        state = "COMPLETED"
    elif record is not None and record.setup_skipped_at is not None:
        state = "SKIPPED"
    elif is_new:
        state = "REQUIRED"
    else:
        state = "NOT_NEEDED"
    missing = [p for p in SETUP_PERMISSIONS if not tenant.has_permission(p)]
    config, problem = ai_provider.try_load_ai_config()
    return {
        "state": state,
        "is_new_organization": is_new,
        "inventory": inventory,
        "can_setup": not missing,
        "missing_permissions": missing,
        "should_redirect": state == "REQUIRED" and not missing,
        "email_verified": is_email_verified_for_mutation(db, tenant.principal),
        "can_add_policies": all(tenant.has_permission(p) for p in POLICY_PERMISSIONS),
        # Content rules are judged by a model; without one they are only stored.
        "content_rules_enforced": _content_evaluator_ready(db),
        "ai": {
            "configured": config is not None,
            "provider": config.provider if config else None,
            "model": config.model if config else None,
            "problem": problem,
        },
    }


def _content_evaluator_ready(db: Session) -> bool:
    config = RiskIntelligenceRepository(db).get_evaluator_config(None)
    return bool(config and config.enabled)


def skip_setup(db: Session) -> None:
    record = OnboardingService(db).get_or_create()
    if record.setup_skipped_at is None:
        record.setup_skipped_at = datetime.now(timezone.utc)
    db.commit()


# ---------------------------------------------------------------------- choices


def get_catalog() -> dict[str, Any]:
    """The models and example resources offered by the wizard. Writes nothing."""
    models = json.loads(_MODEL_CATALOG_PATH.read_text(encoding="utf-8"))
    resources = []
    for server in load_seed_catalog().get("mcps") or []:
        key = str(server["canonical_key"])
        resources.append(
            {
                "key": key,
                "name": server.get("display_name") or key,
                "description": server.get("description"),
                "category": server.get("category"),
                "tools": [
                    {
                        "key": f"{key}.{tool['native_name']}",
                        "name": tool.get("display_name") or tool["native_name"],
                        "risk": tool.get("default_risk") or "LOW",
                    }
                    for tool in server.get("tools") or []
                ],
            }
        )
    return {
        "providers": models["providers"],
        "models": models["models"],
        "resources": resources,
    }


# ------------------------------------------------------------- stage: resources


def apply_resources(
    db: Session, body: ResourcesRequest, *, actor: str
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Register the chosen models and resources, reusing rows that already exist."""
    audit: list[tuple[str, dict[str, Any]]] = []
    with atomic_session(db) as session:
        try:
            registry = RegistryService(session)
            models = _apply_models(session, registry, body.models, audit)
            resources = [
                {"ref": f"seed:{choice.key}", "items": items}
                for choice, items in _apply_seed_servers(
                    session, body.seed_servers, audit
                )
            ]
            for custom in body.custom:
                items = _apply_custom(session, registry, custom, audit)
                resources.append({"ref": custom.ref, "items": items})
        except RegistryError as exc:
            raise FirstRunError(
                exc.message, http_status=exc.status_code, code=exc.error_code
            ) from exc
    return {"models": models, "resources": resources}, audit


def _apply_models(
    session: Session,
    registry: RegistryService,
    choices: list[ModelChoice],
    audit: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for choice in choices:
        wanted = (choice.provider, choice.model_name.lower())
        if wanted in seen:
            continue
        seen.add(wanted)
        # Older seeds spell providers "Anthropic" / "OpenAI": match without case.
        row = (
            session.query(ModelResource)
            .filter(
                func.lower(ModelResource.provider) == wanted[0],
                func.lower(ModelResource.model_name) == wanted[1],
            )
            .order_by(ModelResource.created_at.asc())
            .first()
        )
        if row is None:
            row = registry.create_model_resource(
                provider=choice.provider,
                model_name=choice.model_name,
                hosting_type="EXTERNAL_API",
                metadata={"source": _SOURCE},
            )
            audit.append(("model.created", {"model_id": str(row.id)}))
        result.append(
            {
                "provider": choice.provider,
                "model_name": choice.model_name,
                "id": str(row.id),
            }
        )
    return result


def _item(resource_type: str, row: Any) -> dict[str, str]:
    _, key_field = _CUSTOM_KINDS[resource_type]
    return {
        "resource_type": resource_type,
        "id": str(row.id),
        "key": getattr(row, key_field),
        "name": row.name,
    }


def _apply_seed_servers(
    session: Session,
    choices: list[SeedServerChoice],
    audit: list[tuple[str, dict[str, Any]]],
) -> list[tuple[SeedServerChoice, list[dict[str, str]]]]:
    if not choices:
        return []
    known = {str(s["canonical_key"]) for s in load_seed_catalog().get("mcps") or []}
    unknown = sorted({c.key for c in choices} - known)
    if unknown:
        raise FirstRunError(f"Unknown example resource: {', '.join(unknown)}")

    # The instance's own copy. Built-in PLATFORM templates are never modified.
    def find(key: str) -> MCPServer | None:
        return (
            session.query(MCPServer)
            .filter(MCPServer.server_key == key, MCPServer.scope == "ORGANIZATION")
            .order_by(MCPServer.created_at.asc())
            .first()
        )

    missing = {c.key for c in choices if find(c.key) is None}
    if missing:
        MCPSeedCatalogService(session).seed(scope="ORGANIZATION", mcp_keys=missing)
        audit.append(("mcp_catalog.seeded", {"server_keys": sorted(missing)}))

    result = []
    for choice in choices:
        server = find(choice.key)
        if server is None:
            raise FirstRunError(f"Could not register {choice.key}")
        tools = (
            session.query(ToolResource)
            .filter(ToolResource.mcp_server_id == server.id)
            .order_by(ToolResource.tool_key)
            .all()
        )
        if choice.tool_keys is not None:
            wanted = set(choice.tool_keys)
            tools = [tool for tool in tools if tool.tool_key in wanted]
            absent = sorted(wanted - {tool.tool_key for tool in tools})
            if absent:
                raise FirstRunError(f"Unknown tool: {', '.join(absent)}")
        tools = _activate(server, tools, audit)
        items = [_item("mcp_server", server)]
        items.extend(_item("tool", tool) for tool in tools)
        result.append((choice, items))
    return result


def _activate(
    server: MCPServer,
    tools: list[ToolResource],
    audit: list[tuple[str, dict[str, Any]]],
) -> list[ToolResource]:
    """Make the chosen templates usable: enforcement refuses anything not ACTIVE."""

    def status(row: Any) -> str:
        return (row.status or "").upper()

    if status(server) not in _ACTIVATABLE_STATUSES | {"ACTIVE"}:
        raise FirstRunError(f"{server.name} is {server.status} and cannot be used")
    usable = [t for t in tools if status(t) in _ACTIVATABLE_STATUSES | {"ACTIVE"}]
    pending = [row for row in (server, *usable) if status(row) != "ACTIVE"]
    for row in pending:
        row.status = "ACTIVE"
    if pending:
        audit.append(
            (
                "mcp_catalog.activated",
                {"server_key": server.server_key, "activated": len(pending)},
            )
        )
    return usable


_CUSTOM_KINDS: dict[str, tuple[type, str]] = {
    "mcp_server": (MCPServer, "server_key"),
    "tool": (ToolResource, "tool_key"),
    "external_service": (ExternalService, "service_key"),
    "data_source": (DataSource, "data_source_key"),
}


def _apply_custom(
    session: Session,
    registry: RegistryService,
    custom: CustomResource,
    audit: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, str]]:
    model, key_field = _CUSTOM_KINDS[custom.kind]
    key = derive_key(custom.name, prefix=custom.kind.replace("_", "-"))

    def find(candidate: str) -> Any:
        return (
            session.query(model)
            .filter(
                getattr(model, key_field) == candidate, model.scope == "ORGANIZATION"
            )
            .first()
        )

    row = find(key)
    if row is not None and row.name.casefold() != custom.name.casefold():
        # Two different names, one slug.
        key = f"{key}-{_digest(custom.name)}"
        row = find(key)
    if row is None:
        fields: dict[str, Any] = {
            key_field: key,
            "name": custom.name,
            "description": custom.description,
            "metadata": {"source": _SOURCE},
        }
        if custom.kind in ("mcp_server", "external_service"):
            fields["endpoint_reference"] = custom.endpoint
        create = {
            "mcp_server": registry.create_mcp_server,
            "tool": registry.create_tool_resource,
            "external_service": registry.create_external_service,
            "data_source": registry.create_data_source,
        }[custom.kind]
        row = create(**fields)
        audit.append((f"{custom.kind}.created", {"resource_id": str(row.id)}))
    items = [_item(custom.kind, row)]
    if custom.kind != "mcp_server":
        return items

    for raw in custom.tools:
        name = " ".join(raw.split())[:120]
        if not name:
            continue
        tool_key = f"{key}.{derive_key(name, prefix='tool')}"
        tool = _server_tool(session, row.id, tool_key)
        if tool is not None and tool.name.casefold() != name.casefold():
            # Two different names, one slug.
            tool_key = f"{tool_key}-{_digest(name)}"
            tool = _server_tool(session, row.id, tool_key)
        if tool is None:
            tool = registry.create_tool_resource(
                tool_key=tool_key,
                name=name,
                mcp_server_id=row.id,
                metadata={"source": _SOURCE},
            )
            audit.append(("tool.created", {"resource_id": str(tool.id)}))
        if all(item["id"] != str(tool.id) for item in items):
            items.append(_item("tool", tool))
    return items


def _server_tool(session: Session, server_id: UUID, tool_key: str) -> Any:
    return (
        session.query(ToolResource)
        .filter(
            ToolResource.tool_key == tool_key,
            ToolResource.mcp_server_id == server_id,
        )
        .first()
    )


# --------------------------------------------------------------- stage: systems


def apply_systems(
    db: Session, body: SystemsRequest, *, actor: str
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Create each AI system with an approved agent, version, runtime and credential."""
    names = [system.name.casefold() for system in body.systems]
    if len(set(names)) != len(names):
        raise FirstRunError("Each AI system needs a different name")
    audit: list[tuple[str, dict[str, Any]]] = []
    created: list[dict[str, Any]] = []
    with atomic_session(db) as session:
        try:
            for choice in body.systems:
                existing = (
                    session.query(AISystem)
                    .filter(func.lower(AISystem.name) == choice.name.lower())
                    .first()
                )
                if existing is None:
                    created.append(
                        _create_system(session, choice, body.request_id, actor, audit)
                    )
                else:
                    created.append(
                        _replay_system(session, existing, body.request_id, actor, audit)
                    )
            _record_progress(session, created, body)
        except (RegistryError, IdentityError) as exc:
            raise FirstRunError(
                exc.message, http_status=exc.status_code, code=exc.error_code
            ) from exc
        except SdkError as exc:
            raise FirstRunError(
                exc.message, http_status=exc.http_status, code=exc.code.value
            ) from exc
    return {"environment": ENVIRONMENT, "systems": created}, audit


def _free_system_key(session: Session, name: str) -> str:
    def taken(key: str) -> bool:
        return (
            session.query(AISystem.id).filter(AISystem.system_key == key).first()
            is not None
            or session.query(Agent.id)
            .filter(Agent.agent_key == f"{key}-agent-01")
            .first()
            is not None
        )

    base = derive_key(name, prefix="system")
    for key in (base, f"{base}-{_digest(name)}"):
        if not taken(key):
            return key
    return f"{base}-{uuid.uuid4().hex[:8]}"


def _create_system(
    session: Session,
    choice: SystemChoice,
    request_id: UUID,
    actor: str,
    audit: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    registry = RegistryService(session)
    key = _free_system_key(session, choice.name)
    system = registry.create_ai_system(
        system_key=key,
        name=choice.name,
        description=choice.description,
        created_by=actor,
    )
    audit.append(("ai_system.created", {"ai_system_id": str(system.id)}))
    agent = registry.create_agent(
        system.id,
        agent_key=f"{key}-agent-01",
        name=agent_name(choice.name),
        created_by=actor,
    )
    audit.append(("agent.created", {"agent_id": str(agent.id)}))

    models = [registry.get_model_resource(i) for i in dict.fromkeys(choice.model_ids)]
    version = registry.create_agent_version(
        agent.id,
        created_by=actor,
        version_label=version_label(choice.name),
        change_summary="Created during first-run setup",
        configuration_snapshot={
            "source": _SOURCE,
            "first_run_request_id": str(request_id),
            "allowed_models": [
                {"provider": m.provider, "model_name": m.model_name} for m in models
            ],
        },
    )
    audit.append(("agent_version.created", {"agent_version_id": str(version.id)}))

    # Dependencies can only be added to a draft version: attach before approving.
    for resource_type, resource_id in _ordered_dependencies(registry, choice, models):
        registry.add_version_dependency(
            version.id,
            resource_type=resource_type,
            resource_id=resource_id,
            usage_purpose=_USAGE[resource_type],
        )

    # Runtime eligibility needs all three records APPROVED.
    for status in ("UNDER_REVIEW", "APPROVED"):
        registry.transition_ai_system(system.id, status, updated_by=actor)
        registry.transition_agent(agent.id, status, updated_by=actor)
        registry.transition_agent_version(version.id, status, updated_by=actor)
    for action, field, value in (
        ("ai_system.lifecycle_changed", "ai_system_id", system.id),
        ("agent.lifecycle_changed", "agent_id", agent.id),
        ("agent_version.lifecycle_changed", "agent_version_id", version.id),
    ):
        audit.append(
            (action, {field: str(value), "new_status": "APPROVED", "source": _SOURCE})
        )

    identity = RuntimeIdentityService(session).create_runtime_identity(
        agent_id=agent.id,
        agent_version_id=version.id,
        name=f"{agent_name(choice.name)} runtime",
        environment=ENVIRONMENT,
        created_by=actor,
    )
    audit.append(
        ("runtime_identity.created", {"runtime_identity_id": str(identity.id)})
    )

    integration_key = f"{key}-sdk"
    if (
        session.query(SdkIntegration.id)
        .filter(SdkIntegration.integration_key == integration_key)
        .first()
    ):
        integration_key = f"{integration_key}-{uuid.uuid4().hex[:8]}"
    integration = SdkService(session).create_integration(
        integration_key=integration_key,
        name=f"{choice.name} SDK",
        runtime_identity_id=identity.id,
        environment=ENVIRONMENT,
        created_by=actor,
        metadata={"source": _SOURCE},
    )
    audit.append(
        ("sdk.integration.created", {"integration_id": str(integration["id"])})
    )

    return _result(
        session, system, agent, version, identity, str(integration["id"]), actor, audit
    )


def _ordered_dependencies(
    registry: RegistryService, choice: SystemChoice, models: list[ModelResource]
) -> list[tuple[str, UUID]]:
    chosen: dict[tuple[str, UUID], None] = {
        (d.resource_type, d.resource_id): None for d in choice.dependencies
    }
    # A tool is only valid when its MCP server is attached too.
    for resource_type, resource_id in list(chosen):
        if resource_type == "tool":
            server_id = registry.get_tool_resource(resource_id).mcp_server_id
            if server_id:
                chosen[("mcp_server", server_id)] = None
    ordered = sorted(chosen, key=lambda item: _DEPENDENCY_ORDER.index(item[0]))
    return [("model", model.id) for model in models] + ordered


def _replay_system(
    session: Session,
    system: AISystem,
    request_id: UUID,
    actor: str,
    audit: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """The same draft sent again: its secret may never have arrived, so replace it."""
    rows = (
        session.query(Agent, AgentVersion)
        .join(AgentVersion, AgentVersion.agent_id == Agent.id)
        .filter(Agent.ai_system_id == system.id)
        .all()
    )
    for agent, version in rows:
        snapshot = version.configuration_snapshot or {}
        if snapshot.get("first_run_request_id") != str(request_id):
            continue
        identity = (
            session.query(AgentRuntimeIdentity)
            .filter(AgentRuntimeIdentity.agent_version_id == version.id)
            .order_by(AgentRuntimeIdentity.created_at.asc())
            .first()
        )
        integration = identity and (
            session.query(SdkIntegration)
            .filter(SdkIntegration.runtime_identity_id == identity.id)
            .order_by(SdkIntegration.created_at.asc())
            .first()
        )
        if not identity or not integration:
            break
        credentials = CredentialService(session)
        for credential in credentials.list_credentials(identity.id):
            if credential.status == "ACTIVE" and credential.last_used_at is None:
                credentials.revoke_credential(
                    identity.id, credential.id, revoked_by=actor
                )
                audit.append(
                    ("credential.revoked", {"credential_id": str(credential.id)})
                )
        return _result(
            session,
            system,
            agent,
            version,
            identity,
            str(integration.id),
            actor,
            audit,
        )
    raise FirstRunError(
        f"An AI system named '{system.name}' already exists. Choose another name.",
        http_status=409,
        code="DUPLICATE_NAME",
    )


def _result(
    session: Session,
    system: AISystem,
    agent: Agent,
    version: AgentVersion,
    identity: AgentRuntimeIdentity,
    integration_id: str,
    actor: str,
    audit: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    issued = CredentialService(session).issue_credential(identity.id, created_by=actor)
    audit.append(
        (
            "credential.issued",
            {
                "credential_id": str(issued.credential.id),
                "runtime_identity_id": str(identity.id),
            },
        )
    )
    snapshot = version.configuration_snapshot or {}
    return {
        "ai_system_id": str(system.id),
        "name": system.name,
        "system_key": system.system_key,
        "agent": {"id": str(agent.id), "name": agent.name},
        "version": {"id": str(version.id), "label": version.version_label},
        "runtime_identity": {
            "id": str(identity.id),
            "identity_key": identity.identity_key,
            "name": identity.name,
        },
        "integration_id": integration_id,
        "credential": {
            "id": str(issued.credential.id),
            "credential_key": issued.credential.credential_key,
            "secret": issued.plaintext_secret,
        },
        "models": snapshot.get("allowed_models") or [],
    }


def _record_progress(
    session: Session, created: list[dict[str, Any]], body: SystemsRequest
) -> None:
    """Keep the setup checklist in step with what was just registered."""
    service = OnboardingService(session)
    record = service.get_or_create()
    steps = set(record.completed_steps or [])
    steps.update(
        {"first_ai_system", "agent_version", "choose_integration", "runtime_identity"}
    )
    if any(system.dependencies for system in body.systems):
        steps.add("resources")
    fields: dict[str, Any] = {"completed_steps": sorted(steps)}
    if record.first_ai_system_id is None:
        fields["first_ai_system_id"] = UUID(created[0]["ai_system_id"])
    if record.first_integration_id is None:
        fields["first_integration_id"] = UUID(created[0]["integration_id"])
    service.update(**fields)
