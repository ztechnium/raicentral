"""Compile governance intents into domain-shaped proposal payloads (Step 05)."""

from __future__ import annotations

import re
from typing import Any

from rai_gateway.onboarding.governance_schemas import RUNTIME_VERIFICATION

_EGP_REFUND = re.compile(
    r"refunds?\s+above\s+EGP\s+([\d,]+)\s+require\s+Finance\s+approval",
    re.IGNORECASE,
)
_CALENDAR_BUDGET = re.compile(
    r"USD\s+([\d,]+)\s+per\s+calendar\s+month", re.IGNORECASE
)
_STREAM_REDACT = re.compile(
    r"redact\s+personal\s+data\s+before\s+.*streamed\s+output", re.IGNORECASE
)
_SLIDING_RATE = re.compile(
    r"block\s+the\s+eleventh\s+call.*sliding\s+ten\s+minutes", re.IGNORECASE
)
_TWO_SYSTEM = re.compile(r"systems?\s*=\s*([^;]+)", re.IGNORECASE)


def _base_capability(**overrides: Any) -> dict[str, Any]:
    base = {
        "runtime_verification": RUNTIME_VERIFICATION,
        "enabled": False,
        "interpretation": "SOURCE_STATED",
        "binding": "unbound",
        "representation": "REPRESENTABLE",
        "execution_support": "UNKNOWN",
        "capability_gaps": [],
        "prerequisites": [],
    }
    base.update(overrides)
    return base


def compile_clause(clause: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one normalized requirement into zero or more typed proposals."""
    text = clause.get("text") or ""
    modality = clause.get("modality") or "MUST"
    proposals: list[dict[str, Any]] = []

    m = _EGP_REFUND.search(text)
    if m:
        amount = m.group(1).replace(",", "")
        proposals.append(
            {
                "proposal_family": "obligation",
                "generated": {
                    **_base_capability(
                        binding="unbound",
                        representation="APPLICATION_SERVICE_GAP",
                        execution_support="UNKNOWN",
                        capability_gaps=[
                            {
                                "code": "PARAMETER_PATH_UNKNOWN",
                                "message": "Refund amount API path not stated in source",
                            },
                            {
                                "code": "UNBOUND_SYSTEM",
                                "message": "Tool/system scope requires clarification",
                            },
                        ],
                    ),
                    "modality": modality,
                    "topic": "refund_approval",
                    "constraint_action": "REQUIRE_APPROVAL",
                    "text": text,
                    "amount_egp": amount,
                    "approval_role": "Finance",
                },
            }
        )
        systems = clause.get("systems") or []
        for sys_key in systems or ["unbound"]:
            proposals.append(
                {
                    "proposal_family": "tool_rule",
                    "generated": {
                        **_base_capability(
                            binding="bound" if sys_key != "unbound" else "unbound",
                            execution_support="UNKNOWN",
                            capability_gaps=[
                                {
                                    "code": "PARAMETER_PATH_UNKNOWN",
                                    "message": "No authoritative refund.amount path",
                                },
                            ],
                        ),
                        "effect": "REQUIRE_APPROVAL",
                        "title": f"Refunds above EGP {amount} require Finance approval",
                        "system_key": sys_key if sys_key != "unbound" else None,
                        "tool_selector": "refund",
                        "conditions": [],
                        "conditions_label": "Unconditional",
                        "approval_role": "Finance",
                    },
                }
            )
        return proposals

    m = _CALENDAR_BUDGET.search(text)
    if m:
        limit = m.group(1).replace(",", "")
        proposals.append(
            {
                "proposal_family": "runtime_budget",
                "generated": {
                    **_base_capability(
                        binding="unbound",
                        representation="INCOMPLETE",
                        execution_support="UNSUPPORTED",
                        capability_gaps=[
                            {
                                "code": "CALENDAR_WINDOW_UNSUPPORTED",
                                "message": "Calendar month cannot map to fixed UTC window",
                            },
                            {
                                "code": "UNIT_OR_CURRENCY_UNRESOLVED",
                                "message": "Currency preserved; runtime has no currency field",
                            },
                        ],
                    ),
                    "metric": "COST",
                    "limit_value": float(limit),
                    "currency": "USD",
                    "window_intent": "calendar_month",
                    "window_seconds": None,
                    "warning_threshold": None,
                    "effect_on_exceed": "DENY",
                    "enforcement_mode": "MANDATORY",
                },
            }
        )
        return proposals

    if _STREAM_REDACT.search(text):
        proposals.append(
            {
                "proposal_family": "content_rule",
                "generated": {
                    **_base_capability(
                        execution_support="UNSUPPORTED",
                        capability_gaps=[
                            {
                                "code": "STREAMING_BLOCK_UNSUPPORTED",
                                "message": "Pre-delivery block on streamed output not on inspected path",
                            },
                            {
                                "code": "EVALUATOR_NOT_INSPECTED",
                                "message": "Mask/redact remediation not established",
                            },
                        ],
                    ),
                    "effect": "DENY",
                    "title": "Redact personal data before streamed output delivery",
                    "intervention_point": "OUTPUT_STREAM",
                    "remediation": "MASK",
                },
            }
        )
        return proposals

    if _SLIDING_RATE.search(text):
        proposals.append(
            {
                "proposal_family": "risk_monitor_rule",
                "generated": {
                    **_base_capability(
                        representation="SCHEMA_GAP",
                        execution_support="UNSUPPORTED",
                        capability_gaps=[
                            {
                                "code": "RATE_LIMIT_NOT_MONITOR",
                                "message": "Sliding-window rate limit is not a signal monitor",
                            },
                        ],
                    ),
                    "name": "Eleventh call in sliding ten minutes",
                    "aggregation": "COUNT",
                    "threshold": 10,
                    "window_intent": "sliding_10_minutes",
                },
            }
        )
        return proposals

    if "annual review" in text.lower() or "human process" in text.lower():
        proposals.append(
            {
                "proposal_family": "obligation",
                "generated": {
                    **_base_capability(
                        execution_support="MANUAL_PROCESS",
                        capability_gaps=[
                            {
                                "code": "MANUAL_PROCESS",
                                "message": "Process obligation; no runtime rule equivalent",
                            },
                        ],
                    ),
                    "modality": modality,
                    "topic": "periodic_review",
                    "text": text,
                },
            }
        )
        return proposals

    proposals.append(
        {
            "proposal_family": "obligation",
            "generated": {
                **_base_capability(binding=clause.get("binding") or "unbound"),
                "modality": modality,
                "text": text,
                "topic": clause.get("topic") or "general",
            },
        }
    )
    return proposals