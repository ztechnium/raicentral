"""Tools the RAI assistant may use: the same management API surface as the MCP server.

Every tool is one management-API call made with the *caller's own* bearer token, so
existing authorization applies exactly as it would for that user. ``WRITE`` tools are never
run by the model loop; they are proposed to the user and only run after confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PARAM_RE = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    method: str
    path: str
    # Query parameters the model may pass for GET tools.
    query: tuple[str, ...] = ()

    @property
    def is_write(self) -> bool:
        return self.method != "GET"

    @property
    def path_params(self) -> tuple[str, ...]:
        return tuple(_PARAM_RE.findall(self.path))

    @property
    def has_body(self) -> bool:
        return self.method in ("POST", "PATCH", "PUT")


_LIST = ("q", "status", "ai_system_id", "agent_id", "limit", "offset", "severity")
_TIME = ("from_time", "to_time")
# FinOps/report endpoints default to a recent window when the period is omitted.
_FINOPS = (
    "period_start", "period_end", "ai_system_id", "agent_id", "agent_version_id",
    "provider", "model", "environment", "cost_state", "currency",
)
_REPORT = ("ai_system_id", "period_start", "period_end")


def _t(name: str, description: str, method: str, path: str, query: tuple[str, ...] = ()) -> Tool:
    return Tool(name, description, method, path, query)


TOOLS: tuple[Tool, ...] = (
    # Registry
    _t("ai_systems_list", "List AI systems", "GET", "/v1/ai-systems", _LIST),
    _t("ai_systems_get", "Get one AI system", "GET", "/v1/ai-systems/{id}"),
    _t("ai_systems_create", "Create an AI system", "POST", "/v1/ai-systems"),
    _t("ai_systems_update", "Update an AI system", "PATCH", "/v1/ai-systems/{id}"),
    _t("agents_list", "List agents of an AI system", "GET", "/v1/ai-systems/{id}/agents"),
    _t("agents_create", "Create an agent under an AI system", "POST", "/v1/ai-systems/{id}/agents"),
    _t("agents_get", "Get an agent", "GET", "/v1/agents/{id}"),
    _t("agents_update", "Update an agent", "PATCH", "/v1/agents/{id}"),
    _t("agent_versions_list", "List versions of an agent", "GET", "/v1/agents/{id}/versions"),
    _t("agent_versions_create", "Create an agent version", "POST", "/v1/agents/{id}/versions"),
    _t("agent_versions_get", "Get an agent version", "GET", "/v1/agent-versions/{id}"),
    _t("agent_versions_update", "Update an agent version", "PATCH", "/v1/agent-versions/{id}"),
    # Runtime identity and delegation
    _t("runtime_identities_list", "List runtime identities", "GET", "/v1/runtime-identities", _LIST),
    _t("runtime_identities_get", "Get a runtime identity", "GET", "/v1/runtime-identities/{id}"),
    _t("runtime_identities_create", "Create a runtime identity", "POST", "/v1/runtime-identities"),
    _t("runtime_identities_update", "Update a runtime identity", "PATCH", "/v1/runtime-identities/{id}"),
    _t("credentials_list", "List credentials of a runtime identity", "GET", "/v1/runtime-identities/{id}/credentials"),
    _t("credentials_create", "Issue a runtime credential", "POST", "/v1/runtime-identities/{id}/credentials"),
    _t("delegations_list", "List delegations", "GET", "/v1/delegations", _LIST),
    _t("delegations_get", "Get a delegation", "GET", "/v1/delegations/{id}"),
    _t("delegations_create", "Create a delegation", "POST", "/v1/delegations"),
    # Catalog, rules and policy
    _t("resource_catalog_list", "List resource catalog entries (tools, models, MCP servers, data sources)", "GET", "/v1/resource-catalog", (*_LIST, "resource_type")),
    _t("rai_rules_list", "List RAI rules (unified policy rules)", "GET", "/v1/rai-rules", _LIST),
    _t("policy_library_list", "List policy library documents", "GET", "/v1/policy-library", _LIST),
    _t("policy_library_create", "Create a policy library document", "POST", "/v1/policy-library"),
    _t("content_rules_get", "Get a content rule", "GET", "/v1/policies/content-rules/{id}"),
    _t("content_rules_create", "Create a content rule for an AI system (id = AI system id)", "POST", "/v1/ai-systems/{id}/policies/content-rules"),
    _t("content_rules_update", "Update a content rule", "PATCH", "/v1/policies/content-rules/{id}"),
    _t("tool_rules_get", "Get a tool rule", "GET", "/v1/policies/tool-rules/{id}"),
    _t("tool_rules_create", "Create a tool rule for an AI system (id = AI system id)", "POST", "/v1/ai-systems/{id}/policies/tool-rules"),
    _t("tool_rules_update", "Update a tool rule", "PATCH", "/v1/policies/tool-rules/{id}"),
    _t("budget_rules_list", "List risk budgets (budget rules)", "GET", "/v1/risk/budgets", _LIST),
    _t("budget_rules_create", "Create a risk budget", "POST", "/v1/risk/budgets"),
    # Evidence, audit and risk
    _t("trajectories_list", "List trajectories", "GET", "/v1/trajectories", (*_LIST, *_TIME, "trajectory_id")),
    _t("trajectories_get", "Get a trajectory", "GET", "/v1/trajectories/{id}"),
    _t("audit_search", "Search the runtime audit log", "GET", "/v1/runtime-audit/search", ("q", "limit", "offset", "decision", "tool_name", "runtime_identity_id", *_TIME)),
    _t("risk_overview", "Risk overview snapshot", "GET", "/v1/risk/overview"),
    _t("findings_list", "List risk findings", "GET", "/v1/risk/findings", _LIST),
    _t("findings_get", "Get a risk finding", "GET", "/v1/risk/findings/{id}"),
    _t("monitors_list", "List risk monitors", "GET", "/v1/risk/monitors"),
    _t("monitor_rules_list", "List monitor rules", "GET", "/v1/risk/monitor-rules"),
    _t("signals_list", "List risk signals", "GET", "/v1/risk/signals", _LIST),
    # FinOps and reports: tokens, calls and cost (USD). Same data as the Reports and FinOps pages.
    _t(
        "finops_dashboard",
        "Total cost, tokens and calls for a period, split billed/metered/estimated",
        "GET",
        "/v1/finops/dashboard",
        _FINOPS,
    ),
    _t(
        "finops_breakdown",
        "Cost, tokens, calls per dimension (ai_system, agent, model, provider, environment, ...). "
        "Use dimension=ai_system for per-AI-system usage; sort=cost|tokens|calls|trajectories",
        "GET",
        "/v1/finops/breakdown",
        (*_FINOPS, "dimension", "limit", "offset", "sort", "order", "search"),
    ),
    _t("finops_timeseries", "Cost and tokens over time", "GET", "/v1/finops/timeseries", _FINOPS),
    _t("finops_budgets", "Current-window budget health, headroom and projection", "GET", "/v1/finops/budgets", ("ai_system_id",)),
    _t(
        "finops_trajectories",
        "Top trajectories by known cost and tokens",
        "GET",
        "/v1/finops/trajectories",
        (*_FINOPS, "limit", "offset"),
    ),
    _t("trajectory_economics", "Cost and tokens of one trajectory", "GET", "/v1/trajectories/{id}/economics"),
    _t("reports_summary", "Executive summary report", "GET", "/v1/reports/summary", _REPORT),
    _t("reports_risk", "Risk report", "GET", "/v1/reports/risk", _REPORT),
    _t("reports_policies", "Policy report", "GET", "/v1/reports/policies", _REPORT),
    _t("reports_budgets", "Budget report", "GET", "/v1/reports/budgets", _REPORT),
    _t("reports_runtime", "Runtime report", "GET", "/v1/reports/runtime", _REPORT),
)

BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}
