"""Transport-neutral gateway action and decision contracts.

Every inbound transport (provider proxy, MCP proxy, tool proxy, SDK hook,
future network gateway) adapts its request into a :class:`GatewayAction` and
receives a :class:`GatewayDecision`. Governance logic lives behind
:class:`~rai_gateway.runtime_enforcement.gateway.RuntimeGateway`, never in
transport code.

``GatewayAction`` carries *governance metadata only*. Sensitive payload bodies
(prompts, tool arguments, completions) are deliberately not fields here; use
``input_metadata`` for digests, sizes and classifications.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from rai_gateway.runtime_enforcement.domain import (
    EnforcementDecisionType,
    EnforcementResult,
    ExecutionSource,
    RuntimeActionRequest,
    RuntimeActionTarget,
)


class GatewayEnforcementMode(StrEnum):
    """Rollout posture of the gateway for a given action.

    OBSERVE: evaluate and record, never block.
    WARN:    evaluate, mark violations, allow.
    ENFORCE: evaluate and fail closed.

    Distinct from ``EnforcementMode`` (PROXY_ENFORCED/HOOK_ENFORCED/...), which
    describes *where* enforcement occurs rather than *whether* it blocks.
    """

    OBSERVE = "OBSERVE"
    WARN = "WARN"
    ENFORCE = "ENFORCE"


class GatewayActionType(StrEnum):
    PROVIDER_CALL = "PROVIDER_CALL"
    MCP_TOOL_CALL = "MCP_TOOL_CALL"
    TOOL_CALL = "TOOL_CALL"


# Default execution source per action type when the caller does not set one.
_DEFAULT_SOURCE = {
    GatewayActionType.PROVIDER_CALL: ExecutionSource.PROVIDER_PROXY,
    GatewayActionType.MCP_TOOL_CALL: ExecutionSource.MCP_PROXY,
    GatewayActionType.TOOL_CALL: ExecutionSource.TOOL_PROXY,
}


@dataclass
class GatewayAction:
    """Canonical governance view of one request crossing the gateway."""

    action_type: str
    provider: str | None = None
    operation: str | None = None
    model: str | None = None
    target: RuntimeActionTarget | None = None
    # Governance-relevant, non-sensitive parameters only.
    parameters: dict[str, Any] = field(default_factory=dict)
    declared_tools: list[str] = field(default_factory=list)
    # Digests, sizes, classifications - never the payload body itself.
    input_metadata: dict[str, Any] = field(default_factory=dict)
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    estimated_cost: float | None = None
    environment: str | None = None
    delegation_id: UUID | None = None
    trace_id: str | None = None
    correlation_id: str | None = None
    client_context: dict[str, Any] | None = None
    # Integration addressing (MCP / registered tools).
    integration_type: str | None = None
    integration_id: UUID | None = None
    tool_id: UUID | None = None
    model_id: UUID | None = None
    approval_request_id: UUID | None = None
    idempotency_key: str | None = None
    execution_source: str | None = None

    def resolved_execution_source(self) -> str:
        if self.execution_source:
            return str(self.execution_source)
        try:
            return _DEFAULT_SOURCE[GatewayActionType(self.action_type)].value
        except ValueError:
            return ExecutionSource.RAI_EXECUTED.value

    def governance_metadata(self) -> dict[str, Any]:
        """Stable, payload-free view used for digesting and receipt context."""
        return {
            "action_type": self.action_type,
            "provider": self.provider,
            "operation": self.operation,
            "model": self.model,
            "declared_tools": sorted(self.declared_tools),
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_cost": self.estimated_cost,
            "environment": self.environment,
            "input_metadata": self.input_metadata,
        }

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.governance_metadata(),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    def to_runtime_request(
        self, *, mode: GatewayEnforcementMode
    ) -> RuntimeActionRequest:
        """Project onto the existing runtime-enforcement request."""
        params = dict(self.parameters)
        # Surface the measurements the FinOps/budget checks already understand,
        # without overriding explicit caller values.
        if self.estimated_cost is not None:
            params.setdefault("cost", self.estimated_cost)
        tokens = (self.estimated_input_tokens or 0) + (
            self.estimated_output_tokens or 0
        )
        if tokens:
            params.setdefault("token_usage", tokens)
        if self.provider:
            params.setdefault("provider", self.provider)
        if self.model:
            params.setdefault("model", self.model)

        context = dict(self.client_context or {})
        context["gateway"] = {
            "mode": mode.value,
            "action_type": self.action_type,
            "provider": self.provider,
            "operation": self.operation,
            "model": self.model,
            "declared_tools": sorted(self.declared_tools),
            "input_metadata": self.input_metadata,
            "governance_digest": self.digest(),
        }
        return RuntimeActionRequest(
            integration_type=self.integration_type
            or (
                "mcp"
                if self.action_type == GatewayActionType.MCP_TOOL_CALL.value
                else (self.provider or self.action_type.lower())
            ),
            integration_id=self.integration_id,
            tool_id=self.tool_id,
            model_id=self.model_id,
            target=self.target,
            parameters=params,
            environment=self.environment,
            approval_request_id=self.approval_request_id,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
            idempotency_key=self.idempotency_key,
            delegation_id=self.delegation_id,
            client_context=context,
            execution_source=self.resolved_execution_source(),
        )


@dataclass
class GatewayDecision:
    """Stable result every transport consumes.

    ``allowed`` is what the transport must act on (after the enforcement mode
    is applied). ``decision`` is the *evaluated* outcome and is never softened:
    in OBSERVE/WARN a DENY evaluates to ``decision="DENY"`` with
    ``allowed=True`` and a ``VIOLATION_NOT_BLOCKED`` obligation.
    """

    allowed: bool
    decision: str
    reason_codes: list[str] = field(default_factory=list)
    approval_request_id: UUID | None = None
    authority_snapshot_id: str | None = None
    receipt_id: UUID | None = None
    obligations: list[str] = field(default_factory=list)
    policy_version: str | None = None
    mode: str = GatewayEnforcementMode.ENFORCE.value
    enforced: bool = True
    execution_source: str | None = None
    execution_attempted: bool = False
    execution_status: str | None = None
    downstream_reference: str | None = None
    human_explanation: str = ""
    correlation_id: str | None = None
    trace_id: str | None = None
    # Underlying service result, for transports that must keep a legacy wire
    # format. Not part of the stable contract or its serialization.
    enforcement_result: EnforcementResult | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def violation(self) -> bool:
        return self.decision in {
            EnforcementDecisionType.DENY.value,
            EnforcementDecisionType.REQUIRE_APPROVAL.value,
        }

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization (sorted lists, stringified ids)."""
        return {
            "allowed": self.allowed,
            "decision": self.decision,
            "reason_codes": sorted(self.reason_codes),
            "approval_request_id": str(self.approval_request_id)
            if self.approval_request_id
            else None,
            "authority_snapshot_id": self.authority_snapshot_id,
            "receipt_id": str(self.receipt_id) if self.receipt_id else None,
            "obligations": sorted(self.obligations),
            "policy_version": self.policy_version,
            "mode": self.mode,
            "enforced": self.enforced,
            "execution_source": self.execution_source,
            "execution_attempted": self.execution_attempted,
            "execution_status": self.execution_status,
            "downstream_reference": self.downstream_reference,
            "human_explanation": self.human_explanation,
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
