"""Approval binding and digest for cloud policy changes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from rai_gateway.approval.domain import ApprovalStatus, IntegrationType
from rai_gateway.models.approval import ApprovalRequest


@dataclass
class CloudPolicyAction:
    """Canonical action identity for approval binding."""

    policy_change_id: str
    connection_id: str
    desired: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_change_id": self.policy_change_id,
            "connection_id": self.connection_id,
            "desired": self.desired,
            "integration_type": IntegrationType.CLOUD_GOVERNANCE.value,
        }


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def policy_action_digest(
    *,
    policy_change_id: UUID,
    connection_id: UUID,
    desired: dict[str, Any],
) -> str:
    payload = _normalize(
        CloudPolicyAction(
            policy_change_id=str(policy_change_id),
            connection_id=str(connection_id),
            desired=desired,
        ).to_dict()
    )
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_approval_for_policy_apply(
    approval: ApprovalRequest,
    *,
    expected_digest: str,
    policy_change_id: UUID,
) -> None:
    now = datetime.now(timezone.utc)
    if approval.integration_type != IntegrationType.CLOUD_GOVERNANCE.value:
        raise ValueError("approval integration_type must be cloud_governance")
    if approval.status == ApprovalStatus.CONSUMED.value:
        raise ValueError("approval already consumed")
    if approval.status != ApprovalStatus.APPROVED.value:
        raise ValueError(f"approval must be APPROVED, got {approval.status}")
    if approval.expires_at <= now:
        raise ValueError("approval expired")
    if approval.action_digest != expected_digest:
        raise ValueError("approval action_digest does not match policy change")
    payload = approval.canonical_action_payload or {}
    params = payload.get("parameters") or payload
    bound_id = params.get("policy_change_id")
    if bound_id is not None and str(bound_id) != str(policy_change_id):
        raise ValueError("approval does not target this policy change")


def mark_approval_consumed(approval: ApprovalRequest) -> None:
    if approval.status != ApprovalStatus.APPROVED.value:
        raise ValueError("cannot consume non-approved request")
    approval.status = ApprovalStatus.CONSUMED.value
    approval.consumed_at = datetime.now(timezone.utc)
