"""Administrator provisioning through ``rai-gateway admin create``.

Tests run against a migrated schema whose identity tables are emptied for each
test, so every one starts from the state of a new install.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from click.testing import CliRunner
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from rai_gateway.auth.passwords import verify_password
from rai_gateway.cli import admin as admin_cli
from rai_gateway.core.authorization import load_permissions_for_roles
from rai_gateway.core.config import settings
from rai_gateway.core.database import get_db
from rai_gateway.governance import departments as departments_module
from rai_gateway.governance.departments import DEFAULT_DEPARTMENTS
from rai_gateway.main import app
from rai_gateway.models.database import (
    AdminCredential,
    RoleDefinition,
    RolePermission,
    UserAccount,
    UserRole,
)
from rai_gateway.models.governance import (
    Department,
    OrganizationGovernanceProfile,
    OrganizationMembership,
)
from rai_gateway.models.productization import OrganizationOnboarding
from rai_gateway.services.admin_console import (
    DEFAULT_RBAC_ROLES,
    _ensure_bootstrap_admin,
)

ROOT = Path(__file__).resolve().parents[2]
EMAIL = "first.admin@example.com"
PASSWORD = "Correct-Horse-Battery-9"
PRIVILEGED_MEMBERSHIPS = {"ORGANIZATION_ADMIN", "RAI_OFFICER"}


@pytest.fixture(scope="module")
def migrated_schema():
    """One migrated schema for this module; migrations seed none of these tables."""
    base_url = os.environ.get("TEST_DATABASE_URL", settings.database_url).split("?")[0]
    schema = f"test_{uuid.uuid4().hex}"
    base_engine = create_engine(base_url)
    with base_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    base_engine.dispose()
    schema_url = f"{base_url}?options=-csearch_path%3D{schema}"

    with pytest.MonkeyPatch.context() as patch:
        # alembic/env.py gives TEST_DATABASE_URL precedence over the config value.
        patch.setenv("TEST_DATABASE_URL", schema_url)
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    engine = create_engine(schema_url)
    try:
        yield {"url": schema_url, "engine": engine}
    finally:
        engine.dispose()
        cleanup = create_engine(base_url)
        with cleanup.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.commit()
        cleanup.dispose()


@pytest.fixture()
def fresh_db(migrated_schema, monkeypatch):
    """An empty installation, with production-style authentication settings."""
    engine = migrated_schema["engine"]
    tables = ", ".join(
        model.__tablename__
        for model in (
            UserAccount,
            AdminCredential,
            UserRole,
            RoleDefinition,
            RolePermission,
            OrganizationMembership,
            OrganizationGovernanceProfile,
            OrganizationOnboarding,
            Department,
        )
    )
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr(settings, "admin_bootstrap_enabled", False)
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "auth_set_cookies", False)
    monkeypatch.setattr(admin_cli, "SessionLocal", factory)
    monkeypatch.setattr(admin_cli, "init_db", lambda: None)

    session = factory()
    try:
        yield {"session": session, "factory": factory, "url": migrated_schema["url"]}
    finally:
        session.close()


@pytest.fixture()
def client(fresh_db):
    def override_get_db():
        db = fresh_db["factory"]()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _create(role: str = "admin", email: str = EMAIL, password: str = PASSWORD):
    return CliRunner().invoke(
        admin_cli.admin,
        [
            "create",
            "--email",
            email,
            "--name",
            "First Admin",
            "--role",
            role,
            "--password",
            password,
        ],
    )


def _memberships(db, user_id: str) -> set[str]:
    rows = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.status == "ACTIVE",
        )
        .all()
    )
    return {row.role for row in rows}


def _login(client: TestClient, email: str = EMAIL, password: str = PASSWORD):
    return client.post("/v1/auth/login", json={"email": email, "password": password})


def test_real_cli_process_provisions_a_fresh_database(fresh_db, tmp_path):
    """The shipped entry point, in a clean interpreter (catches import/mapper errors)."""
    env = {
        **os.environ,
        "DATABASE_URL": fresh_db["url"],
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONIOENCODING": "utf-8",
        "ADMIN_BOOTSTRAP_ENABLED": "false",
        "ADMIN_TOKEN": "",
    }
    result = subprocess.run(
        [sys.executable, "-m", "rai_gateway.cli", "admin", "create"]
        + ["--email", EMAIL, "--name", "First Admin", "--role", "admin"]
        + ["--password", PASSWORD],
        cwd=tmp_path,  # no developer .env
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    user = fresh_db["session"].query(UserAccount).filter_by(email=EMAIL).one()
    assert _memberships(fresh_db["session"], user.user_id) == PRIVILEGED_MEMBERSHIPS


def test_creates_canonical_roles_permissions_and_assignment(fresh_db):
    result = _create()
    assert result.exit_code == 0, result.output

    db = fresh_db["session"]
    roles = {row.role_id: row for row in db.query(RoleDefinition).all()}
    assert set(DEFAULT_RBAC_ROLES) <= set(roles)
    assert all(roles[role_id].is_system for role_id in DEFAULT_RBAC_ROLES)
    for role_id, spec in DEFAULT_RBAC_ROLES.items():
        granted = {
            row.permission
            for row in db.query(RolePermission).filter_by(role_id=role_id)
        }
        assert set(spec["permissions"]) <= granted, role_id

    user = db.query(UserAccount).filter_by(email=EMAIL).one()
    assert user.status == "active"
    assert user.email_verified_at is not None
    assignments = db.query(UserRole).filter_by(user_id=user.user_id).all()
    assert [row.role_id for row in assignments] == ["admin"]
    assert assignments[0].created_at is not None
    assert load_permissions_for_roles(db, ["admin"]) == {"*"}


@pytest.mark.parametrize("role", ["admin", "platform_admin", "Platform-Admin"])
def test_instance_admin_initializes_organization_foundation(fresh_db, role):
    result = _create(role=role)
    assert result.exit_code == 0, result.output

    db = fresh_db["session"]
    user = db.query(UserAccount).filter_by(email=EMAIL).one()
    assert db.query(OrganizationGovernanceProfile).count() == 1
    assert db.query(OrganizationOnboarding).count() == 1
    assert _memberships(db, user.user_id) == PRIVILEGED_MEMBERSHIPS
    names = {row.name for row in db.query(Department).all()}
    assert {name for name, _ in DEFAULT_DEPARTMENTS} <= names


def test_second_admin_reuses_the_singleton_organization(fresh_db):
    assert _create().exit_code == 0
    assert _create(email="second@example.com").exit_code == 0

    db = fresh_db["session"]
    assert db.query(OrganizationGovernanceProfile).count() == 1
    assert db.query(OrganizationOnboarding).count() == 1
    assert db.query(Department).count() == len(
        {row.name for row in db.query(Department).all()}
    )


def test_password_is_stored_only_as_a_salted_hash(fresh_db):
    result = _create()
    assert result.exit_code == 0

    credential = fresh_db["session"].query(AdminCredential).one()
    assert PASSWORD not in credential.password_hash
    assert PASSWORD not in result.output
    assert credential.password_hash.startswith("pbkdf2_sha256$")
    assert verify_password(PASSWORD, credential.password_hash)
    assert not verify_password("wrong-password", credential.password_hash)


def test_provisioned_admin_signs_in_and_reaches_governance(fresh_db, client):
    assert _create().exit_code == 0

    login = _login(client)
    assert login.status_code == 200, login.text
    body = login.json()
    assert set(body["roles"]) == PRIVILEGED_MEMBERSHIPS
    assert body["platform_roles"] == ["admin"]

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    context = client.get("/v1/auth/context", headers=headers)
    assert context.status_code == 200, context.text
    assert "organization_governance:manage" in context.json()["permissions"]
    dashboard = client.get("/v1/portfolio/dashboard", headers=headers)
    assert dashboard.status_code == 200, dashboard.text

    assert _login(client, password="wrong-password").status_code == 401


def test_shared_admin_token_is_rejected_when_disabled(fresh_db, client):
    assert _create().exit_code == 0

    for token in ("change-me", "dev-admin-token-change-me", ""):
        headers = {"X-Admin-Token": token}
        assert client.get("/v1/auth/me", headers=headers).status_code == 401
        assert client.get("/admin/api/auth/me", headers=headers).status_code == 401


def test_duplicate_email_is_rejected(fresh_db):
    assert _create().exit_code == 0
    result = _create(email=EMAIL.upper())

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert fresh_db["session"].query(UserAccount).count() == 1
    assert fresh_db["session"].query(AdminCredential).count() == 1


def test_email_of_a_user_without_credentials_is_rejected(fresh_db):
    db = fresh_db["session"]
    db.add(UserAccount(user_id="invited", email=EMAIL, name="Invited"))
    db.commit()

    result = _create()

    assert result.exit_code == 1
    assert "already exists" in result.output
    assert db.query(AdminCredential).count() == 0
    assert db.query(UserRole).count() == 0


@pytest.mark.parametrize(
    "role", ["superuser", "organization_admin", "rai_officer", "auditor", ""]
)
def test_non_platform_roles_are_rejected(fresh_db, role):
    result = _create(role=role)

    assert result.exit_code == 1
    assert "Invalid role" in result.output
    db = fresh_db["session"]
    assert db.query(UserAccount).count() == 0
    assert db.query(OrganizationMembership).count() == 0


def test_support_user_receives_no_privileged_access(fresh_db, client):
    result = _create(role="platform_support")
    assert result.exit_code == 0, result.output

    db = fresh_db["session"]
    user = db.query(UserAccount).filter_by(email=EMAIL).one()
    assert _memberships(db, user.user_id) == set()
    assert db.query(OrganizationGovernanceProfile).count() == 0
    assert db.query(Department).filter_by(created_by=user.user_id).count() == 0

    login = _login(client)
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/v1/auth/context", headers=headers).status_code == 403
    assert client.get("/v1/ai-systems", headers=headers).status_code == 403


@pytest.mark.parametrize("bootstrap_enabled", [False, True])
def test_bootstrap_email_does_not_escalate_a_support_user(
    fresh_db, monkeypatch, bootstrap_enabled
):
    """Authentication runs the bootstrap check; it must respect the stored role."""
    monkeypatch.setattr(settings, "admin_bootstrap_enabled", bootstrap_enabled)
    monkeypatch.setattr(settings, "admin_bootstrap_email", EMAIL)
    assert _create(role="platform_support").exit_code == 0

    db = fresh_db["session"]
    _ensure_bootstrap_admin(db)

    user = db.query(UserAccount).filter_by(email=EMAIL).one()
    assert _memberships(db, user.user_id) == set()


def test_support_role_bootstrap_creates_no_privileged_membership(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "admin_bootstrap_enabled", True)
    monkeypatch.setattr(settings, "admin_bootstrap_email", EMAIL)
    monkeypatch.setattr(settings, "admin_bootstrap_role", "platform_support")

    db = fresh_db["session"]
    _ensure_bootstrap_admin(db)

    assert db.query(AdminCredential).count() == 1
    assert db.query(OrganizationMembership).count() == 0


def test_foundation_failure_leaves_no_partial_account(fresh_db, monkeypatch):
    db = fresh_db["session"]
    before = db.query(Department).count()

    def explode(*_args, **_kwargs):
        raise RuntimeError("department seed failed")

    with monkeypatch.context() as patch:
        patch.setattr(departments_module, "ensure_default_departments", explode)
        result = _create()

    assert result.exit_code == 1
    assert "department seed failed" in result.output
    db.rollback()
    for model in (
        UserAccount,
        UserRole,
        AdminCredential,
        OrganizationMembership,
        OrganizationGovernanceProfile,
        OrganizationOnboarding,
    ):
        assert db.query(model).count() == 0, model.__name__
    assert db.query(Department).count() == before

    # The same command succeeds once the fault is gone.
    retry = _create()
    assert retry.exit_code == 0, retry.output
    assert db.query(OrganizationMembership).count() == 2


def test_database_errors_do_not_print_credentials(fresh_db, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def explode(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO admin_credentials", {"password_hash": "LEAKED"}, Exception("x")
        )

    monkeypatch.setattr(admin_cli, "_ensure_instance_foundation", explode)
    result = _create()

    assert result.exit_code == 1
    assert "LEAKED" not in result.output
    assert "IntegrityError" in result.output
