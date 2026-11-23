"""Canonical outbound alert / webhook event types."""

from __future__ import annotations

from typing import Any

# Enforcement notifications (governance decisions before / at execution)
ENFORCEMENT_DENY = "enforcement.deny"
ENFORCEMENT_MONITOR = "enforcement.monitor"
ENFORCEMENT_REQUIRE_APPROVAL = "enforcement.require_approval"
ENFORCEMENT_ALLOW = "enforcement.allow"

# Risk / oversight
RISK_FINDING_CREATED = "risk.finding.created"

# Approval lifecycle (existing outbox types)
APPROVAL_EVENTS = [
    "approval.requested",
    "approval.approved",
    "approval.rejected",
    "approval.cancelled",
    "approval.expired",
    "approval.consumed",
]

ALERT_EVENT_GROUPS: list[dict[str, Any]] = [
    {
        "id": "enforcement",
        "label": "Enforcement",
        "description": "Runtime governance decisions (block, monitor, require approval).",
        "events": [
            {
                "type": "enforcement.*",
                "label": "All enforcement events",
                "includes": [
                    ENFORCEMENT_DENY,
                    ENFORCEMENT_MONITOR,
                    ENFORCEMENT_REQUIRE_APPROVAL,
                    ENFORCEMENT_ALLOW,
                ],
            },
            {"type": ENFORCEMENT_DENY, "label": "Block / deny"},
            {"type": ENFORCEMENT_MONITOR, "label": "Monitor"},
            {"type": ENFORCEMENT_REQUIRE_APPROVAL, "label": "Require approval"},
            {"type": ENFORCEMENT_ALLOW, "label": "Allow"},
        ],
    },
    {
        "id": "risk",
        "label": "Risk & findings",
        "events": [
            {"type": RISK_FINDING_CREATED, "label": "New risk finding"},
        ],
    },
    {
        "id": "approval",
        "label": "Approvals",
        "events": [{"type": e, "label": e.replace("approval.", "").title()} for e in APPROVAL_EVENTS]
        + [{"type": "approval.*", "label": "All approval events"}],
    },
]

ALL_KNOWN_EVENT_TYPES: set[str] = {
    ENFORCEMENT_DENY,
    ENFORCEMENT_MONITOR,
    ENFORCEMENT_REQUIRE_APPROVAL,
    ENFORCEMENT_ALLOW,
    RISK_FINDING_CREATED,
    *APPROVAL_EVENTS,
}


def event_type_matches(subscribed: str, event_type: str) -> bool:
    """Return True if ``event_type`` is covered by subscription ``subscribed``."""
    if subscribed == event_type:
        return True
    if subscribed == "*":
        return True
    if subscribed.endswith(".*"):
        prefix = subscribed[:-2]
        return event_type == prefix or event_type.startswith(prefix + ".")
    # Legacy approval umbrella
    if subscribed == "approval.decided" and event_type in {
        "approval.approved",
        "approval.rejected",
        "approval.cancelled",
    }:
        return True
    return False


def any_subscription_matches(subscriptions: list[str], event_type: str) -> bool:
    return any(event_type_matches(s, event_type) for s in subscriptions)
