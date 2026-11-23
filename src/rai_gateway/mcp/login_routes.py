"""Browser login for MCP OAuth (resource-owner step)."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from mcp.server.auth.provider import AuthorizeError

from rai_gateway.mcp.oauth_provider import RaiMcpOAuthProvider

router = APIRouter(tags=["mcp-oauth"])


_LOGIN_FORM = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>RAI MCP sign-in</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 28rem; }}
    label {{ display: block; margin-top: 1rem; }}
    input {{ width: 100%; padding: 0.5rem; margin-top: 0.25rem; }}
    button {{ margin-top: 1.25rem; padding: 0.5rem 1rem; }}
    .error {{ color: #b00020; margin-top: 1rem; }}
  </style>
</head>
<body>
  <h1>Sign in to RAI MCP</h1>
  <p>Authorize your MCP client to manage this RAI organization.</p>
  {error_block}
  <form method="post" action="{action}">
    <input type="hidden" name="pending_id" value="{pending_id}" />
    <label>Email<input name="email" type="email" required autocomplete="username" /></label>
    <label>Password<input name="password" type="password" required autocomplete="current-password" /></label>
    <button type="submit">Continue</button>
  </form>
</body>
</html>
"""


def register_mcp_login_routes(
    app_router: APIRouter, provider: RaiMcpOAuthProvider
) -> None:
    mount = provider.login_path()

    @app_router.get(mount, response_class=HTMLResponse, include_in_schema=False)
    async def mcp_login_page(request: Request, pending_id: str) -> HTMLResponse:
        if pending_id not in provider._pending:
            return HTMLResponse(
                _LOGIN_FORM.format(
                    action=mount,
                    pending_id=escape(pending_id, quote=True),
                    error_block='<p class="error">Authorization request expired. Restart MCP OAuth.</p>',
                ),
                status_code=400,
            )
        return HTMLResponse(
            _LOGIN_FORM.format(
                action=mount,
                pending_id=escape(pending_id, quote=True),
                error_block="",
            )
        )

    @app_router.post(mount, include_in_schema=False, response_model=None)
    async def mcp_login_submit(
        request: Request,
        pending_id: str = Form(...),
        email: str = Form(...),
        password: str = Form(...),
    ) -> RedirectResponse | HTMLResponse:
        client = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
        try:
            redirect = provider.complete_password_login(
                pending_id=pending_id,
                email=email,
                password=password,
                ip_address=client,
                user_agent=ua,
            )
            return RedirectResponse(url=redirect, status_code=302)
        except AuthorizeError as exc:
            return HTMLResponse(
                _LOGIN_FORM.format(
                    action=mount,
                    pending_id=escape(pending_id, quote=True),
                    error_block=f'<p class="error">{escape(exc.error_description or exc.error)}</p>',
                ),
                status_code=403,
            )
        except Exception:
            return HTMLResponse(
                _LOGIN_FORM.format(
                    action=mount,
                    pending_id=escape(pending_id, quote=True),
                    error_block='<p class="error">Invalid email or password.</p>',
                ),
                status_code=401,
            )