"""Mount the RAI management MCP server on the FastAPI gateway."""

from __future__ import annotations

import logging
from typing import Any
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.auth.routes import (
    build_resource_metadata_url,
    create_protected_resource_routes,
)
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl
from starlette.requests import Request

from fastapi import FastAPI

from rai_gateway.core.config import settings
from rai_gateway.mcp.api_client import bind_gateway_app
from rai_gateway.mcp.login_routes import register_mcp_login_routes
from rai_gateway.mcp.oauth_provider import RaiMcpOAuthProvider
from rai_gateway.mcp.tools import register_tools

logger = logging.getLogger(__name__)


def mcp_resource_url() -> str:
    base = settings.mcp_server_public_base_url.rstrip("/")
    path = settings.mcp_server_mount_path.rstrip("/")
    return f"{base}{path}"


def integrate_management_mcp(app: FastAPI) -> None:
    """Attach Streamable HTTP MCP, OAuth, and governance tools to the gateway."""
    if not settings.mcp_server_enabled:
        logger.info("mcp_server_disabled")
        return

    bind_gateway_app(app)
    resource_url = mcp_resource_url()
    issuer_url = AnyHttpUrl(resource_url)
    provider = RaiMcpOAuthProvider(issuer_base=resource_url, resource_url=resource_url)

    scopes = [s.strip() for s in settings.mcp_oauth_scopes.split(",") if s.strip()]
    auth = AuthSettings(
        issuer_url=issuer_url,
        resource_server_url=issuer_url,
        required_scopes=scopes,
        validate_token_resource=False,
        client_registration_options=ClientRegistrationOptions(
            enabled=settings.mcp_oauth_enable_dcr,
            valid_scopes=scopes,
            default_scopes=scopes,
        ),
    )

    server = MCPServer(
        "raicentral",
        instructions=(
            "RAI Control Plane management MCP. Use OAuth to obtain a token, then "
            "call tools to manage AI systems, agents, versions, runtime identities, "
            "credentials, delegations, resource catalog, RAI rules, trajectories, "
            "audit explorer, findings, and monitoring."
        ),
        version="0.1.0",
        auth=auth,
        auth_server_provider=provider,
    )
    register_tools(server)

    mcp_starlette = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=False,
        host="127.0.0.1",
    )
    mount_path = settings.mcp_server_mount_path.rstrip("/") or "/mcp"
    app.mount(mount_path, mcp_starlette)

    login_router = __import__("fastapi", fromlist=["APIRouter"]).APIRouter()
    register_mcp_login_routes(login_router, provider)
    app.include_router(login_router)

    protected_routes = create_protected_resource_routes(
        resource_url=issuer_url,
        authorization_servers=[issuer_url],
        scopes_supported=scopes,
        resource_name="RAI Control Plane",
    )
    for route in protected_routes:
        path = route.path
        handler = route.endpoint

        async def protected_metadata(
            request: Request,
            _handler: Any = handler,
        ):
            return await _handler(request)

        app.add_api_route(
            path,
            protected_metadata,
            methods=list(route.methods or ["GET"]),
            include_in_schema=False,
        )

    session_manager = server.session_manager

    @app.on_event("startup")
    async def _mcp_session_manager_start() -> None:
        app.state._mcp_run = session_manager.run()
        await app.state._mcp_run.__aenter__()

    @app.on_event("shutdown")
    async def _mcp_session_manager_stop() -> None:
        run = getattr(app.state, "_mcp_run", None)
        if run is not None:
            await run.__aexit__(None, None, None)

    metadata_url = build_resource_metadata_url(issuer_url)
    logger.info(
        "mcp_server_mounted mount=%s resource=%s protected_metadata=%s oauth_metadata=%s",
        mount_path,
        resource_url,
        metadata_url,
        f"{resource_url}/.well-known/oauth-authorization-server",
    )