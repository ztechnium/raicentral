"""Cloud governance API integration tests (mocked GCP)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from rai_gateway.auth.passwords import hash_password
from rai_gateway.cloud_governance.domain import AuthMode, CloudProvider
from rai_gateway.cloud_governance.protocols import DiscoveredResource, VerifyResult
from rai_gateway.core.database import get_db
from rai_gateway.governance.domain import GovernanceRole, MembershipStatus
from rai_gateway.main import app
from rai_gateway.models.database import AdminCredential, UserAccount
from rai_gateway.models.governance import OrganizationMembership
from rai_gateway.models.registry import AISystem
from rai_gateway.services.admin_console import _ensure_default_rbac_seed


@pytest.fixture()
def public_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def auth_headers(public_client, db_session) -> dict[str, str]:
    _ensure_default_rbac_seed(db_session)
    suffix = uuid.uuid4().hex[:8]
    email = f"cloud-gov-{suffix}@example.com"
    user_id = f"user-{suffix}"
    db_session.add(
        UserAccount(
            user_id=user_id,
            email=email,
            name="Cloud Gov User",
            status="active",
            email_verified_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    db_session.add(
        AdminCredential(
            email=email,
            user_id=user_id,
            password_hash=hash_password("SecurePass123!"),
            is_active=True,
        )
    )
    db_session.add(
        OrganizationMembership(
            user_id=user_id,
            role=GovernanceRole.ORGANIZATION_ADMIN.value,
            status=MembershipStatus.ACTIVE.value,
            created_by=user_id,
        )
    )
    db_session.commit()
    login = public_client.post(
        "/v1/auth/login",
        json={"email": email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@patch("rai_gateway.cloud_governance.service._get_connector")
def test_connection_lifecycle_mocked(
    mock_get_connector, public_client, auth_headers, db_session
):
    class FakeConnector:
        provider_id = CloudProvider.GCP.value

        def verify(self, *, scope, auth_config):
            _ = (scope, auth_config)
            return VerifyResult(
                ok=True,
                capabilities={
                    "discovery": True,
                    "model_armor_read": True,
                    "enforce": False,
                },
                diagnostics=[],
                health={"provider": "gcp"},
            )

        def discover_assets(self, *, scope, auth_config, page_token=None):
            _ = (scope, auth_config, page_token)
            return (
                [
                    DiscoveredResource(
                        canonical_name="//aiplatform.googleapis.com/projects/demo/locations/us-central1/models/1",
                        asset_type="aiplatform.googleapis.com/Model",
                        display_name="demo-model",
                        location="us-central1",
                        config_summary={"labels": {}},
                    )
                ],
                None,
                [],
            )

        def inspect_model_armor(self, *, scope, auth_config):
            _ = (scope, auth_config)
            return {"supported": True, "templates": []}

        def preview_model_armor_change(self, *, scope, auth_config, desired):
            _ = (scope, auth_config, desired)
            return {"dry_run": True}

        def apply_model_armor_change(self, **kwargs):
            _ = kwargs
            raise NotImplementedError

    mock_get_connector.return_value = FakeConnector()

    providers = public_client.get(
        "/v1/cloud-governance/providers", headers=auth_headers
    )
    assert providers.status_code == 200
    assert any(p["id"] == "gcp" for p in providers.json()["providers"])

    created = public_client.post(
        "/v1/cloud-governance/connections",
        headers=auth_headers,
        json={
            "provider": "gcp",
            "display_name": "Demo GCP",
            "scope_identifier": "demo-project",
            "auth_mode": AuthMode.ADC_IMPERSONATION.value,
            "auth_config": {
                "service_account_email": "raicentral-discover@demo.iam.gserviceaccount.com"
            },
            "enforcement_enabled": False,
        },
    )
    assert created.status_code == 200, created.text
    connection_id = created.json()["connection"]["id"]

    verified = public_client.post(
        f"/v1/cloud-governance/connections/{connection_id}/verify",
        headers=auth_headers,
    )
    assert verified.status_code == 200
    assert verified.json()["verify"]["ok"] is True

    sync = public_client.post(
        f"/v1/cloud-governance/connections/{connection_id}/sync",
        headers=auth_headers,
    )
    assert sync.status_code == 200

    from rai_gateway.workers.cloud_sync import process_cloud_jobs

    processed = process_cloud_jobs(db_session, limit=5)
    assert processed >= 1

    assets = public_client.get(
        f"/v1/cloud-governance/connections/{connection_id}/assets",
        headers=auth_headers,
    )
    assert assets.status_code == 200
    assert len(assets.json()["assets"]) == 1

    ai_system = AISystem(
        name="Existing App",
        system_key=f"app-{uuid.uuid4().hex[:6]}",
        criticality="high",
        status="ACTIVE",
        created_by="tester",
    )
    db_session.add(ai_system)
    db_session.commit()
    db_session.refresh(ai_system)

    asset_id = assets.json()["assets"][0]["id"]
    correlated = public_client.post(
        f"/v1/cloud-governance/connections/{connection_id}/assets/{asset_id}/correlate",
        headers=auth_headers,
        json={"ai_system_id": str(ai_system.id)},
    )
    assert correlated.status_code == 200
    assert correlated.json()["asset"]["status"] == "linked"

    coverage = public_client.get(
        f"/v1/cloud-governance/connections/{connection_id}/coverage",
        headers=auth_headers,
    )
    assert coverage.status_code == 200
    assert coverage.json()["coverage"]["stages"]["linked_to_registry"] == 1


def test_reject_service_account_key(public_client, auth_headers):
    resp = public_client.post(
        "/v1/cloud-governance/connections",
        headers=auth_headers,
        json={
            "provider": "gcp",
            "display_name": "Bad",
            "scope_identifier": "demo",
            "auth_mode": AuthMode.CUSTOMER_ADC.value,
            "auth_config": {"type": "service_account", "private_key": "secret"},
        },
    )
    assert resp.status_code == 400
