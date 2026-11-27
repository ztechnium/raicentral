"""Mount the RAI management MCP server on the FastAPI gateway."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.routes import (
    build_metadata,
    build_resource_metadata_url,
    cors_middleware,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from rai_gateway.core.config import settings
from rai_gateway.mcp.api_client import bind_gateway_app
from rai_gateway.mcp.login_routes import register_mcp_login_routes
from rai_gateway.mcp.oauth_provider import RaiMcpOAuthProvider
from rai_gateway.mcp.tools import register_tools

logger = logging.getLogger(__name__)

_DEFAULT_PORTS = {"http": 80, "https": 443}


def mcp_resource_url() -> str:
    base = settings.mcp_server_public_base_url.rstrip("/")
    path = settings.mcp_server_mount_path.rstrip("/")
    return f"{base}{path}"


def mcp_transport_security() -> TransportSecuritySettings:
    """DNS rebinding protection that admits loopback and the configured public host.

    A reverse proxy that preserves ``Host`` presents the public name, which the
    SDK's loopback-only default would reject with 421.
    """
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]

    public = urlsplit(settings.mcp_server_public_base_url)
    netloc = public.netloc.lower()
    hosts.append(netloc)
    default_port = _DEFAULT_PORTS.get(public.scheme)
    if public.hostname and public.port in (None, default_port):
        bare = netloc.removesuffix(f":{default_port}")
        hosts.extend([bare, f"{bare}:{default_port}"])

    for url in (settings.mcp_server_public_base_url, settings.app_public_url):
        parsed = urlsplit(url)
        if parsed.scheme and parsed.netloc:
            origins.append(f"{parsed.scheme}://{parsed.netloc}".lower())

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


class _ServeMountWithoutSlash:
    """Serve ``/mcp`` directly.

    Starlette would otherwise redirect to ``/mcp/`` with an absolute URL built from
    the internal request, which drops the proxy prefix (and may downgrade to http).
    """

    def __init__(self, app: ASGIApp, mount_path: str) -> None:
        self.app = app
        self.mount_path = mount_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == self.mount_path:
            slashed = f"{self.mount_path}/"
            scope = {**scope, "path": slashed, "raw_path": slashed.encode()}
        await self.app(scope, receive, send)


def integrate_management_mcp(app: FastAPI) -> None:
    """Attach Streamable HTTP MCP, OAuth, and governance tools to the gateway."""
    if not settings.mcp_server_enabled:
        logger.info("mcp_server_disabled")
        return

    bind_gateway_app(app)
    public_base = settings.mcp_server_public_base_url.rstrip("/")
    resource_url = mcp_resource_url()
    issuer_url = AnyHttpUrl(resource_url)
    provider = RaiMcpOAuthProvider(issuer_base=public_base, resource_url=resource_url)

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
        transport_security=mcp_transport_security(),
    )
    mount_path = settings.mcp_server_mount_path.rstrip("/") or "/mcp"

    # Registered before the mount, which would otherwise capture the login path.
    login_router = APIRouter()
    register_mcp_login_routes(login_router, provider)
    app.include_router(login_router)

    app.mount(mount_path, mcp_starlette)
    app.add_middleware(_ServeMountWithoutSlash, mount_path=mount_path)

    # RFC 9728 and RFC 8414 place metadata at the origin root with the public resource
    # path appended, so a proxy must forward these two well-known prefixes unchanged.
    app.router.routes.extend(
        create_protected_resource_routes(
            resource_url=issuer_url,
            authorization_servers=[issuer_url],
            scopes_supported=scopes,
            resource_name="RAI Control Plane",
        )
    )
    metadata = build_metadata(
        issuer_url, None, auth.client_registration_options, RevocationOptions()
    )
    app.router.routes.append(
        Route(
            "/.well-known/oauth-authorization-server"
            + urlsplit(str(issuer_url)).path.rstrip("/"),
            endpoint=cors_middleware(
                MetadataHandler(metadata).handle, ["GET", "OPTIONS"]
            ),
            methods=["GET", "OPTIONS"],
        )
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
