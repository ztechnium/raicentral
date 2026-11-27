"""Behaviour that depends on the API's externally visible URL."""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from rai_gateway.core.config import settings

_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


class PublicDocsPrefixMiddleware:
    """Let the API docs resolve behind a proxy that strips a path prefix.

    Only the documentation routes get ``root_path``: Swagger UI then fetches the
    schema and sends "Try it out" requests under the public prefix. Setting it for
    every request would change how mounted applications are routed.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        prefix = settings.api_public_path_prefix
        if prefix and scope["type"] == "http" and scope["path"] in _DOCS_PATHS:
            scope = {**scope, "root_path": prefix}
        await self.app(scope, receive, send)
