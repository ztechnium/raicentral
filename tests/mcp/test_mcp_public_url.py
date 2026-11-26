"""MCP discovery and OAuth sign-in through a reverse proxy, as a client sees them.

The client only ever follows URLs the server advertises. A small proxy in front of
the app reproduces the documented Nginx layout: ``/api`` is stripped, and the
well-known discovery paths are forwarded unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from rai_gateway.auth import service as auth_service
from rai_gateway.cli.admin import create_admin_user
from rai_gateway.core.config import Settings, settings
from rai_gateway.mcp import api_client, oauth_provider
from rai_gateway.mcp.integration import (
    integrate_management_mcp,
    mcp_transport_security,
)
from rai_gateway.models.database import UserAccount

PASSWORD = "Correct-Horse-Battery-9"
CALLBACK = "http://127.0.0.1:9876/callback"
WELL_KNOWN = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
)


class _ReverseProxy:
    def __init__(self, app, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.prefix:
            path = scope["path"]
            if path.startswith(f"{self.prefix}/"):
                path = path[len(self.prefix) :]
                scope = {**scope, "path": path, "raw_path": path.encode()}
            elif not path.startswith(WELL_KNOWN):
                await send(
                    {"type": "http.response.start", "status": 418, "headers": []}
                )
                await send({"type": "http.response.body", "body": b"console"})
                return
        await self.app(scope, receive, send)


@pytest.fixture(params=["https://rai.example.test/api", "https://api.example.test"])
def public_base(request, monkeypatch) -> str:
    monkeypatch.setattr(settings, "api_public_base_url", request.param)
    monkeypatch.setattr(settings, "mcp_server_public_base_url", request.param)
    monkeypatch.setattr(settings, "app_public_url", "https://rai.example.test")
    monkeypatch.setattr(settings, "mcp_server_enabled", True)
    return request.param


@pytest.fixture()
def proxied(public_base, db_engine, monkeypatch):
    monkeypatch.setattr(
        oauth_provider, "SessionLocal", sessionmaker(bind=db_engine["engine"])
    )
    previous_app = api_client._gateway_app
    app = FastAPI()
    integrate_management_mcp(app)
    proxy = _ReverseProxy(app, urlsplit(public_base).path)
    origin = f"https://{urlsplit(public_base).netloc}"
    try:
        with TestClient(proxy, base_url=origin) as client:
            yield client
    finally:
        api_client.bind_gateway_app(previous_app)


@pytest.fixture()
def account(db_session) -> str:
    email = f"mcp-{uuid.uuid4().hex[:8]}@example.com"
    create_admin_user(
        db_session, email=email, password=PASSWORD, name="MCP", role="admin"
    )
    return email


def _sse_json(response) -> dict:
    if "text/event-stream" in response.headers["content-type"]:
        data = [
            line[5:].strip()
            for line in response.text.splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
        return json.loads(data[-1])
    return response.json()


def _initialize(client, url, token=None):
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        url,
        headers=headers,
        follow_redirects=False,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        },
    )


def test_client_discovers_signs_in_and_calls_mcp(
    proxied, public_base, account, db_session
):
    client = proxied
    resource = f"{public_base}/mcp"

    # 1. Unauthenticated call points at the resource metadata.
    challenge = _initialize(client, resource)
    assert challenge.status_code == 401, challenge.text
    metadata_url = re.search(
        r'resource_metadata="([^"]+)"', challenge.headers["www-authenticate"]
    ).group(1)

    # 2. Protected resource metadata names the authorization server.
    protected = client.get(metadata_url)
    assert protected.status_code == 200, protected.text
    assert protected.json()["resource"] == resource
    issuer = protected.json()["authorization_servers"][0]
    assert issuer == resource

    # 3. Authorization server metadata, at both places clients look.
    parsed = urlsplit(issuer)
    inserted = (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/.well-known/oauth-authorization-server{parsed.path}"
    )
    appended = f"{issuer}/.well-known/oauth-authorization-server"
    documents = [client.get(inserted), client.get(appended)]
    assert [d.status_code for d in documents] == [200, 200]
    assert documents[0].json() == documents[1].json()
    server = documents[0].json()
    assert server["issuer"] == issuer
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert server[key].startswith(f"{resource}/"), key
    for document in (protected, *documents):
        assert "127.0.0.1" not in document.text
        assert "localhost" not in document.text

    # 4. Register, authorize, sign in, exchange the code.
    registration = client.post(
        server["registration_endpoint"],
        json={"redirect_uris": [CALLBACK], "client_name": "pytest"},
    )
    assert registration.status_code == 201, registration.text
    registered = registration.json()
    verifier = secrets.token_urlsafe(48)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    authorize = client.get(
        server["authorization_endpoint"],
        params={
            "response_type": "code",
            "client_id": registered["client_id"],
            "redirect_uri": CALLBACK,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": "state-1",
        },
        follow_redirects=False,
    )
    assert authorize.status_code == 302, authorize.text
    login_url = authorize.headers["location"]
    assert login_url.startswith(f"{resource}/oauth/login?")
    page = client.get(login_url)
    assert page.status_code == 200, page.text
    assert 'action="login"' in page.text
    submit = client.post(
        login_url.split("?")[0],
        data={
            "pending_id": parse_qs(urlsplit(login_url).query)["pending_id"][0],
            "email": account,
            "password": PASSWORD,
        },
        follow_redirects=False,
    )
    assert submit.status_code == 302, submit.text
    callback = parse_qs(urlsplit(submit.headers["location"]).query)
    assert callback["state"] == ["state-1"]

    credentials = {
        "client_id": registered["client_id"],
        "client_secret": registered["client_secret"],
    }
    token = client.post(
        server["token_endpoint"],
        data={
            **credentials,
            "grant_type": "authorization_code",
            "code": callback["code"][0],
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.text
    tokens = token.json()

    # 5. The token opens an MCP session, with no redirect on the slashless URL.
    session = _initialize(client, resource, tokens["access_token"])
    assert session.status_code == 200, session.text
    assert _sse_json(session)["result"]["serverInfo"]["name"] == "raicentral"

    # 6. Refresh rotates, repeatedly, and sign-out ends it.
    def refresh(refresh_token):
        return client.post(
            server["token_endpoint"],
            data={
                **credentials,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

    first = refresh(tokens["refresh_token"])
    assert first.status_code == 200, first.text
    second = refresh(first.json()["refresh_token"])
    assert second.status_code == 200, second.text

    user = db_session.query(UserAccount).filter_by(email=account).one()
    auth_service.logout_all_sessions(db_session, user_id=user.user_id)
    assert refresh(second.json()["refresh_token"]).status_code == 400


def test_public_host_is_accepted_and_foreign_host_rejected(proxied, public_base):
    allowed = mcp_transport_security()

    assert urlsplit(public_base).netloc in allowed.allowed_hosts
    assert "https://rai.example.test" in allowed.allowed_origins
    assert "attacker.example" not in " ".join(allowed.allowed_hosts)


async def test_tool_calls_ignore_the_public_path_prefix(monkeypatch):
    """The in-process management call must not repeat the proxy's /api prefix."""
    seen = FastAPI()

    @seen.get("/v1/ping")
    def ping():
        return {"pong": True}

    previous_app = api_client._gateway_app
    monkeypatch.setattr(
        settings, "mcp_server_public_base_url", "https://rai.example.test/api"
    )
    monkeypatch.setattr(api_client, "_require_token", lambda: "token")
    api_client.bind_gateway_app(seen)
    try:
        result = await api_client.management_api_request("GET", "/v1/ping")
    finally:
        api_client.bind_gateway_app(previous_app)

    assert result == {"error": False, "status_code": 200, "data": {"pong": True}}


@pytest.mark.parametrize(
    "api,mcp,expected",
    [
        ("", "", "http://127.0.0.1:8001"),
        (
            "https://rai.example.test/api/",
            "",
            "https://rai.example.test/api",
        ),
        ("http://localhost:8001", "", "http://localhost:8001"),
        # Not usable as an OAuth issuer: keep the safe default instead of crashing.
        ("http://192.168.1.20:8001", "", "http://127.0.0.1:8001"),
        ("/api", "", "http://127.0.0.1:8001"),
        (
            "https://rai.example.test/api",
            "https://mcp.example.test",
            "https://mcp.example.test",
        ),
    ],
)
def test_mcp_public_url_resolution(api, mcp, expected):
    resolved = Settings(
        _env_file=None, api_public_base_url=api, mcp_server_public_base_url=mcp
    )

    assert resolved.mcp_server_public_base_url == expected
