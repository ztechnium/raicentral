"""Upstream HTTP transport (httpx.AsyncClient with pooling and safe retries).

Retry policy: only when *nothing was processed upstream* - connection
failures before the request was sent, or an explicit 429/503 response. Never
retried: policy rejections (they never reach here), any 4xx other than 429,
and anything after a streaming response has begun.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway import errors
from rai_gateway.provider_gateway.production.upstream_guard import (
    assert_resolved_addresses_match_pins,
)

_RETRY_STATUS = frozenset({429, 503})
_BACKOFF_SECONDS = 0.2


@dataclass
class UpstreamResult:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class UpstreamTransport:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        max_retries: int | None = None,
        max_response_bytes: int | None = None,
        backoff_seconds: float = _BACKOFF_SECONDS,
    ) -> None:
        verify: bool | str = settings.provider_gateway_verify_tls
        if settings.provider_gateway_ca_bundle_path:
            verify = settings.provider_gateway_ca_bundle_path
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.provider_gateway_connect_timeout_seconds,
                read=settings.provider_gateway_read_timeout_seconds,
                write=30.0,
                pool=10.0,
            ),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=False,
            verify=verify,
        )
        self._max_retries = (
            settings.provider_gateway_max_retries
            if max_retries is None
            else max_retries
        )
        self._max_response_bytes = (
            max_response_bytes or settings.provider_gateway_max_response_bytes
        )
        self._backoff = backoff_seconds

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _open(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
        params: Mapping[str, str] | None,
        *,
        pinned_addresses: tuple[str, ...] | None = None,
    ) -> httpx.Response:
        """Open a streamed response, retrying only before any bytes are consumed."""
        attempt = 0
        if pinned_addresses:
            from urllib.parse import urlparse

            host = urlparse(url).hostname
            if host:
                assert_resolved_addresses_match_pins(host, pinned_addresses)
        while True:
            request = self._client.build_request(
                method, url, headers=dict(headers), content=content, params=params
            )
            try:
                response = await self._client.send(request, stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt < self._max_retries:
                    attempt += 1
                    await asyncio.sleep(self._backoff * attempt)
                    continue
                raise errors.upstream_unreachable(
                    f"Cannot connect to upstream: {type(exc).__name__}"
                ) from exc
            except httpx.TimeoutException as exc:
                raise errors.upstream_timeout() from exc
            except httpx.HTTPError as exc:
                raise errors.upstream_unreachable(
                    f"Upstream transport error: {type(exc).__name__}"
                ) from exc
            if response.status_code in _RETRY_STATUS and attempt < self._max_retries:
                await response.aclose()
                attempt += 1
                await asyncio.sleep(self._backoff * attempt)
                continue
            return response

    async def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
        params: Mapping[str, str] | None = None,
        *,
        pinned_addresses: tuple[str, ...] | None = None,
    ) -> UpstreamResult:
        """Buffered request/response with a bounded response size."""
        response = await self._open(
            method,
            url,
            headers,
            content,
            params,
            pinned_addresses=pinned_addresses,
        )
        try:
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self._max_response_bytes:
                    raise errors.upstream_unreachable(
                        "Upstream response exceeds gateway size limit"
                    )
                chunks.append(chunk)
            return UpstreamResult(
                response.status_code, dict(response.headers), b"".join(chunks)
            )
        except httpx.TimeoutException as exc:
            raise errors.upstream_timeout() from exc
        except httpx.HTTPError as exc:
            raise errors.upstream_unreachable(
                f"Upstream transport error: {type(exc).__name__}"
            ) from exc
        finally:
            await response.aclose()

    async def open_stream(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
        params: Mapping[str, str] | None = None,
        *,
        pinned_addresses: tuple[str, ...] | None = None,
    ) -> httpx.Response:
        """Open a streaming response. Caller owns closing it."""
        return await self._open(
            method,
            url,
            headers,
            content,
            params,
            pinned_addresses=pinned_addresses,
        )
