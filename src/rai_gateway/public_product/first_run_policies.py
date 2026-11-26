"""The third first-run stage: create the reviewed content, tool and budget rules."""

from __future__ import annotations

import hashlib
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from rai_gateway.models.policy_engine import ContentRule, PolicyCollection, ToolRule
from rai_gateway.models.policy_library import PolicySource
from rai_gateway.models.registry import (
    Agent,
    AgentVersion,
    AgentVersionDependency,
    AISystem,
    ToolResource,
)
from rai_gateway.models.risk_intelligence import RuntimeBudget
from rai_gateway.policy.errors import PolicyError
from rai_gateway.policy.library import PolicyLibraryError, PolicyLibraryService
from rai_gateway.policy.service import PolicyCollectionService
from rai_gateway.public_product.first_run import FirstRunError, atomic_session
from rai_gateway.public_product.onboarding import OnboardingService
from rai_gateway.risk_intelligence.service import RiskIntelligenceService

Effect = Literal["DENY", "REQUIRE_APPROVAL", "MONITOR"]
ApproverRole = Literal["business_approver", "rai_officer", "admin"]


def _clean_title(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class ContentSpec(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    effect: Effect
    applies_to: Literal["INPUT", "OUTPUT", "BOTH"] = "BOTH"
    approver_role: ApproverRole | None = None


class ConditionSpec(BaseModel):
    parameter_path: str = Field(min_length=1, max_length=200)
    operator: Literal[
        "eq",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "contains",
        "not_contains",
        "exists",
        "not_exists",
    ]
    value_type: Literal["string", "number", "boolean"] = "string"
    value: bool | float | str | None = None


class ToolSpec(BaseModel):
    tool_id: UUID
    effect: Effect
    approver_role: ApproverRole | None = None
    # All conditions must match. None means every call of the tool.
    conditions: list[ConditionSpec] = Field(default_factory=list, max_length=10)


class BudgetSpec(BaseModel):
    metric: Literal["ACTION_COUNT", "TOKEN_USAGE", "COST"]
    limit_value: float = Field(gt=0, allow_inf_nan=False)
    window_seconds: int = Field(ge=60, le=366 * 86_400)
    effect: Effect = "DENY"


class RuleChoice(BaseModel):
    family: Literal["content", "tool", "budget"]
    title: str = Field(min_length=1, max_length=200)
    ai_system_ids: list[UUID] = Field(min_length=1, max_length=20)
    enabled: bool = True
    content: ContentSpec | None = None
    tool: ToolSpec | None = None
    budget: BudgetSpec | None = None
    document_ref: str | None = Field(default=None, max_length=64)
    quote: str | None = Field(default=None, max_length=500)

    _title = field_validator("title")(_clean_title)

    @model_validator(mode="after")
    def _one_matching_spec(self) -> RuleChoice:
        given = [n for n in ("content", "tool", "budget") if getattr(self, n)]
        if given != [self.family]:
            raise ValueError(f"A {self.family} rule needs only '{self.family}'")
        spec = self.content or self.tool
        if spec and spec.effect == "REQUIRE_APPROVAL" and not spec.approver_role:
            raise ValueError("An approval rule needs an approver role")
        return self


class SourceDocument(BaseModel):
    ref: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200_000)


class PoliciesRequest(BaseModel):
    rules: list[RuleChoice] = Field(min_length=1, max_length=200)
    documents: list[SourceDocument] = Field(default_factory=list, max_length=30)


def _check(session: Session, body: PoliciesRequest) -> dict[UUID, str]:
    """Reject impossible rules before anything is written. Returns system names."""
    wanted = {i for rule in body.rules for i in rule.ai_system_ids}
    names = {
        row.id: row.name
        for row in session.query(AISystem).filter(AISystem.id.in_(wanted))
    }
    if missing := wanted - set(names):
        raise FirstRunError(
            f"AI system not found: {next(iter(missing))}", http_status=404
        )
    seen: set[tuple[str, str, UUID]] = set()
    for rule in body.rules:
        for system_id in dict.fromkeys(rule.ai_system_ids):
            key = (rule.family, rule.title.casefold(), system_id)
            if key in seen:
                raise FirstRunError(
                    f"Two {rule.family} rules for {names[system_id]} are named "
                    f"'{rule.title}'. Rename one."
                )
            seen.add(key)
            if rule.tool and not _uses_tool(session, system_id, rule.tool.tool_id):
                raise FirstRunError(
                    f"'{rule.title}': {names[system_id]} does not use that tool."
                )
    return names


def _uses_tool(session: Session, system_id: UUID, tool_id: UUID) -> bool:
    return (
        session.query(AgentVersionDependency.id)
        .join(AgentVersion, AgentVersion.id == AgentVersionDependency.agent_version_id)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .filter(
            Agent.ai_system_id == system_id,
            AgentVersionDependency.resource_type == "tool",
            AgentVersionDependency.resource_id == tool_id,
        )
        .first()
        is not None
    )


def _existing(session: Session, rule: RuleChoice, system_id: UUID) -> Any:
    if rule.family == "budget":
        return (
            session.query(RuntimeBudget)
            .filter(
                RuntimeBudget.ai_system_id == system_id,
                RuntimeBudget.name == rule.title,
            )
            .first()
        )
    model = ContentRule if rule.family == "content" else ToolRule
    return (
        session.query(model)
        .join(PolicyCollection, model.policy_collection_id == PolicyCollection.id)
        .filter(
            PolicyCollection.ai_system_id == system_id,
            model.title == rule.title,
            model.archived_at.is_(None),
        )
        .first()
    )


def _create(session: Session, rule: RuleChoice, system_id: UUID, actor: str) -> str:
    policies = PolicyCollectionService(session)
    if rule.content:
        spec = rule.content
        row = policies.create_content_rule(
            system_id,
            title=rule.title,
            content=spec.content,
            effect=spec.effect,
            enabled=rule.enabled,
            required_approver_role=spec.approver_role
            if spec.effect == "REQUIRE_APPROVAL"
            else None,
            applies_to=spec.applies_to,
            actor=actor,
        )
        return str(row.id)
    if rule.tool:
        tool_spec = rule.tool
        tool = session.get(ToolResource, tool_spec.tool_id)
        row = policies.create_tool_rule(
            system_id,
            title=rule.title,
            description=f"From the policy: {rule.quote}" if rule.quote else None,
            tool_id=tool_spec.tool_id,
            mcp_server_id=tool.mcp_server_id,
            effect=tool_spec.effect,
            enabled=rule.enabled,
            required_approver_role=tool_spec.approver_role
            if tool_spec.effect == "REQUIRE_APPROVAL"
            else None,
            conditions=[c.model_dump() for c in tool_spec.conditions],
            actor=actor,
        )
        return str(row.id)
    budget = rule.budget
    created = RiskIntelligenceService(session).create_budget(
        ai_system_id=system_id,
        name=rule.title,
        scope_type="AI_SYSTEM",
        metric=budget.metric,
        limit_value=budget.limit_value,
        window_seconds=budget.window_seconds,
        warning_threshold=0.8,
        effect_on_exceed=budget.effect,
        # A tool call carries no cost or tokens. MANDATORY would deny every one of
        # them; BEST_EFFORT enforces wherever the metric is measured.
        enforcement_mode="BEST_EFFORT",
        enabled=rule.enabled,
    )
    return str(created["id"])


def _source_id(
    session: Session,
    document: SourceDocument,
    actor: str,
    audit: list[tuple[str, dict[str, Any]]],
) -> UUID:
    digest = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
    library = PolicyLibraryService(session)
    row = (
        session.query(PolicySource)
        .filter(
            PolicySource.title == document.title.strip(),
            PolicySource.content_digest == digest,
        )
        .first()
    )
    if row is not None:
        return row.id
    created = library.create_source(
        title=document.title,
        source_type="MANUAL_TEXT",
        content_text=document.text,
        actor=actor,
    )
    audit.append(("policy_source.created", {"policy_source_id": str(created["id"])}))
    return UUID(str(created["id"]))


def apply_policies(
    db: Session, body: PoliciesRequest, *, actor: str
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Create every rule for each of its AI systems, in one transaction.

    A rule already present under the same title is reported, not duplicated, so the
    stage is safe to retry.
    """
    audit: list[tuple[str, dict[str, Any]]] = []
    results: list[dict[str, Any]] = []
    documents = {d.ref: d for d in body.documents}
    with atomic_session(db) as session:
        try:
            names = _check(session, body)
            sources: dict[str, UUID] = {}
            for rule in body.rules:
                for system_id in dict.fromkeys(rule.ai_system_ids):
                    row = _existing(session, rule, system_id)
                    if row is not None:
                        rule_id, status = str(row.id), "existing"
                        enabled = bool(row.enabled)
                    else:
                        rule_id = _create(session, rule, system_id, actor)
                        status, enabled = "created", rule.enabled
                        if rule.budget:
                            audit.append(
                                ("risk.budget.created", {"budget_id": rule_id})
                            )
                    document = documents.get(rule.document_ref or "")
                    if status == "created" and document and not rule.budget:
                        if document.ref not in sources:
                            sources[document.ref] = _source_id(
                                session, document, actor, audit
                            )
                        PolicyLibraryService(session).link_rule(
                            policy_source_id=sources[document.ref],
                            rule_type=rule.family.upper(),
                            rule_id=UUID(rule_id),
                        )
                    results.append(
                        {
                            "family": rule.family,
                            "title": rule.title,
                            "rule_id": rule_id,
                            "ai_system_id": str(system_id),
                            "ai_system_name": names[system_id],
                            "enabled": enabled,
                            "status": status,
                        }
                    )
            _record_progress(session)
        except (PolicyError, PolicyLibraryError) as exc:
            raise FirstRunError(
                getattr(exc, "message", str(exc)),
                http_status=int(getattr(exc, "status_code", 400)),
                code=str(getattr(exc, "error_code", "INVALID_REQUEST")),
            ) from exc
    return {"rules": results}, audit


def _record_progress(session: Session) -> None:
    service = OnboardingService(session)
    record = service.get_or_create()
    steps = set(record.completed_steps or []) | {"first_policies"}
    service.update(completed_steps=sorted(steps))
