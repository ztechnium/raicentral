"""MCP tools exposing RAI governance and operations APIs."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from rai_gateway.mcp.api_client import dumps_result, management_api_request

WriteBody = dict[str, Any] | None


class ListQuery(BaseModel):
    """Optional list filters — pass through as query parameters to the management API."""

    q: str | None = Field(None, description="Free-text search where supported")
    status: str | None = None
    ai_system_id: str | None = None
    agent_id: str | None = None
    limit: int | None = Field(None, description="Maximum rows to return")
    offset: int | None = None
    from_time: str | None = Field(None, description="ISO-8601 start (trajectories, audit)")
    to_time: str | None = Field(None, description="ISO-8601 end (trajectories, audit)")
    severity: str | None = None
    resource_type: str | None = Field(None, description="Resource catalog filter")
    trajectory_id: str | None = None


class IdPath(BaseModel):
    id: str = Field(..., description="Resource UUID")


class CreateBody(BaseModel):
    body: dict[str, Any] = Field(..., description="Create payload (same shape as REST API)")


class UpdateBody(BaseModel):
    id: str
    body: dict[str, Any] = Field(..., description="Patch payload")


class VersionCreate(BaseModel):
    agent_id: str
    body: dict[str, Any]


class SystemAgentCreate(BaseModel):
    system_id: str
    body: dict[str, Any]


class AuditSearch(BaseModel):
    q: str | None = None
    limit: int | None = 50
    offset: int | None = None
    decision: str | None = None
    tool_name: str | None = None
    runtime_identity_id: str | None = None
    from_time: str | None = None
    to_time: str | None = None


def _query_dict(model: BaseModel) -> dict[str, Any]:
    return {k: v for k, v in model.model_dump().items() if v is not None}


def register_tools(server: MCPServer) -> None:
    """Register management tools on the MCP server."""

    # Registry — AI systems
    @server.tool(description="List AI systems with optional filters")
    async def ai_systems_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(await management_api_request("GET", "/v1/ai-systems", query=q))

    @server.tool(description="Get one AI system by id")
    async def ai_systems_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/ai-systems/{path.id}")
        )

    @server.tool(description="Create an AI system")
    async def ai_systems_create(payload: CreateBody) -> str:
        return dumps_result(
            await management_api_request("POST", "/v1/ai-systems", body=payload.body)
        )

    @server.tool(description="Update an AI system")
    async def ai_systems_update(payload: UpdateBody) -> str:
        return dumps_result(
            await management_api_request(
                "PATCH", f"/v1/ai-systems/{payload.id}", body=payload.body
            )
        )

    @server.tool(description="List agents for an AI system")
    async def agents_list(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/ai-systems/{path.id}/agents")
        )

    @server.tool(description="Create an agent under an AI system")
    async def agents_create(payload: SystemAgentCreate) -> str:
        return dumps_result(
            await management_api_request(
                "POST",
                f"/v1/ai-systems/{payload.system_id}/agents",
                body=payload.body,
            )
        )

    @server.tool(description="Get agent by id")
    async def agents_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/agents/{path.id}")
        )

    @server.tool(description="Update agent metadata")
    async def agents_update(payload: UpdateBody) -> str:
        return dumps_result(
            await management_api_request(
                "PATCH", f"/v1/agents/{payload.id}", body=payload.body
            )
        )

    @server.tool(description="List versions for an agent")
    async def agent_versions_list(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/agents/{path.id}/versions")
        )

    @server.tool(description="Create a new agent version")
    async def agent_versions_create(payload: VersionCreate) -> str:
        return dumps_result(
            await management_api_request(
                "POST",
                f"/v1/agents/{payload.agent_id}/versions",
                body=payload.body,
            )
        )

    @server.tool(description="Get agent version by id")
    async def agent_versions_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/agent-versions/{path.id}")
        )

    @server.tool(description="Patch agent version")
    async def agent_versions_update(payload: UpdateBody) -> str:
        return dumps_result(
            await management_api_request(
                "PATCH", f"/v1/agent-versions/{payload.id}", body=payload.body
            )
        )

    # Runtime identity
    @server.tool(description="List runtime identities")
    async def runtime_identities_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/runtime-identities", query=q)
        )

    @server.tool(description="Create runtime identity")
    async def runtime_identities_create(payload: CreateBody) -> str:
        return dumps_result(
            await management_api_request(
                "POST", "/v1/runtime-identities", body=payload.body
            )
        )

    @server.tool(description="Get runtime identity")
    async def runtime_identities_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/runtime-identities/{path.id}")
        )

    @server.tool(description="Update runtime identity")
    async def runtime_identities_update(payload: UpdateBody) -> str:
        return dumps_result(
            await management_api_request(
                "PATCH", f"/v1/runtime-identities/{payload.id}", body=payload.body
            )
        )

    @server.tool(description="List credentials for a runtime identity")
    async def credentials_list(path: IdPath) -> str:
        return dumps_result(
            await management_api_request(
                "GET", f"/v1/runtime-identities/{path.id}/credentials"
            )
        )

    @server.tool(description="Issue a new runtime credential")
    async def credentials_create(payload: UpdateBody) -> str:
        return dumps_result(
            await management_api_request(
                "POST",
                f"/v1/runtime-identities/{payload.id}/credentials",
                body=payload.body,
            )
        )

    @server.tool(description="List delegations")
    async def delegations_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/delegations", query=q)
        )

    @server.tool(description="Create delegation")
    async def delegations_create(payload: CreateBody) -> str:
        return dumps_result(
            await management_api_request("POST", "/v1/delegations", body=payload.body)
        )

    @server.tool(description="Get delegation")
    async def delegations_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/delegations/{path.id}")
        )

    # Resource catalog
    @server.tool(description="List resource catalog entries with filters")
    async def resource_catalog_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/resource-catalog", query=q)
        )

    # Policy / RAI rules
    @server.tool(description="List RAI rules (unified policy rules)")
    async def rai_rules_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/rai-rules", query=q)
        )

    @server.tool(description="List policy library sources")
    async def policy_library_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/policy-library", query=q)
        )

    @server.tool(description="Create a policy library document")
    async def policy_library_create(payload: CreateBody) -> str:
        return dumps_result(
            await management_api_request(
                "POST", "/v1/policy-library", body=payload.body
            )
        )

    @server.tool(description="Create content rule for an AI system")
    async def content_rules_create(payload: SystemAgentCreate) -> str:
        return dumps_result(
            await management_api_request(
                "POST",
                f"/v1/ai-systems/{payload.system_id}/policies/content-rules",
                body=payload.body,
            )
        )

    @server.tool(description="Get content rule by id")
    async def content_rules_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request(
                "GET", f"/v1/policies/content-rules/{path.id}"
            )
        )

    @server.tool(description="Update content rule")
    async def content_rules_update(payload: UpdateBody) -> str:
        return dumps_result(
            await management_api_request(
                "PATCH",
                f"/v1/policies/content-rules/{payload.id}",
                body=payload.body,
            )
        )

    @server.tool(description="Create tool rule for an AI system")
    async def tool_rules_create(payload: SystemAgentCreate) -> str:
        return dumps_result(
            await management_api_request(
                "POST",
                f"/v1/ai-systems/{payload.system_id}/policies/tool-rules",
                body=payload.body,
            )
        )

    @server.tool(description="Get tool rule by id")
    async def tool_rules_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/policies/tool-rules/{path.id}")
        )

    @server.tool(description="Update tool rule")
    async def tool_rules_update(payload: UpdateBody) -> str:
        return dumps_result(
            await management_api_request(
                "PATCH",
                f"/v1/policies/tool-rules/{payload.id}",
                body=payload.body,
            )
        )

    @server.tool(description="List risk budgets (budget rules)")
    async def budget_rules_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/risk/budgets", query=q)
        )

    @server.tool(description="Create risk budget rule")
    async def budget_rules_create(payload: CreateBody) -> str:
        return dumps_result(
            await management_api_request("POST", "/v1/risk/budgets", body=payload.body)
        )

    # Evidence / trajectories / audit
    @server.tool(description="List trajectories with filters")
    async def trajectories_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/trajectories", query=q)
        )

    @server.tool(description="Get trajectory detail")
    async def trajectories_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/trajectories/{path.id}")
        )

    @server.tool(description="Search runtime audit explorer")
    async def audit_explorer_search(filters: AuditSearch) -> str:
        q = _query_dict(filters)
        return dumps_result(
            await management_api_request("GET", "/v1/runtime-audit/search", query=q)
        )

    # Risk — findings & monitoring
    @server.tool(description="Risk overview snapshot")
    async def risk_overview_get() -> str:
        return dumps_result(await management_api_request("GET", "/v1/risk/overview"))

    @server.tool(description="List risk findings with filters")
    async def findings_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/risk/findings", query=q)
        )

    @server.tool(description="Get risk finding by id")
    async def findings_get(path: IdPath) -> str:
        return dumps_result(
            await management_api_request("GET", f"/v1/risk/findings/{path.id}")
        )

    @server.tool(description="List risk monitors (monitoring state)")
    async def monitoring_monitors_list() -> str:
        return dumps_result(
            await management_api_request("GET", "/v1/risk/monitors")
        )

    @server.tool(description="List monitor rules")
    async def monitoring_rules_list() -> str:
        return dumps_result(
            await management_api_request("GET", "/v1/risk/monitor-rules")
        )

    @server.tool(description="List risk signals")
    async def monitoring_signals_list(filters: ListQuery | None = None) -> str:
        q = _query_dict(filters or ListQuery())
        return dumps_result(
            await management_api_request("GET", "/v1/risk/signals", query=q)
        )