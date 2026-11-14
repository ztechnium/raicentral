"""Step 2: provider proxy core."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from provider_gateway.fakes import UPSTREAM_KEY, FakeAdapter, StaticResolver
from rai_gateway.provider_gateway import errors
from rai_gateway.provider_gateway.adapters.base import ProviderProtocolAdapter
from rai_gateway.provider_gateway.auth import extract_runtime_secret
from rai_gateway.provider_gateway.domain import ProxyRequest
from rai_gateway.provider_gateway.headers import (
    filter_inbound_headers,
    filter_outbound_headers,
    inject_gateway_headers,
)
from rai_gateway.runtime_enforcement.domain import ExecutionSource
from rai_gateway.runtime_enforcement.gateway_action import GatewayDecision
from rai_gateway.runtime_enforcement.repository import RuntimeEnforcementRepository


def _req(stack, *, body=None, headers=None, path="/fake/chat", **kw) -> ProxyRequest:
    payload = body if body is not None else {"model": stack["model_name"]}
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    base = {
        "authorization": f"Bearer {stack['secret']}",
        "content-type": "application/json",
    }
    base.update(headers or {})
    return ProxyRequest(method="POST", path=path, headers=base, body=raw, **kw)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"ok": True, "usage": {"in": 11, "out": 5, "total": 16}}
    )


def _receipt(db_session, response):
    return RuntimeEnforcementRepository(db_session).get_receipt(
        __import__("uuid").UUID(response.receipt_id)
    )


# ---- interface ----------------------------------------------------------


def test_adapter_interface_is_abstract() -> None:
    with pytest.raises(TypeError):
        ProviderProtocolAdapter()  # type: ignore[abstract]
    assert FakeAdapter().provider_name == "fakeprov"


# ---- header policy ------------------------------------------------------


def test_inbound_filter_strips_credentials_and_hop_by_hop() -> None:
    safe = filter_inbound_headers(
        {
            "Authorization": "Bearer client-key",
            "api-key": "k",
            "x-api-key": "k",
            "x-goog-api-key": "k",
            "X-RAI-Agent-Key": "rai",
            "Host": "evil",
            "Content-Length": "9",
            "Connection": "close",
            "Proxy-Authorization": "p",
            "Cookie": "c",
            "traceparent": "00-abc",
            "x-request-id": "r1",
            "content-type": "application/json",
            "x-unknown": "dropped",
            "x-fake-version": "2",
        },
        extra_allowed=["x-fake-version"],
    )
    assert safe == {
        "traceparent": "00-abc",
        "x-request-id": "r1",
        "content-type": "application/json",
        "x-fake-version": "2",
    }


def test_adapter_cannot_re_allow_credential_headers() -> None:
    assert (
        filter_inbound_headers({"Authorization": "x"}, extra_allowed=["authorization"])
        == {}
    )


def test_gateway_credentials_override_inherited_headers() -> None:
    merged = inject_gateway_headers({"x-trace": "1"}, {"Authorization": "Bearer g"})
    assert merged == {"x-trace": "1", "Authorization": "Bearer g"}


def test_outbound_filter() -> None:
    out = filter_outbound_headers(
        {"content-type": "a", "Set-Cookie": "s", "content-length": "1", "x-r": "y"}
    )
    assert out == {"content-type": "a", "x-r": "y"}


def test_extract_runtime_secret() -> None:
    assert extract_runtime_secret({"X-RAI-Agent-Key": "a"}) == "a"
    assert extract_runtime_secret({"Authorization": "Bearer b"}) == "b"
    assert extract_runtime_secret({"Authorization": "Basic zzz"}) is None
    assert extract_runtime_secret({}) is None


# ---- allow path ---------------------------------------------------------


async def test_allowed_request_forwarded_with_gateway_credentials(
    db_session, model_stack, make_proxy
) -> None:
    proxy, upstream = make_proxy(_ok)
    body = {
        "model": model_stack["model_name"],
        "messages": [{"role": "user", "content": "hi"}],
    }
    req = _req(
        model_stack,
        body=body,
        headers={"api-key": "client-provider-key", "x-fake-version": "2"},
    )
    response = await proxy.handle(req, db=db_session)

    assert response.status_code == 200
    assert json.loads(response.body)["ok"] is True
    assert len(upstream.requests) == 1
    sent = upstream.requests[0]
    assert str(sent.url) == "https://upstream.test/v1/chat"
    # gateway credential injected; client credentials never forwarded
    assert sent.headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert "api-key" not in sent.headers
    assert model_stack["secret"] not in json.dumps(dict(sent.headers))
    assert sent.headers["x-fake-version"] == "2"
    # body forwarded byte-for-byte
    assert sent.content == req.body


async def test_receipt_finalized_on_success_with_digest_and_usage(
    db_session, model_stack, make_proxy
) -> None:
    proxy, _ = make_proxy(_ok)
    req = _req(
        model_stack, body={"model": model_stack["model_name"], "p": "secret prompt"}
    )
    response = await proxy.handle(req, db=db_session)

    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "SUCCEEDED"
    assert receipt.execution_source == ExecutionSource.PROVIDER_PROXY.value
    report = receipt.execution_report
    assert report["request_body_sha256"] == hashlib.sha256(req.body).hexdigest()
    assert report["upstream_status"] == 200
    assert report["usage"]["input_tokens"] == 11
    assert report["usage"]["provider"] == "fakeprov"
    # No prompt body persisted anywhere on the receipt
    dump = json.dumps(
        [receipt.execution_report, receipt.action_summary, receipt.client_context],
        default=str,
    )
    assert "secret prompt" not in dump
    assert receipt.completed_at is not None


# ---- deny / identity ----------------------------------------------------


async def test_unregistered_model_denied_before_upstream(
    db_session, model_stack, make_proxy
) -> None:
    proxy, upstream = make_proxy(_ok)
    response = await proxy.handle(
        _req(model_stack, body={"model": "not-registered"}), db=db_session
    )
    assert response.status_code == 403
    assert response.gateway_generated is True
    body = json.loads(response.body)
    assert body["rai"]["decision"] == "DENY"
    assert body["rai"]["gateway_generated"] is True
    assert body["error"]["type"] == "rai_gateway_error"
    assert upstream.requests == []


async def test_missing_identity_is_401_without_upstream(
    db_session, model_stack, make_proxy
) -> None:
    proxy, upstream = make_proxy(_ok)
    response = await proxy.handle(
        ProxyRequest("POST", "/fake/chat", {}, b"{}"), db=db_session
    )
    assert response.status_code == 401
    assert upstream.requests == []


async def test_invalid_identity_is_401_without_upstream(
    db_session, model_stack, make_proxy
) -> None:
    proxy, upstream = make_proxy(_ok)
    req = _req(model_stack, headers={"authorization": "Bearer not-valid"})
    response = await proxy.handle(req, db=db_session)
    assert response.status_code == 401
    assert upstream.requests == []


async def test_client_supplied_provider_key_is_not_an_identity(
    db_session, model_stack, make_proxy
) -> None:
    proxy, upstream = make_proxy(_ok)
    req = _req(
        model_stack,
        headers={"authorization": "", "api-key": "sk-provider", "x-api-key": "sk"},
    )
    response = await proxy.handle(req, db=db_session)
    assert response.status_code == 401
    assert upstream.requests == []


async def test_no_adapter_404_and_body_limit_413(
    db_session, model_stack, make_proxy
) -> None:
    proxy, upstream = make_proxy(_ok, max_body_bytes=64)
    assert (
        await proxy.handle(_req(model_stack, path="/other"), db=db_session)
    ).status_code == 404
    big = _req(model_stack, body=b"x" * 200)
    assert (await proxy.handle(big, db=db_session)).status_code == 413
    assert upstream.requests == []


async def test_missing_gateway_credential_fails_closed(
    db_session, model_stack, make_proxy
) -> None:
    proxy, upstream = make_proxy(_ok, resolver=StaticResolver(available=False))
    response = await proxy.handle(_req(model_stack), db=db_session)
    assert response.status_code == 503
    assert (
        json.loads(response.body)["error"]["code"] == "PROVIDER_CREDENTIAL_UNAVAILABLE"
    )
    assert upstream.requests == []


def test_decision_to_http_contract() -> None:
    def make(decision, reasons=(), **kw):
        return errors.from_decision(
            GatewayDecision(
                allowed=False, decision=decision, reason_codes=list(reasons), **kw
            )
        )

    assert make("DENY", ["POLICY_DENIED"]).status_code == 403
    assert make("DENY", ["RUNTIME_AUTHENTICATION_FAILED"]).status_code == 401
    assert make("DENY", ["GATEWAY_EVALUATION_FAILED"]).status_code == 503
    approval = make("REQUIRE_APPROVAL", ["APPROVAL_REQUIRED"])
    assert approval.status_code == 409
    assert approval.body()["rai"]["decision"] == "REQUIRE_APPROVAL"
    assert "approval_request_id" in approval.body()["rai"]


# ---- upstream behaviour -------------------------------------------------


async def test_upstream_4xx_passthrough_and_receipt_failed(
    db_session, model_stack, make_proxy
) -> None:
    payload = {"error": {"message": "bad param", "type": "invalid_request_error"}}
    proxy, upstream = make_proxy(lambda r: httpx.Response(400, json=payload))
    response = await proxy.handle(_req(model_stack), db=db_session)
    assert response.status_code == 400
    assert json.loads(response.body) == payload
    assert response.gateway_generated is False
    assert len(upstream.requests) == 1  # 4xx never retried
    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "FAILED"
    assert receipt.execution_report["upstream_status"] == 400


async def test_upstream_5xx_passthrough(db_session, model_stack, make_proxy) -> None:
    proxy, _ = make_proxy(lambda r: httpx.Response(500, json={"error": "boom"}))
    response = await proxy.handle(_req(model_stack), db=db_session)
    assert response.status_code == 500
    assert json.loads(response.body) == {"error": "boom"}
    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "FAILED"


async def test_503_retried_then_succeeds(db_session, model_stack, make_proxy) -> None:
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else _ok(request)

    proxy, upstream = make_proxy(handler, max_retries=2)
    response = await proxy.handle(_req(model_stack), db=db_session)
    assert response.status_code == 200
    assert len(upstream.requests) == 2


async def test_connect_error_retried_then_502_and_receipt_failed(
    db_session, model_stack, make_proxy
) -> None:
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    proxy, upstream = make_proxy(handler, max_retries=1)
    response = await proxy.handle(_req(model_stack), db=db_session)
    assert response.status_code == 502
    assert len(upstream.requests) == 2
    assert json.loads(response.body)["rai"]["code"] == "UPSTREAM_UNREACHABLE"
    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "FAILED"
    assert receipt.execution_report["outcome"] == "transport_error"


async def test_timeout_maps_to_504_not_retried(
    db_session, model_stack, make_proxy
) -> None:
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    proxy, upstream = make_proxy(handler, max_retries=3)
    response = await proxy.handle(_req(model_stack), db=db_session)
    assert response.status_code == 504
    assert len(upstream.requests) == 1
    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "FAILED"


# ---- streaming ----------------------------------------------------------


def _sse(request):
    async def gen():
        yield b"data: one\n\n"
        yield b"data: two\n\n"
        yield b"data: [DONE]\n\n"

    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=gen()
    )


async def test_stream_concurrency_slot_held_until_relay_finishes(
    db_session, model_stack, make_proxy
) -> None:
    from rai_gateway.provider_gateway.production.limits import gateway_limits

    proxy, _ = make_proxy(_sse)
    req = _req(model_stack, body={"model": model_stack["model_name"], "stream": True})
    response = await proxy.handle(req, db=db_session)
    assert response.stream is not None
    assert gateway_limits.snapshot()["global_streams"] == 1
    chunks = [c async for c in response.stream]
    assert chunks
    assert gateway_limits.snapshot()["global_streams"] == 0


async def test_streaming_passthrough_and_finalize_after_completion(
    db_session, model_stack, make_proxy
) -> None:
    proxy, _ = make_proxy(_sse)
    req = _req(model_stack, body={"model": model_stack["model_name"], "stream": True})
    response = await proxy.handle(req, db=db_session)
    assert response.stream is not None
    assert response.headers["content-type"] == "text/event-stream"

    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "STARTED"  # not finalized mid-stream

    chunks = [c async for c in response.stream]
    assert b"".join(chunks) == b"data: one\n\ndata: two\n\ndata: [DONE]\n\n"
    db_session.refresh(receipt)
    assert receipt.execution_status == "SUCCEEDED"
    assert receipt.execution_report["usage"]["total_tokens"] == 10
    assert receipt.execution_report["streamed"] is True


async def test_stream_failure_midway_finalizes_failed_without_retry(
    db_session, model_stack, make_proxy
) -> None:
    def handler(request):
        async def gen():
            yield b"data: partial\n\n"
            raise httpx.ReadError("connection reset")

        return httpx.Response(200, content=gen())

    proxy, upstream = make_proxy(handler, max_retries=3)
    req = _req(model_stack, body={"model": model_stack["model_name"], "stream": True})
    response = await proxy.handle(req, db=db_session)
    got = []
    with pytest.raises(httpx.ReadError):
        async for chunk in response.stream:
            got.append(chunk)
    assert got == [b"data: partial\n\n"]
    assert len(upstream.requests) == 1  # no retry after partial response
    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "FAILED"


async def test_client_cancellation_finalizes_unknown(
    db_session, model_stack, make_proxy
) -> None:
    proxy, _ = make_proxy(_sse)
    req = _req(model_stack, body={"model": model_stack["model_name"], "stream": True})
    response = await proxy.handle(req, db=db_session)
    stream = response.stream
    await stream.__anext__()
    await stream.aclose()  # client disconnected
    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "UNKNOWN"
    assert receipt.execution_report["outcome"] == "cancelled"


async def test_streaming_upstream_error_relayed_before_stream(
    db_session, model_stack, make_proxy
) -> None:
    proxy, _ = make_proxy(lambda r: httpx.Response(429, json={"error": "rate"}))
    req = _req(model_stack, body={"model": model_stack["model_name"], "stream": True})
    response = await proxy.handle(req, db=db_session)
    assert response.status_code == 429
    assert response.stream is None
    receipt = _receipt(db_session, response)
    db_session.refresh(receipt)
    assert receipt.execution_status == "FAILED"
