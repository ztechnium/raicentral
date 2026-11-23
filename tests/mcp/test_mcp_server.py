"""Tests for RAI management MCP wiring."""

from __future__ import annotations

import pytest
from mcp.server.auth.routes import build_resource_metadata_url
from pydantic import AnyHttpUrl

from rai_gateway.core.config import settings
from rai_gateway.mcp.integration import mcp_resource_url
from rai_gateway.mcp.oauth_provider import RaiMcpOAuthProvider
from rai_gateway.services.llm_controller import app


def test_mcp_resource_url_uses_config() -> None:
    base = settings.mcp_server_public_base_url.rstrip("/")
    path = settings.mcp_server_mount_path.rstrip("/")
    assert mcp_resource_url() == f"{base}{path}"


def test_fastapi_app_mounts_mcp() -> None:
    mount_paths = [getattr(r, "path", "") for r in app.routes]
    assert settings.mcp_server_mount_path.rstrip("/") in mount_paths


def test_protected_resource_metadata_path() -> None:
    issuer = AnyHttpUrl(mcp_resource_url())
    metadata = build_resource_metadata_url(issuer)
    assert "/.well-known/oauth-protected-resource" in str(metadata)


@pytest.mark.asyncio
async def test_oauth_provider_registers_client() -> None:
    provider = RaiMcpOAuthProvider(
        issuer_base="http://127.0.0.1:8001/mcp",
        resource_url="http://127.0.0.1:8001/mcp",
    )
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    client = OAuthClientInformationFull(
        client_id="cursor-test",
        redirect_uris=[AnyUrl("http://127.0.0.1:9876/callback")],
    )
    await provider.register_client(client)
    loaded = await provider.get_client("cursor-test")
    assert loaded is not None
    assert loaded.client_secret is not None
