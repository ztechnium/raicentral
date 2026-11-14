"""Step 3: OpenAI-compatible gateway through the provider proxy core."""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext

import httpx
import pytest

from provider_gateway.fakes import UPSTREAM_KEY, StaticResolver, Upstream
from rai_gateway.identity.service import CredentialService, RuntimeIdentityService
from rai_gateway.provider_gateway.adapters.openai import OpenAIAdapter
from rai_gateway.provider_gateway.domain import ProxyRequest
from rai_gateway.provider_gateway.proxy import AdapterRegistry, ProviderProxy
from rai_gateway.provider_gateway.transport import UpstreamTransport
from rai_gateway.registry.domain import AgentStatus, AgentVersionStatus, AISystemStatus
from rai_gateway.registry.service import RegistryService
from rai_gateway.runtime_enforcement.domain import ExecutionSource
from rai_gateway.runtime_enforcement.repository import RuntimeEnforcementRepository
from rai_gateway.services.llm_controller import app

CHAT_PATH = "/providers/openai/v1/chat/completions"
EMBED_PATH = "/providers/openai/v1/embeddings"


@pytest.fixture()
def openai_stack(db_session, test_organization) -> dict:
    registry = RegistryService(db_session)
    suffix = uuid.uuid4().hex[:8]
    system = registry.create_ai_system(
        system_key=f"oai-sys-{suffix}", name="OpenAI GW", created_by="tester"
    )
    for status in (AISystemStatus.UNDER_REVIEW, AISystemStatus.APPROVED):
        registry.transition_ai_system(system.id, status.value, updated_by="tester")
    agent = registry.create_agent(
        system.id, agent_key=f"oai-agent-{suffix}", name="OpenAI Agent", created_by="tester"
    )
    for status in (AgentStatus.UNDER_REVIEW, AgentStatus.APPROVED):
        registry.transition_agent(agent.id, status.value, updated_by="tester")
    version = registry.create_agent_version(agent.id, created_by="tester")
    model_name = f"gpt-test-{suffix}"
    model = registry.create_model_resource(
        scope="PLATFORM",
        allow_platform=True,
        provider="openai",
        model_name=model_name,
    )
    registry.add_version_dependency(
        version.id, resource_type="model", resource_id=model.id
    )
    for status in (AgentVersionStatus.UNDER_REVIEW, AgentVersionStatus.APPROVED):
        registry.transition_agent_version(version.id, status.value, updated_by="tester")
    identity = RuntimeIdentityService(db_session).create_runtime_identity(
        agent_id=agent.id,
        agent_version_id=version.id,
        name="OpenAI Runtime",
        created_by="tester",
    )
    issued = CredentialService(db_session).issue_credential(
        identity.id, created_by="tester"
    )
    return {"model_name": model_name, "secret": issued.plaintext_secret}


@pytest.fixture()
def make_openai_proxy(db_session):
    proxies: list[ProviderProxy] = []

    def _make(handler, **kwargs):
        upstream = Upstream(handler)
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        transport = UpstreamTransport(client, max_retries=0, backoff_seconds=0)
        proxy = ProviderProxy(
            AdapterRegistry([OpenAIAdapter()]),
            transport=transport,
            credentials=StaticResolver(),
            mode="ENFORCE",
            session_scope=lambda: nullcontext(db_session),
            **kwargs,
        )
        proxies.append(proxy)
        return proxy, upstream

    return _make


def _req(stack, *, body=None, path=CHAT_PATH, headers=None) -> ProxyRequest:
    payload = body if body is not None else {"model": stack["model_name"]}
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    base = {
        "authorization": f"Bearer {stack['secret']}",
        "content-type": "application/json",
    }
    base.update(headers or {})
    return ProxyRequest(method="POST", path=path, headers=base, body=raw)


def _chat_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chat-1",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        },
    )


async def test_chat_completions_forwarded_openai_upstream(
    db_session, openai_stack, make_openai_proxy
) -> None:
    proxy, upstream = make_openai_proxy(_chat_ok)
    body = {
        "model": openai_stack["model_name"],
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
    }
    req = _req(openai_stack, body=body)
    response = await proxy.handle(req, db=db_session)
    assert response.status_code == 200
    assert len(upstream.requests) == 1
    sent = upstream.requests[0]
    assert str(sent.url) == "https://upstream.test/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert openai_stack["secret"] not in json.dumps(dict(sent.headers))
    assert sent.content == req.body


async def test_embeddings_operation_and_usage(
    db_session, openai_stack, make_openai_proxy
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1]}], "usage": {"prompt_tokens": 3, "total_tokens": 3}},
        )

    proxy, upstream = make_openai_proxy(handler)
    req = _req(
        openai_stack,
        path=EMBED_PATH,
        body={"model": openai_stack["model_name"], "input": "x"},
    )
    response = await proxy.handle(req, db=db_session)
    assert response.status_code == 200
    assert upstream.requests[0].url.path.endswith("/embeddings")
    receipt = RuntimeEnforcementRepository(db_session).get_receipt(
        __import__("uuid").UUID(response.receipt_id)
    )
    db_session.refresh(receipt)
    assert receipt.execution_report["usage"]["input_tokens"] == 3
    assert receipt.execution_source == ExecutionSource.PROVIDER_PROXY.value


async def test_openai_streaming_sse(
    db_session, openai_stack, make_openai_proxy
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        async def gen():
            yield (
                b'data: {"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=gen()
        )

    proxy, _ = make_openai_proxy(handler)
    req = _req(
        openai_stack, body={"model": openai_stack["model_name"], "stream": True, "messages": []}
    )
    response = await proxy.handle(req, db=db_session)
    chunks = [c async for c in response.stream]
    assert b"[DONE]" in b"".join(chunks)
    receipt = RuntimeEnforcementRepository(db_session).get_receipt(
        __import__("uuid").UUID(response.receipt_id)
    )
    db_session.refresh(receipt)
    assert receipt.execution_status == "SUCCEEDED"
    assert receipt.execution_report["usage"]["total_tokens"] == 2


async def test_unregistered_model_denied(
    db_session, openai_stack, make_openai_proxy
) -> None:
    proxy, upstream = make_openai_proxy(_chat_ok)
    response = await proxy.handle(
        _req(openai_stack, body={"model": "gpt-unknown"}), db=db_session
    )
    assert response.status_code == 403
    assert upstream.requests == []


def test_http_routes_mounted_on_llm_controller() -> None:
    paths = set(app.openapi()["paths"])
    assert "/providers/{provider_name}/{api_version}/{path}" in paths
    openai_route = app.openapi()["paths"][
        "/providers/{provider_name}/{api_version}/{path}"
    ]
    assert "post" in openai_route
