"""Call the management API with the MCP caller's OAuth access token."""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token

from rai_gateway.core.config import settings

_gateway_app: Any | None = None


def bind_gateway_app(app: Any) -> None:
    global _gateway_app
    _gateway_app = app


def _require_token() -> str:
    access = get_access_token()
    if access is None or not access.token:
        raise PermissionError("MCP request is not authenticated")
    return access.token


async def management_api_request(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Proxy a management API call using the authenticated MCP bearer token."""
    if _gateway_app is None:
        raise RuntimeError("MCP gateway app is not bound")

    token = _require_token()
    normalized_path = path if path.startswith("/") else f"/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_gateway_app),  # type: ignore[arg-type]
        base_url=settings.mcp_server_public_base_url,
    ) as client:
        response = await client.request(
            method.upper(),
            normalized_path,
            params=query,
            json=body,
            headers=headers,
        )

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        payload: Any = response.json()
    elif response.status_code == 204:
        payload = {"status_code": 204}
    else:
        payload = {"status_code": response.status_code, "body": response.text}

    if response.status_code >= 400:
        return {
            "error": True,
            "status_code": response.status_code,
            "detail": payload,
        }
    return {"error": False, "status_code": response.status_code, "data": payload}


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, default=str)