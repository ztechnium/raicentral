"""Behaviour behind a reverse proxy that publishes the API under a path prefix."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from rai_gateway.cli.admin import create_admin_user
from rai_gateway.client_hooks import service as hooks_module
from rai_gateway.client_hooks.service import ClientHookService
from rai_gateway.core.config import Settings, settings

PASSWORD = "Correct-Horse-Battery-9"


@pytest.mark.parametrize(
    "url,prefix",
    [
        ("", ""),
        ("https://api.example.test", ""),
        ("https://rai.example.test/api", "/api"),
        ("https://rai.example.test/platform/api/", "/platform/api"),
        ("/api", "/api"),
    ],
)
def test_path_prefix_follows_the_public_url(url, prefix):
    assert (
        Settings(_env_file=None, api_public_base_url=url).api_public_path_prefix
        == prefix
    )


@pytest.mark.parametrize(
    "public_url,prefix",
    [("", ""), ("https://rai.example.test/api", "/api")],
)
def test_api_docs_use_the_public_prefix(test_client, monkeypatch, public_url, prefix):
    monkeypatch.setattr(settings, "api_public_base_url", public_url)

    docs = test_client.get("/docs")
    schema = test_client.get("/openapi.json")

    assert docs.status_code == 200
    assert f"url: '{prefix}/openapi.json'" in docs.text
    assert schema.status_code == 200
    servers = schema.json().get("servers", [])
    assert servers == ([{"url": prefix}] if prefix else [])
    # The proxy strips the prefix, so ordinary routes are unaffected.
    assert test_client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "public_url,cookie_path",
    [("", "/v1/auth"), ("https://rai.example.test/api", "/api/v1/auth")],
)
def test_cookie_session_works_under_the_public_prefix(
    test_client, db_session, monkeypatch, public_url, cookie_path
):
    monkeypatch.setattr(settings, "api_public_base_url", public_url)
    monkeypatch.setattr(settings, "auth_set_cookies", True)
    monkeypatch.setattr(settings, "auth_cookie_secure", True)
    email = f"cookie-{uuid.uuid4().hex[:8]}@example.com"
    create_admin_user(
        db_session, email=email, password=PASSWORD, name="Cookie", role="admin"
    )

    login = test_client.post(
        "/v1/auth/login", json={"email": email, "password": PASSWORD}
    )

    assert login.status_code == 200, login.text
    cookies = {
        header.split("=", 1)[0]: header.lower()
        for header in login.headers.get_list("set-cookie")
    }
    assert f"path={cookie_path};" in cookies["rai_refresh"] + ";"
    assert "path=/;" in cookies["rai_access"] + ";"
    for name in ("rai_access", "rai_refresh"):
        assert "httponly" in cookies[name]
        assert "secure" in cookies[name]
        assert "samesite=lax" in cookies[name]
        assert "domain=" not in cookies[name]


@pytest.mark.parametrize(
    "public_url,expected",
    [
        ("", "http://localhost:8001"),
        ("/api", "http://localhost:8001"),
        ("https://rai.example.test/api", "https://rai.example.test/api"),
    ],
)
def test_client_integration_setup_advertises_the_public_url(
    monkeypatch, public_url, expected
):
    monkeypatch.setattr(settings, "api_public_base_url", public_url)
    monkeypatch.setattr(hooks_module, "log_admin_audit", lambda **_kwargs: None)
    service = ClientHookService(MagicMock())
    service._integrations = MagicMock()
    service._integrations.upsert.return_value = SimpleNamespace(
        id=uuid.uuid4(), enforcement_mode="ENFORCE", status="ACTIVE", coverage={}
    )

    setup = service.generate_setup_config(uuid.uuid4(), "cursor")

    assert setup["environment"]["RAI_BASE_URL"] == expected
