"""First-run setup: status, skip, staged creation and the resulting runtime access."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from rai_gateway.auth.passwords import hash_password
from rai_gateway.core.config import settings
from rai_gateway.core.database import get_db
from rai_gateway.governance.domain import GovernanceRole, MembershipStatus
from rai_gateway.main import app
from rai_gateway.models.database import AdminCredential, UserAccount
from rai_gateway.models.governance import OrganizationMembership
from rai_gateway.models.identity import AgentRuntimeIdentity, RuntimeCredential
from rai_gateway.models.registry import (
    Agent,
    AgentVersion,
    AgentVersionDependency,
    AISystem,
    MCPServer,
    ModelResource,
    ToolResource,
)
from rai_gateway.sdk.service import SdkService
from rai_gateway.services.admin_console import _ensure_default_rbac_seed

BASE = "/v1/productization/first-run"


def _migrate(schema_url: str) -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    # Alembic's logging setup disables existing loggers; this extra migration run
    # must not silence them for the tests that follow.
    loggers = [
        item
        for item in logging.root.manager.loggerDict.values()
        if isinstance(item, logging.Logger) and not item.disabled
    ]
    previous = os.environ.get("TEST_DATABASE_URL")
    os.environ["TEST_DATABASE_URL"] = schema_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("TEST_DATABASE_URL", None)
        else:
            os.environ["TEST_DATABASE_URL"] = previous
        for item in loggers:
            item.disabled = False


@pytest.fixture(scope="module")
def db_engine():
    """An empty schema of its own: "new organization" needs no leftover records."""
    base_url = os.environ.get("TEST_DATABASE_URL", settings.database_url)
    base_url = base_url.split("?")[0]
    schema = f"test_first_run_{uuid.uuid4().hex}"
    admin = create_engine(base_url)
    with admin.connect() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.commit()
    schema_url = f"{base_url}?options=-csearch_path%3D{schema}"
    _migrate(schema_url)
    engine = create_engine(schema_url)
    yield {"base_url": base_url, "schema": schema, "engine": engine}
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()
    admin.dispose()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _login(client, db, *, role=GovernanceRole.ORGANIZATION_ADMIN, verified=True):
    _ensure_default_rbac_seed(db)
    suffix = uuid.uuid4().hex[:8]
    email = f"first-run-{suffix}@example.com"
    user_id = f"user-{suffix}"
    db.add(
        UserAccount(
            user_id=user_id,
            email=email,
            name="Setup User",
            status="active",
            email_verified_at=datetime.now(timezone.utc) if verified else None,
        )
    )
    db.flush()
    db.add(
        AdminCredential(
            email=email,
            user_id=user_id,
            password_hash=hash_password("SecurePass123!"),
            is_active=True,
        )
    )
    db.add(
        OrganizationMembership(
            user_id=user_id,
            role=role.value,
            status=MembershipStatus.ACTIVE.value,
            created_by=user_id,
        )
    )
    db.commit()
    response = client.post(
        "/v1/auth/login", json={"email": email, "password": "SecurePass123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def headers(client, db_session):
    return _login(client, db_session)


def _resources_body(tag: str) -> dict:
    return {
        "models": [
            {"provider": "anthropic", "model_name": "claude-sonnet-5-5"},
            {"provider": "google_ai", "model_name": "gemini-3.8-flash"},
        ],
        "seed_servers": [{"key": "github", "tool_keys": None}],
        "custom": [
            {
                "ref": "crm",
                "kind": "mcp_server",
                "name": f"CRM {tag}",
                "tools": ["Create order", "Read order"],
            },
            {"ref": "docs", "kind": "data_source", "name": f"Handbook {tag}"},
        ],
    }


def _apply_resources(client, headers, tag: str) -> dict:
    response = client.post(
        f"{BASE}/apply/resources", json=_resources_body(tag), headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _systems_body(resources: dict, names: list[str], request_id=None) -> dict:
    items = [item for entry in resources["resources"] for item in entry["items"]]
    return {
        "request_id": str(request_id or uuid.uuid4()),
        "systems": [
            {
                "name": name,
                "model_ids": [model["id"] for model in resources["models"]],
                "dependencies": [
                    {"resource_type": i["resource_type"], "resource_id": i["id"]}
                    for i in items
                ],
            }
            for name in names
        ],
    }


def _heartbeat(client, system: dict, secret: str | None = None):
    return client.post(
        "/sdk/v1/heartbeat",
        json={"integration_id": system["integration_id"]},
        headers={"X-RAI-Agent-Key": secret or system["credential"]["secret"]},
    )


def test_new_organization_is_sent_to_setup_until_skipped(client, db_session):
    unverified = _login(client, db_session, verified=False)
    auditor = _login(client, db_session, role=GovernanceRole.AUDITOR)

    status = client.get(BASE, headers=unverified).json()
    assert status["state"] == "REQUIRED"
    assert status["is_new_organization"] is True
    assert status["should_redirect"] is True
    assert status["email_verified"] is False

    # Someone who could not complete setup is never forced into it.
    reader = client.get(BASE, headers=auditor).json()
    assert reader["can_setup"] is False
    assert reader["should_redirect"] is False
    assert client.post(f"{BASE}/skip", headers=auditor).status_code == 403
    assert (
        client.post(
            f"{BASE}/apply/resources", json=_resources_body("x"), headers=auditor
        ).status_code
        == 403
    )

    # Unverified: may leave, may not create.
    blocked = client.post(
        f"{BASE}/apply/resources", json=_resources_body("x"), headers=unverified
    )
    assert blocked.status_code == 403, blocked.text
    skipped = client.post(f"{BASE}/skip", headers=unverified)
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["state"] == "SKIPPED"
    assert skipped.json()["should_redirect"] is False
    assert db_session.query(AISystem).count() == 0

    welcome = client.get("/v1/productization/welcome", headers=unverified).json()
    assert welcome["setup_skipped"] is True


def test_catalog_offers_three_providers_and_example_resources(client, headers):
    catalog = client.get(f"{BASE}/catalog", headers=headers).json()
    providers = [p["value"] for p in catalog["providers"]]
    assert providers == ["google_ai", "anthropic", "openai"]
    assert {m["provider"] for m in catalog["models"]} == set(providers)
    github = next(r for r in catalog["resources"] if r["key"] == "github")
    assert github["tools"] and github["tools"][0]["key"].startswith("github.")


def test_resources_stage_can_be_retried(client, db_session, headers):
    first = _apply_resources(client, headers, "retry")
    counts = (
        db_session.query(ModelResource).count(),
        db_session.query(MCPServer).count(),
        db_session.query(ToolResource).count(),
    )
    again = _apply_resources(client, headers, "retry")
    assert again == first
    db_session.expire_all()
    assert counts == (
        db_session.query(ModelResource).count(),
        db_session.query(MCPServer).count(),
        db_session.query(ToolResource).count(),
    )
    # Only the chosen example server is registered, not the whole catalog.
    assert (
        db_session.query(MCPServer).filter(MCPServer.server_key == "atlassian").count()
        == 0
    )


def test_selected_tools_only_and_unknown_choice_rejected(client, db_session, headers):
    catalog = client.get(f"{BASE}/catalog", headers=headers).json()
    slack = next(r for r in catalog["resources"] if r["key"] == "slack")
    chosen = slack["tools"][0]["key"]
    body = _resources_body("subset")
    body["seed_servers"] = [{"key": "slack", "tool_keys": [chosen]}]
    result = client.post(f"{BASE}/apply/resources", json=body, headers=headers).json()
    entry = next(r for r in result["resources"] if r["ref"] == "seed:slack")
    assert [i["resource_type"] for i in entry["items"]] == ["mcp_server", "tool"]

    # Only what was chosen is switched on.
    server_id = uuid.UUID(entry["items"][0]["id"])
    statuses = {
        tool.tool_key: tool.status
        for tool in db_session.query(ToolResource).filter(
            ToolResource.mcp_server_id == server_id
        )
    }
    assert statuses.pop(chosen) == "ACTIVE"
    assert set(statuses.values()) == {"SEEDED"}

    body["seed_servers"] = [{"key": "no-such-server"}]
    bad = client.post(f"{BASE}/apply/resources", json=body, headers=headers)
    assert bad.status_code == 400, bad.text


def test_platform_templates_are_left_untouched(client, db_session, headers):
    seeded = client.post("/v1/registry/mcp-servers/seed-catalog", headers=headers)
    assert seeded.status_code == 200, seeded.text
    body = _resources_body("platform")
    body["seed_servers"] = [{"key": "sentry"}]
    result = client.post(f"{BASE}/apply/resources", json=body, headers=headers)
    assert result.status_code == 200, result.text
    entry = next(r for r in result.json()["resources"] if r["ref"] == "seed:sentry")

    db_session.expire_all()
    rows = {
        row.scope: row
        for row in db_session.query(MCPServer).filter(MCPServer.server_key == "sentry")
    }
    assert rows["PLATFORM"].status == "SEEDED"
    assert rows["ORGANIZATION"].status == "ACTIVE"
    assert entry["items"][0]["id"] == str(rows["ORGANIZATION"].id)

    # A tool named by key resolves to the usable copy, not the template.
    chosen = db_session.get(ToolResource, uuid.UUID(entry["items"][1]["id"]))
    resolved = SdkService(db_session)._resolve_tool_id(chosen.tool_key)
    assert resolved == chosen.id


def test_created_credential_passes_runtime_checks(client, db_session, headers):
    resources = _apply_resources(client, headers, "runtime")
    arabic = "مساعد العملاء"
    response = client.post(
        f"{BASE}/apply/systems",
        json=_systems_body(resources, ["Support Desk", arabic]),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["environment"] == "development"
    support, localized = body["systems"]
    assert support["agent"]["name"] == "Support Desk - agent 01"
    assert support["version"]["label"] == "Support Desk - agent 01 - V1.0.0"
    assert localized["system_key"].startswith("system-")
    assert support["credential"]["secret"] != localized["credential"]["secret"]

    db_session.expire_all()
    for created in body["systems"]:
        system = db_session.get(AISystem, uuid.UUID(created["ai_system_id"]))
        agent = db_session.get(Agent, uuid.UUID(created["agent"]["id"]))
        version = db_session.get(AgentVersion, uuid.UUID(created["version"]["id"]))
        identity = db_session.get(
            AgentRuntimeIdentity, uuid.UUID(created["runtime_identity"]["id"])
        )
        assert (system.status, agent.status, version.lifecycle_status) == (
            "APPROVED",
            "APPROVED",
            "APPROVED",
        )
        assert agent.current_version_id == version.id
        assert identity.environment == "development"
        kinds = {
            d.resource_type
            for d in db_session.query(AgentVersionDependency).filter(
                AgentVersionDependency.agent_version_id == version.id
            )
        }
        assert kinds == {"model", "mcp_server", "tool", "data_source"}

        beat = _heartbeat(client, created)
        assert beat.status_code == 200, beat.text

    # A real session and tool authorization with the new credential.
    key = {"X-RAI-Agent-Key": support["credential"]["secret"]}
    session = client.post(
        "/sdk/v1/sessions",
        json={"integration_id": support["integration_id"], "channel": "application"},
        headers=key,
    )
    assert session.status_code == 200, session.text
    # Both a custom tool and a chosen catalog example are usable at runtime.
    for ref in ("crm", "seed:github"):
        entry = next(r for r in resources["resources"] if r["ref"] == ref)
        server = entry["items"][0]
        # Consequential tools correctly ask for approval; use a read-only one.
        tool = next(
            item
            for item in entry["items"][1:]
            if not db_session.get(
                ToolResource, uuid.UUID(item["id"])
            ).consequential_action
        )
        authorized_request = {
            "integration_id": server["id"],
            "tool_id": tool["id"],
            "parameters": {},
        }
        decision = client.post(
            "/v1/runtime/actions/authorize", json=authorized_request, headers=key
        )
        assert decision.status_code == 200, f"{ref}: {decision.text}"
        assert decision.json()["allowed"] is True

    hierarchy = client.get(
        f"/v1/ai-systems/{support['ai_system_id']}/hierarchy", headers=headers
    )
    assert "Support Desk - agent 01 - V1.0.0" in hierarchy.text

    # The check above is meaningful: an unapproved agent is refused.
    agent = db_session.get(Agent, uuid.UUID(support["agent"]["id"]))
    agent.status = "SUSPENDED"
    db_session.commit()
    refused = client.post(
        "/v1/runtime/actions/authorize", json=authorized_request, headers=key
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["allowed"] is False

    status = client.get(BASE, headers=headers).json()
    assert status["is_new_organization"] is False
    assert status["should_redirect"] is False


def test_same_draft_again_replaces_the_secret_without_duplicates(
    client, db_session, headers
):
    resources = _apply_resources(client, headers, "replay")
    request = _systems_body(resources, ["Replay Bot"])
    first = client.post(f"{BASE}/apply/systems", json=request, headers=headers)
    second = client.post(f"{BASE}/apply/systems", json=request, headers=headers)
    assert second.status_code == 200, second.text
    a, b = first.json()["systems"][0], second.json()["systems"][0]
    for field in ("ai_system_id", "agent", "version", "runtime_identity"):
        assert a[field] == b[field]
    assert a["integration_id"] == b["integration_id"]
    assert a["credential"]["secret"] != b["credential"]["secret"]
    assert _heartbeat(client, a).status_code != 200
    assert _heartbeat(client, b).status_code == 200
    db_session.expire_all()
    assert db_session.query(AISystem).filter(AISystem.name == "Replay Bot").count() == 1


def test_duplicate_name_from_another_draft_is_refused(client, db_session, headers):
    resources = _apply_resources(client, headers, "dup")
    ok = client.post(
        f"{BASE}/apply/systems",
        json=_systems_body(resources, ["Taken Name"]),
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    credentials = db_session.query(RuntimeCredential).count()
    clash = client.post(
        f"{BASE}/apply/systems",
        json=_systems_body(resources, ["taken name"]),
        headers=headers,
    )
    assert clash.status_code == 409, clash.text
    assert clash.json()["detail"]["error"] == "DUPLICATE_NAME"
    twice = client.post(
        f"{BASE}/apply/systems",
        json=_systems_body(resources, ["One", "one"]),
        headers=headers,
    )
    assert twice.status_code == 400, twice.text
    db_session.expire_all()
    assert db_session.query(RuntimeCredential).count() == credentials


def test_systems_stage_is_all_or_nothing(client, db_session, headers):
    resources = _apply_resources(client, headers, "atomic")
    body = _systems_body(resources, ["Atomic First", "Atomic Second"])
    body["systems"][1]["model_ids"] = [str(uuid.uuid4())]
    response = client.post(f"{BASE}/apply/systems", json=body, headers=headers)
    assert response.status_code == 404, response.text
    db_session.expire_all()
    assert db_session.query(AISystem).filter(AISystem.name.like("Atomic%")).count() == 0
    assert db_session.query(Agent).filter(Agent.name.like("Atomic%")).count() == 0


def test_completing_ends_setup(client, headers):
    done = client.post("/v1/productization/welcome/complete", headers=headers)
    assert done.status_code == 200, done.text
    assert client.get(BASE, headers=headers).json()["state"] == "COMPLETED"
