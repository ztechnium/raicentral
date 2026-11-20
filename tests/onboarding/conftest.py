"""Shared fixtures for onboarding intake tests (real PostgreSQL, real filesystem)."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
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

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


DOCX = make_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})
XLSX = make_zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"})
CSV = b"system,owner\nchatbot,alice\n"
MD = "# Policy\n\nUse AI responsibly — é ü\n".encode("utf-8")


@pytest.fixture(autouse=True)
def clean_onboarding(db_engine):
    """Isolate each test: the schema is shared across the whole test session."""
    statements = [
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


class Intake:
    """Tiny typed wrapper over the HTTP contract."""

    BASE = "/v1/productization/onboarding/sessions"

    def __init__(self, client: TestClient, headers: dict[str, str]) -> None:
        self.client = client
        self.headers = headers

    def _h(self, key: str | None = None) -> dict[str, str]:
        return {**self.headers, "Idempotency-Key": key or uuid.uuid4().hex}

    def create(self, title: str = "Q4 onboarding", key: str | None = None):
        return self.client.post(self.BASE, json={"title": title}, headers=self._h(key))

    def get(self, session_id: str):
        return self.client.get(f"{self.BASE}/{session_id}", headers=self.headers)

    def upload(
        self,
        session: dict,
        name: str,
        data: bytes,
        content_type: str = "application/octet-stream",
        key: str | None = None,
        version: int | None = None,
    ):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/files",
            files={"file": (name, data, content_type)},
            data={"expected_row_version": str(version or session["row_version"])},
            headers=self._h(key),
        )

    def replace_file(self, session, source_id, name, data, key=None, version=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/{source_id}/revisions/file",
            files={"file": (name, data, "application/octet-stream")},
            data={"expected_row_version": str(version or session["row_version"])},
            headers=self._h(key),
        )

    def text(
        self,
        session,
        text_value,
        role="USER_PROMPT",
        title=None,
        key=None,
        version=None,
    ):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/text",
            json={
                "role": role,
                "text": text_value,
                "title": title,
                "expected_row_version": version or session["row_version"],
            },
            headers=self._h(key),
        )

    def revise_text(self, session, source_id, text_value, key=None, version=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/sources/{source_id}/revisions/text",
            json={
                "text": text_value,
                "expected_row_version": version or session["row_version"],
            },
            headers=self._h(key),
        )

    def include(self, session, source_id, included: bool, version=None):
        return self.client.patch(
            f"{self.BASE}/{session['id']}/sources/{source_id}",
            json={
                "included": included,
                "expected_row_version": version or session["row_version"],
            },
            headers=self.headers,
        )

    def prepare(self, session, key=None, version=None, revision=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/prepare",
            json={
                "expected_row_version": version or session["row_version"],
                "requested_input_revision": (
                    session["input_revision"] if revision is None else revision
                ),
            },
            headers=self._h(key),
        )

    def retry(self, session, run_id, key=None, version=None, revision=None):
        return self.client.post(
            f"{self.BASE}/{session['id']}/runs/{run_id}/retry",
            json={
                "expected_row_version": version or session["row_version"],
                "requested_input_revision": (
                    session["input_revision"] if revision is None else revision
                ),
            },
            headers=self._h(key),
        )

    def cancel(self, session_id, run_id):
        return self.client.post(
            f"{self.BASE}/{session_id}/runs/{run_id}/cancel", headers=self.headers
        )


@pytest.fixture()
def api(client, admin):
    return Intake(client, admin)


@pytest.fixture()
def session(api):
    response = api.create()
    assert response.status_code == 201, response.text
    return response.json()


def fresh(api: Intake, session: dict) -> dict:
    """Reload the session from the server (it is the only authority)."""
    response = api.get(session["id"])
    assert response.status_code == 200, response.text
    return response.json()
