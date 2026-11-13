"""Provider proxy core: govern first, then forward provider-native traffic.

Pipeline::

    ProxyRequest -> adapter (governance metadata only) -> RuntimeGateway
        -> DENY / REQUIRE_APPROVAL  => gateway error, NO upstream call
        -> ALLOW                    => UpstreamTransport (gateway credentials)
                                       -> provider-native response
                                       -> receipt finalized from the real outcome

Transport (this module) never contains policy logic; policy lives behind
``RuntimeGateway``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from rai_gateway.core.config import settings
from rai_gateway.core.observability import generate_trace_id
from rai_gateway.models.registry import ModelResource
from rai_gateway.provider_gateway import errors
from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.auth import (
    ProviderCredentialResolver,
    ProviderCredentialUnavailable,
    SettingsCredentialResolver,
    extract_runtime_secret,
)
from rai_gateway.provider_gateway.domain import (
    ProviderCredential,
    ProxyRequest,
    ProxyResponse,
    UsageInfo,
)
from rai_gateway.provider_gateway.metering import build_execution_metadata
from rai_gateway.provider_gateway.streaming import relay_stream
from rai_gateway.provider_gateway.transport import UpstreamTransport
from rai_gateway.runtime_enforcement.domain import (
    MODEL_PROVIDER_INTEGRATION,
    ExecutionStatus,
)
from rai_gateway.runtime_enforcement.gateway import RuntimeGateway
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayDecision,
    GatewayEnforcementMode,
)

logger = logging.getLogger(__name__)

SessionScope = Callable[[], AbstractContextManager[Session]]


@contextmanager
def default_session_scope():
    from rai_gateway.core.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def resolve_model_id(db: Session, provider: str | None, model: str | None):
    """Map provider-native model names onto the registered ModelResource."""
    if not provider or not model:
        return None
    rows = (
        db.query(ModelResource)
        .filter(ModelResource.status == "ACTIVE")
        .filter(ModelResource.provider.ilike(provider))
        .all()
    )
    wanted = model.lower()
    for row in rows:
        if (row.model_name or "").lower() == wanted:
            return row.id
    for row in rows:
        if (row.deployment_reference or "").lower() == wanted:
            return row.id
    return None


class AdapterRegistry:
    def __init__(self, adapters: list[ProviderProtocolAdapter] | None = None) -> None:
        self._adapters = list(adapters or [])

    def register(self, adapter: ProviderProtocolAdapter) -> None:
        self._adapters.append(adapter)

    def resolve(self, request: ProxyRequest) -> ProviderProtocolAdapter | None:
        for adapter in self._adapters:
            if adapter.matches_request(request):
                return adapter
        return None


def error_response(
    exc: errors.ProviderGatewayError, *, receipt_id: str | None = None
) -> ProxyResponse:
    return ProxyResponse(
        status_code=exc.status_code,
        headers={"content-type": "application/json", **exc.headers},
        body=json.dumps(exc.body()).encode("utf-8"),
        gateway_generated=True,
        receipt_id=receipt_id or exc.rai.get("receipt_id"),
    )


class ProviderProxy:
    def __init__(
        self,
        adapters: AdapterRegistry,
        *,
        transport: UpstreamTransport | None = None,
        credentials: ProviderCredentialResolver | None = None,
        mode: GatewayEnforcementMode | str | None = None,
        session_scope: SessionScope | None = None,
        max_body_bytes: int | None = None,
    ) -> None:
        self._adapters = adapters
        self._transport = transport or UpstreamTransport()
        self._credentials = credentials or SettingsCredentialResolver()
        self._mode = mode
        self._session_scope = session_scope or default_session_scope
        self._max_body = max_body_bytes or settings.provider_gateway_max_body_bytes

    async def handle(self, request: ProxyRequest, *, db: Session) -> ProxyResponse:
        try:
            return await self._handle(request, db)
        except errors.ProviderGatewayError as exc:
            return error_response(exc)

    async def _handle(self, request: ProxyRequest, db: Session) -> ProxyResponse:
        secret = extract_runtime_secret(request.headers)
        if not secret:
            raise errors.missing_identity()
        if len(request.body) > self._max_body:
            raise errors.body_too_large(self._max_body)

        adapter = self._adapters.resolve(request)
        if adapter is None:
            raise errors.no_adapter(request.path)

        action = self._build_action(adapter, request, db)
        try:
            credential = self._credentials.resolve(adapter.provider_name)
        except ProviderCredentialUnavailable as exc:
            raise errors.credential_unavailable(exc.provider) from exc

        gateway = RuntimeGateway(db, mode=self._mode)
        decision = await run_in_threadpool(
            gateway.authorize_for_execution, action, credential_secret=secret
        )
        if not decision.allowed:
            raise _with_receipt(errors.from_decision(decision), decision)

        return await self._forward(adapter, request, credential, action, decision)

    def _build_action(
        self, adapter: ProviderProtocolAdapter, request: ProxyRequest, db: Session
    ) -> GatewayAction:
        action = adapter.extract_governance_action(request)
        digest = request.body_sha256()
        action.integration_type = MODEL_PROVIDER_INTEGRATION
        action.provider = action.provider or adapter.provider_name
        action.input_metadata = {
            **action.input_metadata,
            "body_sha256": digest,
            "body_bytes": len(request.body),
        }
        # Binds any approval to the exact request body without storing it.
        action.parameters = {
            **action.parameters,
            "body_sha256": digest,
            "stream": adapter.is_streaming(request),
            "operation": action.operation,
        }
        action.model_id = resolve_model_id(db, action.provider, action.model)
        action.correlation_id = action.correlation_id or (
            request.headers.get("x-request-id") or generate_trace_id()
        )
        return action

    async def _forward(
        self,
        adapter: ProviderProtocolAdapter,
        request: ProxyRequest,
        credential: ProviderCredential,
        action: GatewayAction,
        decision: GatewayDecision,
    ) -> ProxyResponse:
        url = adapter.upstream_url(request, credential)
        headers = adapter.upstream_headers(request, credential)
        params = dict(credential.query_params) or None
        streaming = adapter.is_streaming(request)
        started = time.monotonic()

        def meta(**kw: Any) -> dict[str, Any]:
            return build_execution_metadata(
                provider=adapter.provider_name,
                operation=action.operation,
                model=action.model,
                body_sha256=request.body_sha256(),
                request_bytes=len(request.body),
                streamed=streaming,
                **kw,
            )

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            if not streaming:
                result = await self._transport.send(
                    request.method, url, headers, request.body, params
                )
                usage = _usage_from_body(adapter, result.body)
                ok = result.status_code < 400
                await self._finalize(
                    decision,
                    succeeded=ok,
                    duration_ms=elapsed(),
                    meta=meta(
                        upstream_status=result.status_code,
                        response_bytes=len(result.body),
                        outcome="completed" if ok else "upstream_error",
                        usage=usage if ok else None,
                    ),
                )
                return ProxyResponse(
                    status_code=result.status_code,
                    headers=adapter.sanitize_response_metadata(result.headers),
                    body=result.body,
                    receipt_id=_rid(decision),
                )

            upstream = await self._transport.open_stream(
                request.method, url, headers, request.body, params
            )
        except errors.ProviderGatewayError as exc:
            await self._finalize(
                decision,
                succeeded=False,
                duration_ms=elapsed(),
                meta=meta(
                    upstream_status=None,
                    response_bytes=None,
                    outcome="transport_error",
                    usage=None,
                    error=exc.code,
                ),
            )
            raise _with_receipt(exc, decision) from exc
        except Exception as exc:  # never leave a receipt dangling in STARTED
            logger.exception("Unexpected provider proxy failure")
            await self._finalize(
                decision,
                succeeded=False,
                duration_ms=elapsed(),
                meta=meta(
                    upstream_status=None,
                    response_bytes=None,
                    outcome="gateway_error",
                    usage=None,
                    error=type(exc).__name__,
                ),
            )
            raise _with_receipt(
                errors.upstream_unreachable("Gateway failed while proxying"), decision
            ) from exc

        if upstream.status_code >= 400:
            # Upstream refused before streaming: relay the error body as-is.
            try:
                body = await upstream.aread()
            finally:
                await upstream.aclose()
            await self._finalize(
                decision,
                succeeded=False,
                duration_ms=elapsed(),
                meta=meta(
                    upstream_status=upstream.status_code,
                    response_bytes=len(body),
                    outcome="upstream_error",
                    usage=None,
                ),
            )
            return ProxyResponse(
                status_code=upstream.status_code,
                headers=adapter.sanitize_response_metadata(dict(upstream.headers)),
                body=body,
                receipt_id=_rid(decision),
            )

        collector = adapter.new_stream_usage_collector()

        async def on_done(
            outcome: str, usage: UsageInfo | None, streamed: int, duration_ms: int
        ) -> None:
            completed = outcome == "completed"
            await self._finalize(
                decision,
                succeeded=completed,
                duration_ms=duration_ms,
                meta=meta(
                    upstream_status=upstream.status_code,
                    response_bytes=streamed,
                    outcome=outcome,
                    usage=usage if completed else None,
                ),
                execution_status=None
                if completed
                else (
                    ExecutionStatus.UNKNOWN.value
                    if outcome == "cancelled"
                    else ExecutionStatus.FAILED.value
                ),
            )

        return ProxyResponse(
            status_code=upstream.status_code,
            headers=adapter.sanitize_response_metadata(dict(upstream.headers)),
            stream=relay_stream(upstream, collector, on_done),
            receipt_id=_rid(decision),
        )

    async def _finalize(
        self,
        decision: GatewayDecision,
        *,
        succeeded: bool,
        duration_ms: int,
        meta: dict[str, Any],
        execution_status: str | None = None,
    ) -> None:
        if decision.receipt_id is None:
            return

        def work() -> None:
            with self._session_scope() as session:
                RuntimeGateway(session, mode=self._mode).finalize_execution(
                    decision.receipt_id,
                    succeeded=succeeded,
                    duration_ms=duration_ms,
                    safe_result_metadata=meta,
                    execution_status=execution_status,
                )

        try:
            await run_in_threadpool(work)
        except Exception:  # finalization failure must not corrupt the response
            logger.exception(
                "Failed to finalize gateway receipt %s", decision.receipt_id
            )

    async def aclose(self) -> None:
        await self._transport.aclose()


def _rid(decision: GatewayDecision) -> str | None:
    return str(decision.receipt_id) if decision.receipt_id else None


def _with_receipt(
    exc: errors.ProviderGatewayError, decision: GatewayDecision
) -> errors.ProviderGatewayError:
    if decision.receipt_id and "receipt_id" not in exc.rai:
        exc.rai["receipt_id"] = str(decision.receipt_id)
    return exc


def _usage_from_body(adapter: ProviderProtocolAdapter, body: bytes) -> UsageInfo | None:
    try:
        parsed = json.loads(body) if body else None
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return adapter.extract_usage(parsed)
    except Exception:
        return None
