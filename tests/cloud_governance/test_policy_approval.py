"""Unit tests for cloud policy approval digests and validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from rai_gateway.approval.domain import ApprovalStatus, IntegrationType
from rai_gateway.cloud_governance.policy_approval import (
    mark_approval_consumed,
    policy_action_digest,
    validate_approval_for_policy_apply,
)
from rai_gateway.models.approval import ApprovalRequest


def test_policy_action_digest_stable():
    change_id = uuid4()
    conn_id = uuid4()
    desired = {"floor_settings": {"filter_config": {"pi_and_jailbreak": "ENABLED"}}}
    a = policy_action_digest(
        policy_change_id=change_id, connection_id=conn_id, desired=desired
    )
    b = policy_action_digest(
        policy_change_id=change_id, connection_id=conn_id, desired=desired
    )
    assert a == b
    c = policy_action_digest(
        policy_change_id=change_id,
        connection_id=conn_id,
        desired={**desired, "location": "us-central1"},
    )
    assert c != a


def test_validate_approval_happy_path():
    change_id = uuid4()
    digest = policy_action_digest(
        policy_change_id=change_id,
        connection_id=uuid4(),
        desired={"k": "v"},
    )
    approval = ApprovalRequest(
        ai_system_id=uuid4(),
        agent_id=uuid4(),
        agent_version_id=uuid4(),
        runtime_identity_id=uuid4(),
        integration_type=IntegrationType.CLOUD_GOVERNANCE.value,
        canonical_action_payload={
            "parameters": {"policy_change_id": str(change_id)},
        },
        action_digest=digest,
        status=ApprovalStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by="tester",
    )
    validate_approval_for_policy_apply(
        approval, expected_digest=digest, policy_change_id=change_id
    )


def test_validate_approval_rejects_wrong_digest():
    approval = ApprovalRequest(
        ai_system_id=uuid4(),
        agent_id=uuid4(),
        agent_version_id=uuid4(),
        runtime_identity_id=uuid4(),
        integration_type=IntegrationType.CLOUD_GOVERNANCE.value,
        canonical_action_payload={},
        action_digest="deadbeef",
        status=ApprovalStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by="tester",
    )
    with pytest.raises(ValueError, match="action_digest"):
        validate_approval_for_policy_apply(
            approval,
            expected_digest="cafebabe",
            policy_change_id=uuid4(),
        )


def test_mark_approval_consumed():
    approval = ApprovalRequest(
        ai_system_id=uuid4(),
        agent_id=uuid4(),
        agent_version_id=uuid4(),
        runtime_identity_id=uuid4(),
        integration_type=IntegrationType.CLOUD_GOVERNANCE.value,
        canonical_action_payload={},
        action_digest="x",
        status=ApprovalStatus.APPROVED.value,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by="tester",
    )
    mark_approval_consumed(approval)
    assert approval.status == ApprovalStatus.CONSUMED.value
    assert approval.consumed_at is not None
