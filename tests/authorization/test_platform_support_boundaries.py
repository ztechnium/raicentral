"""platform_support reads across the instance but must never write through its role."""

from __future__ import annotations

import uuid

import pytest

from rai_gateway.cli.admin import create_admin_user
from rai_gateway.models.governance import OrganizationMembership

PASSWORD = "Correct-Horse-Battery-9"


def _account(db_session, test_client, role: str) -> tuple[str, dict[str, str]]:
    email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    user, _ = create_admin_user(
        db_session, email=email, password=PASSWORD, name=role, role=role
    )
    login = test_client.post(
        "/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    return user.user_id, {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.parametrize("governance_role", ["ORGANIZATION_ADMIN", "RAI_OFFICER"])
def test_support_cannot_grant_itself_a_privileged_membership(
    db_session, test_client, governance_role
):
    user_id, headers = _account(db_session, test_client, "platform_support")

    response = test_client.post(
        "/v1/organization/memberships",
        headers=headers,
        json={"user_id": user_id, "role": governance_role},
    )

    assert response.status_code == 403, response.text
    assert (
        db_session.query(OrganizationMembership).filter_by(user_id=user_id).count() == 0
    )


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("patch", "/v1/organization/governance-profile", {"name": "Taken over"}),
        (
            "post",
            "/v1/portfolio/decisions",
            {
                "scope_type": "ORGANIZATION",
                "scope_id": "x",
                "decision": "x",
                "reason": "x",
            },
        ),
        ("post", "/v1/portfolio/reports", {}),
        ("post", "/v1/portfolio/projections/refresh", None),
    ],
)
def test_support_cannot_write_through_its_platform_role(
    db_session, test_client, method, path, body
):
    _, headers = _account(db_session, test_client, "platform_support")

    response = test_client.request(method, path, headers=headers, json=body)

    assert response.status_code == 403, response.text


def test_support_keeps_read_access(db_session, test_client):
    _, headers = _account(db_session, test_client, "platform_support")

    for path in ("/v1/portfolio/dashboard", "/v1/organization/memberships"):
        response = test_client.get(path, headers=headers)
        assert response.status_code == 200, f"{path}: {response.text}"


def test_instance_admin_can_still_grant_memberships(db_session, test_client):
    _, headers = _account(db_session, test_client, "platform_admin")
    member_id, _ = _account(db_session, test_client, "platform_support")

    response = test_client.post(
        "/v1/organization/memberships",
        headers=headers,
        json={"user_id": member_id, "role": "AUDITOR"},
    )

    assert response.status_code == 200, response.text
