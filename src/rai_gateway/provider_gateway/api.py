"""HTTP glue for provider-compatible routes.

Concrete routes (OpenAI-compatible, ...) arrive in later steps; they call
:func:`handle_provider_request` with their adapter registry. This module does
no governance itself.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.domain import ProxyRequest, ProxyResponse
from rai_gateway.provider_gateway.errors import body_too_large
from rai_gateway.provider_gateway.proxy import ProviderProxy, error_response


async def read_bounded_body(request: Request, limit: int | None = None) -> bytes:
    """Read the request body, rejecting oversize payloads without buffering them."""
    cap = limit or settings.provider_gateway_max_body_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise body_too_large(cap)
    size = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise body_too_large(cap)
        chunks.append(chunk)
    return b"".join(chunks)


def to_starlette(response: ProxyResponse) -> Response:
    headers = dict(response.headers)
    if response.receipt_id:
        headers["x-rai-receipt-id"] = response.receipt_id
    if response.stream is not None:
        return StreamingResponse(
            response.stream, status_code=response.status_code, headers=headers
        )
    return Response(
        content=response.body or b"", status_code=response.status_code, headers=headers
    )


async def handle_provider_request(
    request: Request, *, proxy: ProviderProxy, db: Session
) -> Response:
    from rai_gateway.provider_gateway.production.lifecycle import gateway_lifecycle
    from rai_gateway.provider_gateway.production.limits import gateway_limits

    if not gateway_lifecycle.accept_new_requests():
        from rai_gateway.provider_gateway import errors

        return to_starlette(
            error_response(
                errors.ProviderGatewayError(
                    503,
                    "GATEWAY_DRAINING",
                    "Gateway is draining connections",
                )
            )
        )
    gateway_lifecycle.begin_request()
    try:
        limit = gateway_limits.check_headers(dict(request.headers))
        gateway_limits.raise_if_denied(limit)
        secret = request.headers.get("x-rai-agent-key") or ""
        if secret:
            rate = gateway_limits.check_rate(secret[:16])
            gateway_limits.raise_if_denied(rate)
    except Exception as exc:
        gateway_lifecycle.end_request()
        from rai_gateway.provider_gateway.errors import ProviderGatewayError

        if isinstance(exc, ProviderGatewayError):
            return to_starlette(error_response(exc))
        raise
    try:
        try:
            body = await read_bounded_body(request)
        except Exception as exc:  # ProviderGatewayError only; anything else bubbles
            from rai_gateway.provider_gateway.errors import ProviderGatewayError

            if isinstance(exc, ProviderGatewayError):
                return to_starlette(error_response(exc))
            raise
        proxy_request = ProxyRequest(
            method=request.method,
            path=request.url.path,
            query=request.url.query,
            headers=dict(request.headers),
            body=body,
            client_host=request.client.host if request.client else None,
        )
        return to_starlette(await proxy.handle(proxy_request, db=db))
    finally:
        gateway_lifecycle.end_request()
