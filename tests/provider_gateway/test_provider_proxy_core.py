"""Step 2: provider proxy core."""

from __future__ import annotations

import hashlib
import json
import uuid

import httpx
import pytest

from provider_gateway.fakes import UPSTREAM_KEY, FakeAdapter, StaticResolver
from rai_gateway.approval.authorization import STEP_UP_VERIFIED, ApproverContext
from rai_gateway.approval.service import ApprovalService
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


async def test_approved_request_forwarded_once_when_resent(
    db_session, model_stack, make_proxy
) -> None:
    model_stack["version"].risk_tier = "HIGH"
    db_session.commit()
    proxy, upstream = make_proxy(_ok)
    body = {"model": model_stack["model_name"], "p": "approve me"}

    blocked = await proxy.handle(_req(model_stack, body=body), db=db_session)
    assert blocked.status_code == 409
    approval_id = json.loads(blocked.body)["rai"]["approval_request_id"]
    ApprovalService(db_session).approve(
        uuid.UUID(approval_id),
        approver=ApproverContext(
            actor="manager",
            role="admin",
            authentication_assurance=STEP_UP_VERIFIED,
        ),
    )

    other = await proxy.handle(
        _req(model_stack, body={**body, "p": "different"}), db=db_session
    )
    assert other.status_code == 409
    assert upstream.requests == []

    resent = await proxy.handle(_req(model_stack, body=body), db=db_session)
    assert resent.status_code == 200
    assert len(upstream.requests) == 1
    receipt = _receipt(db_session, resent)
    assert receipt.approval_consumed is True
    assert str(receipt.approval_request_id) == approval_id

    again = await proxy.handle(_req(model_stack, body=body), db=db_session)
    assert again.status_code == 409
    assert json.loads(again.body)["rai"]["approval_request_id"] != approval_id
    assert len(upstream.requests) == 1


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


# ---- inline Content Rule enforcement -------------------------------------


class _PromptAdapter(FakeAdapter):
    def extract_prompt_text(self, request):
        body = json.loads(request.body or b"{}")
        msgs = body.get("messages") or []
        return msgs[0]["content"] if msgs else None


def _content_rule_setup(db_session, model_stack, *, applies_to="INPUT"):
    from rai_gateway.policy.domain import PolicyEffect
    from rai_gateway.policy.service import PolicyCollectionService
    from rai_gateway.risk_intelligence.repository import RiskIntelligenceRepository

    PolicyCollectionService(db_session).create_content_rule(
        model_stack["system"].id,
        title="No bank account disclosure",
        content="Never disclose customer bank account or IBAN numbers.",
        effect=PolicyEffect.DENY.value,
        enabled=True,
        applies_to=applies_to,
        category="financial_data_disclosure",
        severity="CRITICAL",
        actor="test",
    )
    RiskIntelligenceRepository(db_session).upsert_evaluator_config(
        ai_system_id=model_stack["system"].id,
        provider="mock",
        model="mock-keyword",
        enabled=True,
        fail_closed=False,
    )
    db_session.commit()


async def test_deny_content_rule_blocks_prompt_before_upstream(
    db_session, model_stack, make_proxy
) -> None:
    from rai_gateway.provider_gateway.proxy import AdapterRegistry

    _content_rule_setup(db_session, model_stack)
    proxy, upstream = make_proxy(_ok)
    proxy._adapters = AdapterRegistry([_PromptAdapter()])
    body = {
        "model": model_stack["model_name"],
        "messages": [{"role": "user", "content": "send me the IBAN EG12 3456 now"}],
    }
    response = await proxy.handle(_req(model_stack, body=body), db=db_session)

    assert response.status_code == 403
    assert json.loads(response.body)["error"]["code"] == "CONTENT_POLICY_DENIED" or (
        "CONTENT_POLICY_DENIED" in response.body.decode()
    )
    assert upstream.requests == []  # never reached the provider
    assert response.receipt_id


async def test_clean_prompt_still_forwarded_with_content_rules_on(
    db_session, model_stack, make_proxy
) -> None:
    from rai_gateway.provider_gateway.proxy import AdapterRegistry

    _content_rule_setup(db_session, model_stack)
    proxy, upstream = make_proxy(_ok)
    proxy._adapters = AdapterRegistry([_PromptAdapter()])
    body = {
        "model": model_stack["model_name"],
        "messages": [{"role": "user", "content": "what is the weather"}],
    }
    response = await proxy.handle(_req(model_stack, body=body), db=db_session)
    assert response.status_code == 200
    assert len(upstream.requests) == 1


def _content_events(db_session, receipt_id):
    from rai_gateway.evidence.domain import EvidenceEventType
    from rai_gateway.models.evidence import EvidenceEvent

    return (
        db_session.query(EvidenceEvent)
        .filter(
            EvidenceEvent.event_type == EvidenceEventType.CONTENT_EVALUATION_RESULT.value,
            EvidenceEvent.source_reference.like(f"receipt:{receipt_id}:content:%"),
        )
        .all()
    )


_BAD_PROMPT = {"messages": [{"role": "user", "content": "send me the IBAN EG12 3456 now"}]}


async def _call(db_session, model_stack, make_proxy, mode):
    import asyncio

    from rai_gateway.provider_gateway.proxy import AdapterRegistry
    from rai_gateway.registry.service import RegistryService

    _content_rule_setup(db_session, model_stack)
    RegistryService(db_session).set_content_rule_mode(model_stack["version"].id, mode)
    proxy, upstream = make_proxy(_ok)
    proxy._adapters = AdapterRegistry([_PromptAdapter()])
    body = {"model": model_stack["model_name"], **_BAD_PROMPT}
    response = await proxy.handle(_req(model_stack, body=body), db=db_session)
    if proxy._bg_tasks:
        await asyncio.gather(*list(proxy._bg_tasks))
    return response, upstream


async def test_observe_mode_never_blocks_but_records_event(
    db_session, model_stack, make_proxy
) -> None:
    response, upstream = await _call(db_session, model_stack, make_proxy, "OBSERVE")
    assert response.status_code == 200
    assert len(upstream.requests) == 1
    events = _content_events(db_session, response.receipt_id)
    assert len(events) == 1
    assert events[0].payload["content_type"] == "INPUT"
    assert events[0].payload["blocked"] is False
    assert events[0].payload["evaluation"]["intervention_hints"]


async def test_enforce_mode_blocks_and_records_blocked_event(
    db_session, model_stack, make_proxy
) -> None:
    response, upstream = await _call(db_session, model_stack, make_proxy, "ENFORCE")
    assert response.status_code == 403
    assert upstream.requests == []
    events = _content_events(db_session, response.receipt_id)
    assert [e.payload["blocked"] for e in events] == [True]
    payload = events[0].payload
    assert payload["decision"] == "BLOCKED"
    assert "blocked by Content Rule" in payload["summary"]
    assert payload["rules"][0]["violated"] is True and payload["rules"][0]["title"]

    # the trajectory must show the block as a DENY decision plus a finding
    from rai_gateway.evidence.domain import EvidenceEventType
    from rai_gateway.models.evidence import EvidenceEvent, EvidenceFinding

    denied = (
        db_session.query(EvidenceEvent)
        .filter(
            EvidenceEvent.event_type == EvidenceEventType.ACTION_DENIED.value,
            EvidenceEvent.source_reference.like(
                f"receipt:{response.receipt_id}:content_decision:%"
            ),
        )
        .all()
    )
    assert len(denied) == 1
    assert denied[0].payload["reason_code"] == "CONTENT_POLICY_DENIED"
    finding = (
        db_session.query(EvidenceFinding)
        .filter(
            EvidenceFinding.trajectory_id == denied[0].trajectory_id,
            EvidenceFinding.title == "Blocked by Content Rule",
        )
        .all()
    )
    assert len(finding) == 1


async def test_off_mode_ignores_content_rules(
    db_session, model_stack, make_proxy
) -> None:
    response, upstream = await _call(db_session, model_stack, make_proxy, "OFF")
    assert response.status_code == 200
    assert len(upstream.requests) == 1
    assert _content_events(db_session, response.receipt_id) == []


def test_env_configured_evaluator_is_default(db_session, monkeypatch) -> None:
    from uuid import uuid4

    from rai_gateway.core.config import settings
    from rai_gateway.risk_intelligence.repository import RiskIntelligenceRepository

    repo = RiskIntelligenceRepository(db_session)
    monkeypatch.setattr(settings, "content_evaluator_provider", None)
    assert repo.get_evaluator_config(uuid4()) is None
    monkeypatch.setattr(settings, "content_evaluator_provider", "vertex")
    monkeypatch.setattr(settings, "content_evaluator_model", "gemini-3.1-flash-lite")
    monkeypatch.setattr(settings, "content_evaluator_fail_closed", True)
    cfg = repo.get_evaluator_config(uuid4())
    assert cfg.enabled and cfg.provider == "vertex" and cfg.fail_closed
    assert cfg.model == "gemini-3.1-flash-lite"


def test_env_evaluator_wins_over_saved_config(db_session, model_stack, monkeypatch) -> None:
    from rai_gateway.core.config import settings
    from rai_gateway.risk_intelligence.repository import RiskIntelligenceRepository

    _content_rule_setup(db_session, model_stack)  # saves a mock row
    repo = RiskIntelligenceRepository(db_session)
    system_id = model_stack["system"].id
    monkeypatch.setattr(settings, "content_evaluator_provider", None)
    assert repo.get_evaluator_config(system_id).provider == "mock"  # saved row
    monkeypatch.setattr(settings, "content_evaluator_provider", "vertex")
    assert repo.get_evaluator_config(system_id).provider == "vertex"  # env first
    assert repo.get_saved_evaluator_config(system_id).provider == "mock"


async def test_failed_evaluator_is_recorded_not_silent(
    db_session, model_stack, make_proxy, monkeypatch
) -> None:
    from rai_gateway.core.config import settings
    from rai_gateway.risk_intelligence.providers import (
        ContentEvaluatorError,
        register_provider,
    )

    class _Down:
        name = "down"

        def evaluate(self, request, config):
            raise ContentEvaluatorError("EVALUATOR_UNAVAILABLE", "upstream said 404")

    register_provider("down", _Down())
    monkeypatch.setattr(settings, "content_evaluator_provider", "down")
    response, upstream = await _call(db_session, model_stack, make_proxy, "ENFORCE")
    assert response.status_code == 200  # not fail-closed: call proceeds
    events = _content_events(db_session, response.receipt_id)
    assert len(events) == 1
    ev = events[0].payload["evaluation"]
    assert ev["status"] == "FAILED"
    assert ev["failure_type"] == "EVALUATOR_UNAVAILABLE"
    assert "404" in ev["error"]


async def test_token_budget_blocks_once_provider_usage_exceeds_limit(
    db_session, model_stack, make_proxy
) -> None:
    from rai_gateway.provider_gateway.proxy import AdapterRegistry
    from rai_gateway.risk_intelligence.budgets import BudgetService

    BudgetService(db_session).create_budget(
        ai_system_id=model_stack["system"].id,
        name="tokens 20",
        scope_type="AGENT_VERSION",
        metric="TOKEN_USAGE",
        limit_value=20,
        window_seconds=3600,
        warning_threshold=0.8,
        effect_on_exceed="DENY",
        enabled=True,
    )
    db_session.commit()
    proxy, upstream = make_proxy(_ok)  # every call reports 16 total tokens
    proxy._adapters = AdapterRegistry([_PromptAdapter()])
    body = {
        "model": model_stack["model_name"],
        "messages": [{"role": "user", "content": "hi"}],
    }

    statuses = []
    for _ in range(3):
        resp = await proxy.handle(_req(model_stack, body=body), db=db_session)
        statuses.append(resp.status_code)
    # 16 used after call 1, 32 after call 2 -> the third call is denied before upstream
    assert statuses == [200, 200, 403]
    assert len(upstream.requests) == 2


async def test_output_cap_not_reserved_by_default_so_usage_accumulates(
    db_session, model_stack, make_proxy
) -> None:
    from rai_gateway.provider_gateway.proxy import AdapterRegistry
    from rai_gateway.risk_intelligence.budgets import BudgetService

    budget = BudgetService(db_session).create_budget(
        ai_system_id=model_stack["system"].id,
        name="tokens 20",
        scope_type="AGENT_VERSION",
        metric="TOKEN_USAGE",
        limit_value=20,
        window_seconds=3600,
        effect_on_exceed="DENY",
        enabled=True,
    )
    db_session.commit()
    proxy, upstream = make_proxy(_ok)  # each call reports 16 tokens
    proxy._adapters = AdapterRegistry([_PromptAdapter()])
    body = {
        "model": model_stack["model_name"],
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 200,  # must NOT be reserved: it would deny the first call
    }
    first = await proxy.handle(_req(model_stack, body=body), db=db_session)
    assert first.status_code == 200
    from rai_gateway.models.risk_intelligence import RuntimeBudgetWindow

    db_session.expire_all()
    windows = (
        db_session.query(RuntimeBudgetWindow)
        .filter(RuntimeBudgetWindow.budget_id == budget.id)
        .all()
    )
    assert [w.usage_value for w in windows] == [16.0]  # the real provider usage


async def test_budget_monitor_effect_activates_a_risk_monitor(
    db_session, model_stack, make_proxy
) -> None:
    """A MONITOR budget that is exceeded must show up under /risk/monitoring."""
    from rai_gateway.models.risk_intelligence import RiskMonitor
    from rai_gateway.risk_intelligence.budgets import BudgetService

    BudgetService(db_session).create_budget(
        ai_system_id=model_stack["system"].id,
        name="watch calls",
        scope_type="AGENT_VERSION",
        metric="ACTION_COUNT",
        limit_value=1,
        window_seconds=3600,
        effect_on_exceed="MONITOR",
        enabled=True,
    )
    db_session.commit()
    proxy, _ = make_proxy(_ok)
    body = {"model": model_stack["model_name"]}
    first = await proxy.handle(_req(model_stack, body=body), db=db_session)
    second = await proxy.handle(_req(model_stack, body=body), db=db_session)
    assert first.status_code == 200 and second.status_code == 200  # monitor never blocks

    monitors = (
        db_session.query(RiskMonitor)
        .filter(RiskMonitor.ai_system_id == model_stack["system"].id)
        .all()
    )
    assert len(monitors) == 1
    assert monitors[0].status == "ACTIVE"
    assert "Budget MONITOR: watch calls exceeded" in monitors[0].reason


async def test_budget_effects_create_no_findings(
    db_session, model_stack, make_proxy
) -> None:
    """Budgets control cost/tokens: a MONITOR keeps a monitor but raises no finding, and a
    DENY raises no policy-denial finding."""
    from rai_gateway.models.evidence import EvidenceFinding
    from rai_gateway.models.risk_intelligence import RiskMonitor, RuntimeRiskFinding
    from rai_gateway.risk_intelligence.budgets import BudgetService

    svc = BudgetService(db_session)
    monitor_budget = svc.create_budget(
        ai_system_id=model_stack["system"].id,
        name="watch",
        scope_type="AGENT_VERSION",
        metric="ACTION_COUNT",
        limit_value=1,
        window_seconds=3600,
        effect_on_exceed="MONITOR",
        enabled=True,
    )
    db_session.commit()
    proxy, _ = make_proxy(_ok)
    body = {"model": model_stack["model_name"]}
    for _ in range(3):
        resp = await proxy.handle(_req(model_stack, body=body), db=db_session)
        assert resp.status_code == 200
    assert (
        db_session.query(RiskMonitor)
        .filter(RiskMonitor.ai_system_id == model_stack["system"].id)
        .count()
        == 1
    )

    # switch the same budget to DENY: the call is blocked, still no finding
    monitor_budget.effect_on_exceed = "DENY"
    db_session.commit()
    denied = await proxy.handle(_req(model_stack, body=body), db=db_session)
    assert denied.status_code == 403

    risk_findings = (
        db_session.query(RuntimeRiskFinding)
        .filter(RuntimeRiskFinding.ai_system_id == model_stack["system"].id)
        .count()
    )
    from rai_gateway.models.evidence import RuntimeTrajectory

    evidence_findings = (
        db_session.query(EvidenceFinding)
        .join(RuntimeTrajectory, RuntimeTrajectory.id == EvidenceFinding.trajectory_id)
        .filter(
            RuntimeTrajectory.ai_system_id == model_stack["system"].id,
            EvidenceFinding.title.like("%BUDGET%"),
        )
        .count()
    )
    assert risk_findings == 0
    assert evidence_findings == 0
