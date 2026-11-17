"""Gateway FinOps bridge tests (PR-F)."""

from __future__ import annotations

from rai_gateway.provider_gateway.domain import UsageInfo
from rai_gateway.provider_gateway.production.finops import (
    attach_finops_metadata,
    usage_for_settlement,
)
from rai_gateway.runtime_enforcement.domain import EnforcementResult
from rai_gateway.runtime_enforcement.gateway_action import GatewayDecision


def test_usage_for_settlement_marks_provider_response() -> None:
    payload = usage_for_settlement(
        UsageInfo(total_tokens=10, input_tokens=4, output_tokens=6, provider_reported=True)
    )
    assert payload is not None
    assert payload["source"] == "PROVIDER_RESPONSE"


def test_attach_finops_includes_reservation_from_policy_decision() -> None:
    decision = GatewayDecision(
        allowed=True,
        decision="ALLOW",
        mode="ENFORCE",
        enforcement_result=EnforcementResult(
            allowed=True,
            effect="ALLOW",
            reason_code=None,
            human_explanation="ok",
            policy_decision={"finops_reservation_id": "00000000-0000-0000-0000-0000000000f1"},
        ),
    )
    meta = attach_finops_metadata(
        {"provider": "openai"},
        model="gpt-4o",
        request_bytes=400,
        streaming=False,
        usage=UsageInfo(total_tokens=120, provider_reported=True),
        succeeded=True,
        pre_estimate={
            "estimated_tokens": 164,
            "estimated_cost_usd": 0.01,
            "pricing_version": "test",
        },
        decision=decision,
    )
    assert meta["finops"]["reservation_id"] == "00000000-0000-0000-0000-0000000000f1"
    assert meta["usage"]["total_tokens"] == 120