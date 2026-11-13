"""Fixtures for provider proxy core tests."""

from __future__ import annotations

import uuid
from contextlib import nullcontext

import httpx
import pytest

from provider_gateway.fakes import FakeAdapter, StaticResolver, Upstream
from rai_gateway.identity.service import CredentialService, RuntimeIdentityService
from rai_gateway.provider_gateway.proxy import AdapterRegistry, ProviderProxy
from rai_gateway.provider_gateway.transport import UpstreamTransport
from rai_gateway.registry.domain import AgentStatus, AgentVersionStatus, AISystemStatus
from rai_gateway.registry.service import RegistryService

MODEL_NAME = "test-model-1"


@pytest.fixture()
def model_stack(db_session, test_organization) -> dict:
    registry = RegistryService(db_session)
    suffix = uuid.uuid4().hex[:8]
    system = registry.create_ai_system(
        system_key=f"pg-sys-{suffix}", name="PG System", created_by="tester"
    )
    for status in (AISystemStatus.UNDER_REVIEW, AISystemStatus.APPROVED):
        registry.transition_ai_system(system.id, status.value, updated_by="tester")
    agent = registry.create_agent(
        system.id, agent_key=f"pg-agent-{suffix}", name="PG Agent", created_by="tester"
    )
    for status in (AgentStatus.UNDER_REVIEW, AgentStatus.APPROVED):
        registry.transition_agent(agent.id, status.value, updated_by="tester")
    version = registry.create_agent_version(agent.id, created_by="tester")

    model_name = f"{MODEL_NAME}-{suffix}"
    model = registry.create_model_resource(
        scope="PLATFORM",
        allow_platform=True,
        provider="fakeprov",
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
        name="PG Runtime",
        created_by="tester",
    )
    issued = CredentialService(db_session).issue_credential(
        identity.id, created_by="tester"
    )
    return {
        "system": system,
        "model": model,
        "model_name": model_name,
        "secret": issued.plaintext_secret,
    }


@pytest.fixture()
def make_proxy(db_session):
    proxies: list[ProviderProxy] = []

    def _make(handler, *, resolver=None, max_retries=0, **kwargs):
        upstream = Upstream(handler)
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        transport = UpstreamTransport(
            client, max_retries=max_retries, backoff_seconds=0
        )
        proxy = ProviderProxy(
            AdapterRegistry([FakeAdapter()]),
            transport=transport,
            credentials=resolver or StaticResolver(),
            mode="ENFORCE",
            session_scope=lambda: nullcontext(db_session),
            **kwargs,
        )
        proxies.append(proxy)
        return proxy, upstream

    return _make
