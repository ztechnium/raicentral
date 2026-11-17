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
    CredentialResolveContext,
    ProviderCredentialResolver,
    ProviderCredentialUnavailable,
    SettingsCredentialResolver,
    extract_runtime_secret,
)
from rai_gateway.provider_gateway.credentials.ownership import (
    ProviderCredentialScopeDenied,
)
from rai_gateway.provider_gateway.domain import (
    ProviderCredential,
    ProxyRequest,
    ProxyResponse,
    UsageInfo,
)
from rai_gateway.provider_gateway.metering import build_execution_metadata
from rai_gateway.provider_gateway.streaming import StreamLifecycle, relay_stream
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


def _release_stream_slot(stream_slot: tuple[str | None, str] | None) -> None:
    if stream_slot is None:
        return
    from rai_gateway.provider_gateway.production.limits import gateway_limits

    runtime_key, provider = stream_slot
    gateway_limits.release_stream(runtime_key=runtime_key, provider=provider)


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
        # receipt_id -> [(content_type, evaluation, blocked)] awaiting trajectory recording
        self._pending_evals: dict[Any, list[tuple[str, dict[str, Any], bool]]] = {}
        self._bg_tasks: set[Any] = set()

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

        policy_started = time.monotonic()
        gateway = RuntimeGateway(db, mode=self._mode)
        decision = await run_in_threadpool(
            gateway.authorize_for_execution, action, credential_secret=secret
        )
        policy_ms = int((time.monotonic() - policy_started) * 1000)
        if not decision.allowed:
            from rai_gateway.provider_gateway.production.metrics import gateway_metrics

            gateway_metrics.record_request(
                policy_decision_ms=policy_ms,
                gateway_overhead_ms=policy_ms,
                error=True,
            )
            raise _with_receipt(errors.from_decision(decision), decision)

        await self._enforce_input_content(adapter, request, action, decision, policy_ms)

        streaming = adapter.is_streaming(request)
        runtime_key = secret[:16] if secret else None
        stream_slot = (
            (runtime_key, adapter.provider_name) if streaming else None
        )
        if streaming:
            from rai_gateway.provider_gateway.production.limits import gateway_limits

            slot = gateway_limits.acquire_stream(
                runtime_key=runtime_key, provider=adapter.provider_name
            )
            gateway_limits.raise_if_denied(slot)

        runtime_identity_id = None
        if decision.enforcement_result:
            runtime_identity_id = decision.enforcement_result.runtime_identity_id

        context = CredentialResolveContext(
            db=db,
            runtime_identity_id=runtime_identity_id,
            environment=action.environment,
            model=action.model,
        )
        try:
            credential = self._credentials.resolve(
                adapter.provider_name, context=context
            )
        except ProviderCredentialScopeDenied as exc:
            _release_stream_slot(stream_slot)
            raise errors.credential_scope_denied(exc.reason, decision=decision) from exc
        except ProviderCredentialUnavailable as exc:
            _release_stream_slot(stream_slot)
            raise errors.credential_unavailable(exc.provider, decision=decision) from exc

        defer_stream_release = False
        try:
            response = await self._forward(
                adapter,
                request,
                credential,
                action,
                decision,
                db,
                policy_ms=policy_ms,
                runtime_key=runtime_key,
                streaming=streaming,
            )
            if streaming and response.stream is not None:
                defer_stream_release = True
            return response
        finally:
            if streaming and not defer_stream_release:
                from rai_gateway.provider_gateway.production.limits import (
                    gateway_limits,
                )

                gateway_limits.release_stream(
                    runtime_key=runtime_key, provider=adapter.provider_name
                )

    @staticmethod
    def _estimate_tokens(
        adapter: ProviderProtocolAdapter,
        request: ProxyRequest,
        action: GatewayAction,
    ) -> None:
        """Pre-call token estimate so TOKEN/COST budgets can be checked before the call.

        Input: ~4 characters per token of the prompt (request bytes when the adapter
        cannot read the prompt). Output is unknown before the call and is not reserved,
        unless PROVIDER_GATEWAY_RESERVE_MAX_OUTPUT_TOKENS is on (then the caller's
        max-output-tokens cap is reserved). The reservation is reconciled to the
        provider-reported usage afterwards, so the next call sees the real total.
        """
        from rai_gateway.provider_gateway.adapters.common import (
            load_json,
            max_output_tokens,
        )

        if action.estimated_input_tokens is None:
            try:
                prompt = adapter.extract_prompt_text(request) or ""
            except Exception:
                prompt = ""
            basis = len(prompt) if prompt else len(request.body)
            action.estimated_input_tokens = max(1, (basis + 3) // 4)
        if (
            action.estimated_output_tokens is None
            and settings.provider_gateway_reserve_max_output_tokens
        ):
            cap = max_output_tokens(load_json(request.body))
            if cap:
                action.estimated_output_tokens = cap

    def _build_action(
        self, adapter: ProviderProtocolAdapter, request: ProxyRequest, db: Session
    ) -> GatewayAction:
        action = adapter.extract_governance_action(request)
        self._estimate_tokens(adapter, request, action)
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
        db: Session,
        policy_ms: int = 0,
        *,
        runtime_key: str | None = None,
        streaming: bool = False,
    ) -> ProxyResponse:
        from rai_gateway.provider_gateway.production.finops import (
            attach_finops_metadata,
            estimate_for_gateway_execution,
        )
        from rai_gateway.provider_gateway.production.metrics import gateway_metrics
        from rai_gateway.provider_gateway.production.upstream_guard import (
            validate_upstream_url,
        )

        validated = validate_upstream_url(adapter.upstream_url(request, credential))
        url = validated.url
        pinned_addresses = validated.pinned_addresses
        headers = adapter.upstream_headers(request, credential)
        params = dict(credential.query_params) or None
        if not streaming:
            streaming = adapter.is_streaming(request)
        stream_slot = (
            (runtime_key, adapter.provider_name) if streaming else None
        )
        started = time.monotonic()
        pre_estimate = estimate_for_gateway_execution(
            db,
            provider=adapter.provider_name,
            model=action.model,
            request_bytes=len(request.body),
            streaming=streaming,
        )

        def meta(**kw: Any) -> dict[str, Any]:
            base = build_execution_metadata(
                provider=adapter.provider_name,
                operation=action.operation,
                model=action.model,
                body_sha256=request.body_sha256(),
                request_bytes=len(request.body),
                streamed=streaming,
                **kw,
            )
            if credential.credential_ref_id:
                base["provider_credential_ref_id"] = str(credential.credential_ref_id)
            return attach_finops_metadata(
                base,
                model=action.model,
                request_bytes=len(request.body),
                streaming=streaming,
                usage=kw.get("usage"),
                succeeded=kw.get("outcome") == "completed",
                pre_estimate=pre_estimate,
                decision=decision,
            )

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            if not streaming:
                result = await self._transport.send(
                    request.method,
                    url,
                    headers,
                    request.body,
                    params,
                    pinned_addresses=pinned_addresses,
                )
                usage = _usage_from_body(adapter, result.body)
                ok = result.status_code < 400
                provider_ms = elapsed()
                await self._finalize(
                    decision,
                    succeeded=ok,
                    duration_ms=provider_ms,
                    meta=meta(
                        upstream_status=result.status_code,
                        response_bytes=len(result.body),
                        outcome="completed" if ok else "upstream_error",
                        usage=usage if ok else None,
                    ),
                    content=self._observed_content(
                        adapter, request, action, result.body
                    )
                    if ok
                    else None,
                )
                gateway_metrics.record_request(
                    policy_decision_ms=policy_ms,
                    gateway_overhead_ms=max(0, policy_ms),
                    provider_latency_ms=provider_ms,
                    error=not ok,
                    streamed=False,
                )
                if ok:
                    blocked = await self._enforce_output_content(
                        adapter, result.body, decision
                    )
                    if blocked is not None:
                        return blocked
                return ProxyResponse(
                    status_code=result.status_code,
                    headers=adapter.sanitize_response_metadata(result.headers),
                    body=result.body,
                    receipt_id=_rid(decision),
                )

            upstream = await self._transport.open_stream(
                request.method,
                url,
                headers,
                request.body,
                params,
                pinned_addresses=pinned_addresses,
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
        stream_lifecycle = StreamLifecycle()

        async def on_done(
            outcome: str,
            usage: UsageInfo | None,
            streamed: int,
            duration_ms: int,
            stream_meta: dict[str, object],
        ) -> None:
            try:
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
                        stream_lifecycle=dict(stream_meta),
                    ),
                    content=self._observed_content(
                        adapter, request, action, None, stream_text=collector.text()
                    )
                    if completed
                    else None,
                    execution_status=None
                    if completed
                    else (
                        ExecutionStatus.UNKNOWN.value
                        if outcome == "cancelled"
                        else ExecutionStatus.FAILED.value
                    ),
                )
            finally:
                _release_stream_slot(stream_slot)

        return ProxyResponse(
            status_code=upstream.status_code,
            headers=adapter.sanitize_response_metadata(dict(upstream.headers)),
            stream=relay_stream(upstream, collector, on_done, lifecycle=stream_lifecycle),
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
        content: dict[str, Any] | None = None,
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
            if content and (content.get("prompt") or content.get("response")):
                _record_content(decision.receipt_id, content, self._session_scope)
            self._record_pending_evals(decision.receipt_id)

        try:
            await run_in_threadpool(work)
        except Exception:  # finalization failure must not corrupt the response
            logger.exception(
                "Failed to finalize gateway receipt %s", decision.receipt_id
            )
        if content and (content.get("prompt") or content.get("response")):
            mode = await self._content_mode(decision)
            if mode == "OBSERVE" or (mode == "ENFORCE" and content.get("streamed")):
                self._spawn_observation(decision.receipt_id, content)

    def _record_pending_evals(self, receipt_id: Any) -> None:
        from rai_gateway.provider_gateway.content_gate import record_evaluations

        pending = self._pending_evals.pop(receipt_id, None)
        if not pending:
            return
        try:
            with self._session_scope() as session:
                record_evaluations(session, receipt_id, pending)
        except Exception:
            logger.exception("Failed to record content evaluations for %s", receipt_id)

    def _spawn_observation(self, receipt_id: Any, content: dict[str, Any]) -> None:
        """OBSERVE: evaluate after the call is complete, off the response path."""
        import asyncio

        from rai_gateway.provider_gateway.content_gate import observe_content

        def work() -> None:
            try:
                with self._session_scope() as session:
                    observe_content(
                        session,
                        receipt_id,
                        prompt=content.get("prompt"),
                        response=content.get("response"),
                        streamed=bool(content.get("streamed")),
                    )
            except Exception:
                logger.exception("Content observation failed for %s", receipt_id)

        task = asyncio.ensure_future(run_in_threadpool(work))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _content_mode(self, decision: GatewayDecision) -> str:
        from rai_gateway.provider_gateway.content_gate import ENFORCE, resolve_mode

        if decision.receipt_id is None:
            return ENFORCE

        def work() -> str:
            with self._session_scope() as session:
                return resolve_mode(session, decision.receipt_id)

        return await run_in_threadpool(work)

    async def _content_gate(
        self, decision: GatewayDecision, text: str, content_type: str
    ) -> Any:
        from rai_gateway.provider_gateway.content_gate import (
            ContentGateResult,
            evaluate_content_gate,
        )

        if decision.receipt_id is None or not text:
            return ContentGateResult()

        def work() -> Any:
            with self._session_scope() as session:
                return evaluate_content_gate(
                    session,
                    decision.receipt_id,
                    text=text,
                    content_type=content_type,
                )

        return await run_in_threadpool(work)

    @staticmethod
    def _content_error(gate: Any, where: str) -> errors.ProviderGatewayError:
        if gate.blocked:
            return errors.ProviderGatewayError(
                403,
                "CONTENT_POLICY_DENIED",
                f"{where} blocked by a Content Rule",
                rai={"rule_ids": gate.rule_ids, "reasons": gate.reasons},
            )
        return errors.ProviderGatewayError(
            503,
            "CONTENT_EVALUATOR_UNAVAILABLE",
            "Content Rules could not be evaluated and this system is fail-closed",
        )

    async def _enforce_input_content(
        self,
        adapter: ProviderProtocolAdapter,
        request: ProxyRequest,
        action: GatewayAction,
        decision: GatewayDecision,
        policy_ms: int,
    ) -> None:
        """Block the call before it reaches the provider when a DENY rule matches."""
        prompt = adapter.extract_prompt_text(request)
        if not prompt or await self._content_mode(decision) != "ENFORCE":
            return
        gate = await self._content_gate(decision, prompt, "INPUT")
        if gate.evaluation is not None:
            self._pending_evals.setdefault(decision.receipt_id, []).append(
                ("INPUT", gate.evaluation, gate.blocked)
            )
        if not (gate.blocked or gate.unavailable):
            return
        exc = self._content_error(gate, "Prompt")
        await self._finalize(
            decision,
            succeeded=False,
            duration_ms=policy_ms,
            meta=build_execution_metadata(
                provider=adapter.provider_name,
                operation=action.operation,
                model=action.model,
                body_sha256=request.body_sha256(),
                request_bytes=len(request.body),
                upstream_status=None,
                response_bytes=None,
                streamed=False,
                outcome="content_blocked",
                usage=None,
                error=exc.code,
            ),
        )
        raise _with_receipt(exc, decision)

    async def _enforce_output_content(
        self,
        adapter: ProviderProtocolAdapter,
        body: bytes,
        decision: GatewayDecision,
    ) -> ProxyResponse | None:
        """Withhold a non-streaming response that violates a DENY Content Rule."""
        try:
            parsed = json.loads(body) if body else None
            text = adapter.extract_response_text(parsed) if isinstance(parsed, dict) else None
        except Exception:
            return None
        if not text or await self._content_mode(decision) != "ENFORCE":
            return None
        gate = await self._content_gate(decision, text, "OUTPUT")
        if gate.evaluation is not None:
            self._pending_evals.setdefault(decision.receipt_id, []).append(
                ("OUTPUT", gate.evaluation, gate.blocked)
            )
        if not (gate.blocked or gate.unavailable):
            return None
        await run_in_threadpool(self._record_pending_evals, decision.receipt_id)
        return error_response(
            _with_receipt(self._content_error(gate, "Response"), decision)
        )

    @staticmethod
    def _observed_content(
        adapter: ProviderProtocolAdapter,
        request: ProxyRequest,
        action: GatewayAction,
        response_body: bytes | None,
        *,
        stream_text: str | None = None,
    ) -> dict[str, Any] | None:
        """Prompt/response text held in memory only; persisted per agent-version policy."""
        try:
            response = stream_text
            if response is None and response_body:
                parsed = json.loads(response_body)
                if isinstance(parsed, dict):
                    response = adapter.extract_response_text(parsed)
            prompt = adapter.extract_prompt_text(request)
        except Exception:
            return None
        if not prompt and not response:
            return None
        return {
            "prompt": prompt,
            "response": response,
            "model": action.model,
            "provider": adapter.provider_name,
            "streamed": stream_text is not None,
        }

    async def aclose(self) -> None:
        await self._transport.aclose()


def _record_content(receipt_id: Any, content: dict[str, Any], session_scope: Any) -> None:
    """Best effort: keep prompt/response evidence if the agent version opted in."""
    try:
        from rai_gateway.evidence.service import TrajectoryService

        with session_scope() as session:
            TrajectoryService(session).record_gateway_content(
                receipt_id,
                prompt=content.get("prompt"),
                response=content.get("response"),
                model=content.get("model"),
                provider=content.get("provider"),
            )
            # default_session_scope does not commit; without this the events roll back.
            session.commit()
    except Exception:
        logger.exception("Failed to record gateway content evidence for %s", receipt_id)


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
