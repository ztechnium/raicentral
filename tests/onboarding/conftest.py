"""Shared fixtures for onboarding intake tests (real PostgreSQL, real filesystem)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from onboarding_support import PURE_MODULES, Intake
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from rai_gateway.auth.passwords import hash_password
from rai_gateway.core.config import settings
from rai_gateway.core.database import get_db
from rai_gateway.governance.domain import GovernanceRole, MembershipStatus
from rai_gateway.main import app
from rai_gateway.models.database import AdminCredential, UserAccount, UserRole
from rai_gateway.models.governance import OrganizationMembership
from rai_gateway.services.admin_console import _ensure_default_rbac_seed


@pytest.fixture(autouse=True)
def clean_onboarding(request):
    """Isolate each test: the schema is shared across the whole test session."""
    if request.module.__name__.rsplit(".", 1)[-1] in PURE_MODULES:
        yield  # pure parser tests need no database
        return
    db_engine = request.getfixturevalue("db_engine")
    statements = [
        "DELETE FROM onboarding_governance_proposal_edits",
        "UPDATE onboarding_governance_proposal_sets SET current_reconciliation_id = NULL",
        "DELETE FROM onboarding_governance_reconciliation_snapshots",
        "DELETE FROM onboarding_governance_proposals",
        "DELETE FROM onboarding_governance_clauses",
        "DELETE FROM onboarding_governance_proposal_sets",
        "DELETE FROM onboarding_governance_calls",
        "DELETE FROM onboarding_governance_clarification_answers",
        "DELETE FROM onboarding_governance_plans",
        "DELETE FROM onboarding_proposal_edits",
        "UPDATE onboarding_proposal_sets SET current_reconciliation_id = NULL",
        "DELETE FROM onboarding_reconciliation_snapshots",
        "DELETE FROM onboarding_inventory_proposals",
        "DELETE FROM onboarding_proposal_sets",
        "DELETE FROM onboarding_discovery_calls",
        "DELETE FROM onboarding_clarification_answers",
        "DELETE FROM onboarding_discovery_plans",
        "DELETE FROM onboarding_run_sources",
        "DELETE FROM onboarding_extracted_blocks",
        "DELETE FROM onboarding_source_extractions",
        "DELETE FROM onboarding_provider_probes",
        "DELETE FROM onboarding_ai_configuration",
        "UPDATE onboarding_runs SET retry_of_run_id = NULL, preparation_run_id = NULL, "
        "extraction_run_id = NULL, discovery_plan_id = NULL, "
        "inventory_proposal_set_id = NULL, governance_plan_id = NULL",
        "UPDATE onboarding_sources SET current_revision_id = NULL",
        "DELETE FROM onboarding_runs",
        "DELETE FROM onboarding_source_revisions",
        "DELETE FROM onboarding_sources",
        "DELETE FROM onboarding_sessions",
        "DELETE FROM onboarding_mutation_receipts",
        "DELETE FROM background_jobs WHERE job_type LIKE 'ONBOARDING_%'",
    ]
    with db_engine["engine"].begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
    yield


@pytest.fixture()
def storage_root(tmp_path, monkeypatch):
    root = tmp_path / "sources"
    monkeypatch.setattr(settings, "onboarding_source_storage_root", str(root))
    return root


@pytest.fixture()
def session_factory(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine["engine"])


@pytest.fixture()
def client(db_session, storage_root):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def make_user(client, db_session):
    """Create a user with the given governance roles; return auth headers."""

    def _make(
        *roles: str, active: bool = True, platform_role: str | None = None
    ) -> dict[str, str]:
        _ensure_default_rbac_seed(db_session)
        suffix = uuid.uuid4().hex[:8]
        email = f"intake-{suffix}@example.com"
        user_id = f"user-{suffix}"
        db_session.add(
            UserAccount(
                user_id=user_id,
                email=email,
                name="Intake User",
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
        if platform_role:
            db_session.add(UserRole(user_id=user_id, role_id=platform_role))
        for role in roles:
            db_session.add(
                OrganizationMembership(
                    user_id=user_id,
                    role=role,
                    status=MembershipStatus.ACTIVE.value
                    if active
                    else MembershipStatus.SUSPENDED.value,
                    created_by=user_id,
                )
            )
        db_session.commit()
        login = client.post(
            "/v1/auth/login", json={"email": email, "password": "SecurePass123!"}
        )
        assert login.status_code == 200, login.text
        return {"Authorization": f"Bearer {login.json()['access_token']}"}

    return _make


@pytest.fixture()
def admin(make_user):
    return make_user(
        GovernanceRole.ORGANIZATION_ADMIN.value, GovernanceRole.RAI_OFFICER.value
    )


@pytest.fixture()
def api(client, admin):
    return Intake(client, admin)


@pytest.fixture()
def session(api):
    response = api.create()
    assert response.status_code == 201, response.text
    return response.json()
