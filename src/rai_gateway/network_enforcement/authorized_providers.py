"""Resolve authorized AI provider families for a runtime identity."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.models.identity import AgentRuntimeIdentity
from rai_gateway.models.registry import AgentVersionDependency, ModelResource

_PROVIDER_ALIASES: dict[str, str] = {
    "openai": "OPENAI",
    "anthropic": "ANTHROPIC",
    "azure_openai": "AZURE_OPENAI",
    "azure-openai": "AZURE_OPENAI",
    "google_ai": "GOOGLE_AI",
    "google-ai": "GOOGLE_AI",
    "gemini": "GOOGLE_AI",
    "vertex": "VERTEX",
    "vertex_ai": "VERTEX",
    "bedrock": "BEDROCK",
    "aws_bedrock": "BEDROCK",
    "ollama": "OLLAMA",
}


def _normalize_provider(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.strip().upper().replace("-", "_")
    if key in {v for v in _PROVIDER_ALIASES.values()}:
        return key
    lowered = raw.strip().lower()
    return _PROVIDER_ALIASES.get(lowered, key if len(key) <= 32 else None)


def authorized_providers_for_runtime(db: Session, runtime_identity_id: UUID) -> frozenset[str]:
    """Providers authorized for the runtime's pinned agent version (MODEL dependencies)."""
    runtime = (
        db.query(AgentRuntimeIdentity)
        .filter(AgentRuntimeIdentity.id == runtime_identity_id)
        .one_or_none()
    )
    if runtime is None:
        return frozenset()

    deps = (
        db.query(AgentVersionDependency)
        .filter(AgentVersionDependency.agent_version_id == runtime.agent_version_id)
        .filter(AgentVersionDependency.resource_type.in_(("MODEL", "model")))
        .all()
    )
    providers: set[str] = set()
    for dep in deps:
        model = (
            db.query(ModelResource)
            .filter(ModelResource.id == dep.resource_id)
            .one_or_none()
        )
        if model is None:
            continue
        normalized = _normalize_provider(model.provider)
        if normalized:
            providers.add(normalized)
    return frozenset(providers)