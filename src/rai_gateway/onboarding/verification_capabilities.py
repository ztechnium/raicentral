"""Supported onboarding verification scenarios and claim registry."""

from __future__ import annotations

from typing import Any


ONBOARDING_VERIFICATION_PURPOSE = "ONBOARDING_VERIFICATION"
RUNTIME_RECEIPT_RUNNER = "RUNTIME_RECEIPT"
MAX_SCENARIOS_PER_ATTEMPT = 12
MAX_AUTH_REQUESTS_PER_ATTEMPT = 30

_BLOCKED_ADAPTER_CODES = frozenset(
    {
        "MCP_SELECTOR_THROUGH_SDK",
        "APPROVAL_RESUME",
        "STREAMING_PREVENTION",
        "DESTRUCTIVE_MONITOR",
    }
)


def scenario_templates_for_controls(controls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic scenario templates from selected controls."""
    scenarios: list[dict[str, Any]] = []
    for control in controls:
        ctype = control.get("control_type")
        cid = control.get("control_id")
        if not ctype or not cid:
            continue
        key_base = f"{ctype.lower()}:{cid}"
        if ctype in ("TOOL_RULE", "CONTENT_RULE"):
            scenarios.append(
                {
                    "case_key": f"{key_base}:deny_match",
                    "control_type": ctype,
                    "control_id": cid,
                    "assertion_kind": "policy_decision",
                    "claim": "SERVER_DECISION",
                    "adapter": "SDK_AUTHORIZE",
                    "synthetic_action": {
                        "tool": control.get("declared_tool") or "synthetic.tool",
                        "parameters": control.get("fixture_parameters")
                        or {"amount": 9999},
                    },
                    "expected_decision": control.get("expected_decision") or "DENY",
                    "evidence_class": "SERVER_DECISION",
                }
            )
            scenarios.append(
                {
                    "case_key": f"{key_base}:non_match",
                    "control_type": ctype,
                    "control_id": cid,
                    "assertion_kind": "policy_non_match",
                    "claim": "SERVER_DECISION",
                    "adapter": "SDK_AUTHORIZE",
                    "synthetic_action": {
                        "tool": "synthetic.benign",
                        "parameters": {"amount": 1},
                    },
                    "expected_decision": "ALLOW",
                    "evidence_class": "SERVER_DECISION",
                }
            )
        elif ctype == "RUNTIME_BUDGET":
            scenarios.append(
                {
                    "case_key": f"{key_base}:threshold",
                    "control_type": ctype,
                    "control_id": cid,
                    "assertion_kind": "budget_measurement",
                    "claim": "SERVER_DECISION",
                    "adapter": "SDK_AUTHORIZE",
                    "synthetic_action": {
                        "tool": "synthetic.budget_probe",
                        "amount": control.get("fixture_amount") or 1,
                    },
                    "expected_decision": control.get("expected_decision") or "ALLOW",
                    "evidence_class": "SERVER_DECISION",
                }
            )
        elif ctype == "RISK_MONITOR":
            scenarios.append(
                {
                    "case_key": f"{key_base}:blocked",
                    "control_type": ctype,
                    "control_id": cid,
                    "assertion_kind": "unsupported",
                    "claim": "DIAGNOSTIC",
                    "adapter": "UNAVAILABLE",
                    "blocked_code": "DESTRUCTIVE_MONITOR",
                    "evidence_class": "DIAGNOSTIC",
                    "verdict_default": "BLOCKED",
                }
            )
    return scenarios[:MAX_SCENARIOS_PER_ATTEMPT]


def adapter_status(scenario: dict[str, Any]) -> str:
    code = scenario.get("blocked_code")
    if code in _BLOCKED_ADAPTER_CODES:
        return "BLOCKED"
    if scenario.get("adapter") == "UNAVAILABLE":
        return "BLOCKED"
    if scenario.get("assertion_kind") == "unsupported":
        return "BLOCKED"
    return "READY"