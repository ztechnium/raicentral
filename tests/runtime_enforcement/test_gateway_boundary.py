"""Step 1: shared runtime gateway boundary (GatewayAction / GatewayDecision)."""

from __future__ import annotations

import json

import pytest

from rai_gateway.policy.domain import PolicyEffect
from rai_gateway.policy.service import PolicyCollectionService
from rai_gateway.runtime_enforcement.domain import (
    EnforcementDecisionType,
    ExecutionSource,
    RuntimeActionTarget,
    RuntimeEnforcementReasonCode,
)
from rai_gateway.runtime_enforcement.gateway import (
    OBLIGATION_NOT_EXECUTED,
    OBLIGATION_VIOLATION_NOT_BLOCKED,
    RuntimeGateway,
    resolve_gateway_mode,
)
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayActionType,
    GatewayDecision,
    GatewayEnforcementMode,
)
from rai_gateway.runtime_enforcement.repository import RuntimeEnforcementRepository
from rai_gateway.runtime_enforcement.service import RuntimeEnforcementService


def _action(stack, tool="submit_tool", **kwargs) -> GatewayAction:
    defaults = {
        "action_type": GatewayActionType.MCP_TOOL_CALL.value,
        "integration_type": "mcp",
        "integration_id": stack["mcp"].id,
        "tool_id": stack[tool].id,
        "target": RuntimeActionTarget(type="PurchaseOrder"),
        "parameters": {"supplier": "SUP-104", "amount": 50000},
        "delegation_id": stack["delegation"].id,
    }
    defaults.update(kwargs)
    return GatewayAction(**defaults)


def _gateway(db_session, adapter_registry, mode) -> RuntimeGateway:
    return RuntimeGateway(
        db_session,
        mode=mode,
        enforcement=RuntimeEnforcementService(
            db_session, adapter_registry=adapter_registry
        ),
    )


def _deny_delete(db_session, stack) -> None:
    PolicyCollectionService(db_session).create_tool_rule(
        stack["system"].id,
        title="Deny delete",
        tool_id=stack["delete_tool"].id,
        mcp_server_id=stack["mcp"].id,
        effect=PolicyEffect.DENY.value,
        actor="tester",
    )


def _require_approval_delete(db_session, stack) -> None:
    PolicyCollectionService(db_session).create_tool_rule(
        stack["system"].id,
        title="Approve delete",
        tool_id=stack["delete_tool"].id,
        mcp_server_id=stack["mcp"].id,
        effect=PolicyEffect.REQUIRE_APPROVAL.value,
        required_approver_role="business_approver",
        actor="tester",
    )


# ---- canonical action ---------------------------------------------------


def test_canonical_action_digest_is_deterministic_and_payload_free() -> None:
    a = GatewayAction(
        action_type=GatewayActionType.PROVIDER_CALL.value,
        provider="openai",
        operation="chat.completions",
        model="gpt-4o",
        declared_tools=["b", "a"],
        input_metadata={"body_sha256": "abc", "bytes": 12},
        parameters={"secret_prompt": "do not leak"},
    )
    b = GatewayAction(
        action_type=GatewayActionType.PROVIDER_CALL.value,
        provider="openai",
        operation="chat.completions",
        model="gpt-4o",
        declared_tools=["a", "b"],
        input_metadata={"bytes": 12, "body_sha256": "abc"},
    )
    assert a.digest() == b.digest()
    assert "do not leak" not in json.dumps(a.governance_metadata())


def test_runtime_request_carries_source_mode_and_measurements() -> None:
    action = GatewayAction(
        action_type=GatewayActionType.PROVIDER_CALL.value,
        provider="openai",
        model="gpt-4o",
        estimated_input_tokens=10,
        estimated_output_tokens=5,
        estimated_cost=0.25,
    )
    req = action.to_runtime_request(mode=GatewayEnforcementMode.WARN)
    assert req.execution_source == ExecutionSource.PROVIDER_PROXY.value
    assert req.parameters["token_usage"] == 15
    assert req.parameters["cost"] == 0.25
    assert req.client_context["gateway"]["mode"] == "WARN"
    assert req.client_context["gateway"]["governance_digest"] == action.digest()


def test_default_execution_source_per_action_type() -> None:
    for action_type, expected in (
        (GatewayActionType.PROVIDER_CALL, ExecutionSource.PROVIDER_PROXY),
        (GatewayActionType.MCP_TOOL_CALL, ExecutionSource.MCP_PROXY),
        (GatewayActionType.TOOL_CALL, ExecutionSource.TOOL_PROXY),
    ):
        assert (
            GatewayAction(action_type=action_type.value).resolved_execution_source()
            == expected.value
        )


def test_decision_serialization_is_deterministic() -> None:
    d = GatewayDecision(
        allowed=False,
        decision="DENY",
        reason_codes=["B", "A"],
        obligations=["Z", "Y"],
    )
    assert d.to_json() == d.to_json()
    assert d.to_dict()["reason_codes"] == ["A", "B"]
    assert d.to_dict()["obligations"] == ["Y", "Z"]
    assert d.violation is True


def test_unknown_mode_fails_closed_to_enforce() -> None:
    assert resolve_gateway_mode("bogus") == GatewayEnforcementMode.ENFORCE
    assert resolve_gateway_mode("observe") == GatewayEnforcementMode.OBSERVE


# ---- enforcement modes --------------------------------------------------


def test_allow_executes_and_records_execution_source(
    db_session, runtime_stack, adapter_registry, fake_adapter
) -> None:
    gw = _gateway(db_session, adapter_registry, GatewayEnforcementMode.ENFORCE)
    decision = gw.execute_authorized_gateway_action(
        _action(runtime_stack),
        credential_secret=runtime_stack["credential_secret"],
    )
    assert decision.allowed is True
    assert decision.decision == EnforcementDecisionType.ALLOW.value
    assert decision.execution_attempted is True
    assert decision.execution_source == ExecutionSource.MCP_PROXY.value
    assert fake_adapter.invocation_count == 1

    receipt = RuntimeEnforcementRepository(db_session).get_receipt(decision.receipt_id)
    assert receipt.execution_source == ExecutionSource.MCP_PROXY.value
    assert receipt.client_context["gateway"]["mode"] == "ENFORCE"
    assert decision.authority_snapshot_id == receipt.authority_digest


def test_enforce_denies_and_never_invokes(
    db_session, runtime_stack, adapter_registry, fake_adapter
) -> None:
    _deny_delete(db_session, runtime_stack)
    gw = _gateway(db_session, adapter_registry, GatewayEnforcementMode.ENFORCE)
    decision = gw.execute_authorized_gateway_action(
        _action(runtime_stack, tool="delete_tool"),
        credential_secret=runtime_stack["credential_secret"],
    )
    assert decision.allowed is False
    assert decision.enforced is True
    assert decision.reason_codes == [RuntimeEnforcementReasonCode.POLICY_DENIED.value]
    assert fake_adapter.invocation_count == 0
    receipt = RuntimeEnforcementRepository(db_session).get_receipt(decision.receipt_id)
    assert receipt.execution_source == ExecutionSource.MCP_PROXY.value


@pytest.mark.parametrize(
    "mode", [GatewayEnforcementMode.OBSERVE, GatewayEnforcementMode.WARN]
)
def test_observe_and_warn_allow_but_mark_violation(
    db_session, runtime_stack, adapter_registry, fake_adapter, mode
) -> None:
    _deny_delete(db_session, runtime_stack)
    gw = _gateway(db_session, adapter_registry, mode)
    decision = gw.authorize_gateway_action(
        _action(runtime_stack, tool="delete_tool"),
        credential_secret=runtime_stack["credential_secret"],
    )
    # Evaluated outcome is never softened; only the transport verdict is.
    assert decision.decision == EnforcementDecisionType.DENY.value
    assert decision.allowed is True
    assert decision.enforced is False
    assert decision.mode == mode.value
    assert OBLIGATION_VIOLATION_NOT_BLOCKED in decision.obligations
    assert decision.receipt_id is not None
    receipt = RuntimeEnforcementRepository(db_session).get_receipt(decision.receipt_id)
    assert receipt.enforcement_decision == EnforcementDecisionType.DENY.value
    assert receipt.client_context["gateway"]["mode"] == mode.value
    assert fake_adapter.invocation_count == 0


def test_observe_execute_does_not_run_gateway_executor_on_violation(
    db_session, runtime_stack, adapter_registry, fake_adapter
) -> None:
    _deny_delete(db_session, runtime_stack)
    gw = _gateway(db_session, adapter_registry, GatewayEnforcementMode.OBSERVE)
    decision = gw.execute_authorized_gateway_action(
        _action(runtime_stack, tool="delete_tool"),
        credential_secret=runtime_stack["credential_secret"],
    )
    assert decision.allowed is True
    assert OBLIGATION_NOT_EXECUTED in decision.obligations
    assert fake_adapter.invocation_count == 0


def test_require_approval_flow(
    db_session, runtime_stack, adapter_registry, fake_adapter
) -> None:
    _require_approval_delete(db_session, runtime_stack)
    action = _action(runtime_stack, tool="delete_tool")

    enforce = _gateway(db_session, adapter_registry, GatewayEnforcementMode.ENFORCE)
    blocked = enforce.execute_authorized_gateway_action(
        action, credential_secret=runtime_stack["credential_secret"]
    )
    assert blocked.allowed is False
    assert blocked.decision == EnforcementDecisionType.REQUIRE_APPROVAL.value
    assert blocked.approval_request_id is not None
    assert fake_adapter.invocation_count == 0

    warn = _gateway(db_session, adapter_registry, GatewayEnforcementMode.WARN)
    warned = warn.authorize_gateway_action(
        _action(runtime_stack, tool="delete_tool"),
        credential_secret=runtime_stack["credential_secret"],
    )
    assert warned.allowed is True
    assert warned.decision == EnforcementDecisionType.REQUIRE_APPROVAL.value
    assert "APPROVAL_NOT_OBTAINED" in warned.obligations


@pytest.mark.parametrize(
    "mode",
    [
        GatewayEnforcementMode.OBSERVE,
        GatewayEnforcementMode.WARN,
        GatewayEnforcementMode.ENFORCE,
    ],
)
def test_bad_credential_always_denied(
    db_session, runtime_stack, adapter_registry, fake_adapter, mode
) -> None:
    gw = _gateway(db_session, adapter_registry, mode)
    decision = gw.authorize_gateway_action(
        _action(runtime_stack), credential_secret="not-a-real-credential"
    )
    assert decision.allowed is False
    assert decision.enforced is True
    assert fake_adapter.invocation_count == 0


def test_gateway_error_fails_closed_in_enforce_only(db_session, runtime_stack) -> None:
    class Boom:
        def authorize(self, *a, **k):
            raise RuntimeError("policy snapshot unavailable")

        enforce_and_execute = authorize

    action = _action(runtime_stack)
    enforce = RuntimeGateway(
        db_session, mode=GatewayEnforcementMode.ENFORCE, enforcement=Boom()
    ).authorize_gateway_action(action, credential_secret="x")
    assert enforce.allowed is False
    assert enforce.reason_codes == [
        RuntimeEnforcementReasonCode.GATEWAY_EVALUATION_FAILED.value
    ]

    observe = RuntimeGateway(
        db_session, mode=GatewayEnforcementMode.OBSERVE, enforcement=Boom()
    ).authorize_gateway_action(action, credential_secret="x")
    assert observe.allowed is True
    assert OBLIGATION_VIOLATION_NOT_BLOCKED in observe.obligations


# ---- legacy endpoints still use the shared boundary ---------------------


def test_legacy_execute_source_unchanged_and_authorize_marks_sdk_hook(
    db_session, runtime_stack, adapter_registry
) -> None:
    from rai_gateway.runtime_enforcement.domain import RuntimeActionRequest

    svc = RuntimeEnforcementService(db_session, adapter_registry=adapter_registry)
    legacy = svc.enforce_and_execute(
        RuntimeActionRequest(
            integration_type="mcp",
            integration_id=runtime_stack["mcp"].id,
            tool_id=runtime_stack["submit_tool"].id,
            delegation_id=runtime_stack["delegation"].id,
        ),
        credential_secret=runtime_stack["credential_secret"],
    )
    # Callers that predate execution_source keep RAI_EXECUTED.
    assert legacy.execution_source == ExecutionSource.RAI_EXECUTED.value


def test_rest_endpoints_use_gateway_and_keep_wire_format(
    test_client, db_session, runtime_stack
) -> None:
    body = {
        "integration_type": "mcp",
        "integration_id": str(runtime_stack["mcp"].id),
        "tool_id": str(runtime_stack["submit_tool"].id),
        "delegation_id": str(runtime_stack["delegation"].id),
    }
    headers = {"X-RAI-Agent-Key": runtime_stack["credential_secret"]}

    ok = test_client.post("/v1/runtime/actions/authorize", json=body, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["allowed"] is True  # legacy EnforcementResult shape
    assert "effect" in ok.json()

    missing = test_client.post("/v1/runtime/actions/execute", json=body)
    assert missing.status_code == 401

    bad = test_client.post(
        "/v1/runtime/actions/authorize",
        json=body,
        headers={"X-RAI-Agent-Key": "nope"},
    )
    assert bad.status_code == 403
    assert bad.json()["allowed"] is False
