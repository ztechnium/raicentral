"""Step 5: workload identity and provider credential isolation."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import nullcontext

import httpx
import pytest

from provider_gateway.fakes import FakeAdapter, Upstream
from rai_gateway.identity.service import CredentialService, RuntimeIdentityService
from rai_gateway.provider_gateway.credentials.domain import SecretBackend
from rai_gateway.provider_gateway.credentials.isolated_resolver import (
    IsolatedProviderCredentialResolver,
)
from rai_gateway.provider_gateway.credentials.secret_resolver import (
    EnvironmentSecretResolver,
)
from rai_gateway.provider_gateway.credentials.service import ProviderCredentialService
from rai_gateway.provider_gateway.proxy import AdapterRegistry, ProviderProxy
from rai_gateway.provider_gateway.transport import UpstreamTransport
from rai_gateway.provider_gateway.workload_identity import (
    extract_workload_principal,
    resolve_runtime_identity_id,
)
from rai_gateway.registry.domain import AgentStatus, AgentVersionStatus, AISystemStatus
from rai_gateway.registry.service import RegistryService
from rai_gateway.runtime_enforcement.repository import RuntimeEnforcementRepository

ISOLATED_UPSTREAM_KEY = "gateway-isolated-upstream-key"
ENV_REF = "RAI_TEST_PROVIDER_UPSTREAM_KEY"


@pytest.fixture()
def isolated_stack(db_session, test_organization, monkeypatch) -> dict:
    monkeypatch.setenv(ENV_REF, ISOLATED_UPSTREAM_KEY)
    registry = RegistryService(db_session)
    creds = ProviderCredentialService(db_session)
    suffix = uuid.uuid4().hex[:8]
    system = registry.create_ai_system(
        system_key=f"iso-sys-{suffix}", name="ISO System", created_by="tester"
    )
    for status in (AISystemStatus.UNDER_REVIEW, AISystemStatus.APPROVED):
        registry.transition_ai_system(system.id, status.value, updated_by="tester")
    agent = registry.create_agent(
        system.id,
        agent_key=f"iso-agent-{suffix}",
        name="ISO Agent",
        created_by="tester",
    )
    for status in (AgentStatus.UNDER_REVIEW, AgentStatus.APPROVED):
        registry.transition_agent(agent.id, status.value, updated_by="tester")
    version = registry.create_agent_version(agent.id, created_by="tester")
    model_name = f"iso-model-{suffix}"
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
        name="ISO Runtime",
        created_by="tester",
    )
    issued = CredentialService(db_session).issue_credential(
        identity.id, created_by="tester"
    )

    ref = creds.create_credential_ref(
        credential_key=f"fakeprov-{suffix}",
        provider="fakeprov",
        credential_type="API_KEY",
        secret_backend=SecretBackend.ENVIRONMENT.value,
        secret_reference=ENV_REF,
        created_by="tester",
        base_url_override="https://upstream.test/v1",
    )
    creds.grant_to_runtime(
        runtime_identity_id=identity.id,
        provider_credential_id=ref.id,
        allowed_providers=["fakeprov"],
        created_by="tester",
    )

    return {
        "identity": identity,
        "model_name": model_name,
        "secret": issued.plaintext_secret,
        "credential_ref": ref,
    }


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"ok": True, "usage": {"in": 1, "out": 1, "total": 2}}
    )


@pytest.fixture()
def isolated_proxy(db_session):
    def _make(handler):
        upstream = Upstream(handler)
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        transport = UpstreamTransport(client, max_retries=0, backoff_seconds=0)
        resolver = IsolatedProviderCredentialResolver(
            db_session,
            secrets=EnvironmentSecretResolver(),
        )
        proxy = ProviderProxy(
            AdapterRegistry([FakeAdapter()]),
            transport=transport,
            credentials=resolver,
            mode="ENFORCE",
            session_scope=lambda: nullcontext(db_session),
        )
        return proxy, upstream

    return _make


async def test_isolated_resolver_injects_gateway_key_not_client(
    db_session, isolated_stack, isolated_proxy
) -> None:
    proxy, upstream = isolated_proxy(_ok)
    body = json.dumps({"model": isolated_stack["model_name"]}).encode()
    from rai_gateway.provider_gateway.domain import ProxyRequest

    request = ProxyRequest(
        method="POST",
        path="/fake/chat",
        headers={
            "authorization": f"Bearer {isolated_stack['secret']}",
            "api-key": "sk-client-openai",
            "content-type": "application/json",
        },
        body=body,
    )
    response = await proxy.handle(request, db=db_session)
    assert response.status_code == 200
    sent = upstream.requests[0]
    assert sent.headers["authorization"] == f"Bearer {ISOLATED_UPSTREAM_KEY}"
    assert "api-key" not in sent.headers
    assert ISOLATED_UPSTREAM_KEY not in isolated_stack["secret"]


async def test_wrong_environment_grant_denied(
    db_session, isolated_stack, isolated_proxy
) -> None:
    from rai_gateway.models.provider_credentials import ProviderCredentialGrant

    grant = (
        db_session.query(ProviderCredentialGrant)
        .filter(
            ProviderCredentialGrant.runtime_identity_id == isolated_stack["identity"].id
        )
        .one()
    )
    grant.environment = "production"
    db_session.commit()
    proxy, upstream = isolated_proxy(_ok)
    from rai_gateway.provider_gateway.domain import ProxyRequest

    request = ProxyRequest(
        method="POST",
        path="/fake/chat",
        headers={"authorization": f"Bearer {isolated_stack['secret']}"},
        body=json.dumps({"model": isolated_stack["model_name"]}).encode(),
    )
    response = await proxy.handle(request, db=db_session)
    assert response.status_code == 403
    assert upstream.requests == []
    payload = json.loads(response.body)
    assert payload["error"]["code"] == "PROVIDER_CREDENTIAL_SCOPE_DENIED"


async def test_secret_not_in_receipt(
    db_session, isolated_stack, isolated_proxy
) -> None:
    proxy, upstream = isolated_proxy(_ok)
    from rai_gateway.provider_gateway.domain import ProxyRequest

    request = ProxyRequest(
        method="POST",
        path="/fake/chat",
        headers={
            "authorization": f"Bearer {isolated_stack['secret']}",
            "content-type": "application/json",
        },
        body=json.dumps({"model": isolated_stack["model_name"]}).encode(),
    )
    response = await proxy.handle(request, db=db_session)
    receipt = RuntimeEnforcementRepository(db_session).get_receipt(
        __import__("uuid").UUID(response.receipt_id)
    )
    blob = json.dumps(
        [receipt.execution_report, receipt.action_summary, receipt.client_context],
        default=str,
    )
    assert ISOLATED_UPSTREAM_KEY not in blob
    assert os.environ[ENV_REF] not in blob
    assert receipt.execution_report.get("provider_credential_ref_id") == str(
        isolated_stack["credential_ref"].id
    )


def test_environment_secret_resolver_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("RAI_PG_TEST_SECRET", "super-secret")
    resolved = EnvironmentSecretResolver().resolve(
        SecretBackend.ENVIRONMENT.value, "RAI_PG_TEST_SECRET"
    )
    assert resolved.payload["value"] == "super-secret"


def test_workload_principal_extraction_and_binding(db_session, isolated_stack) -> None:
    svc = ProviderCredentialService(db_session)
    svc.bind_workload_principal(
        cloud_provider="GCP",
        principal_identifier="my-sa@project.iam.gserviceaccount.com",
        runtime_identity_id=isolated_stack["identity"].id,
        created_by="tester",
    )
    principal = extract_workload_principal(
        {
            "X-Goog-Authenticated-User-Email": "accounts.google.com:my-sa@project.iam.gserviceaccount.com"
        }
    )
    assert principal is not None
    mapped = resolve_runtime_identity_id(db_session, principal)
    assert mapped == str(isolated_stack["identity"].id)


def test_rotation_cutover_uses_new_secret(
    db_session, isolated_stack, monkeypatch
) -> None:
    svc = ProviderCredentialService(db_session)
    ref = isolated_stack["credential_ref"]
    monkeypatch.setenv("RAI_TEST_PROVIDER_UPSTREAM_KEY_V2", "rotated-key")
    svc.begin_rotation(
        ref.id,
        pending_secret_reference="RAI_TEST_PROVIDER_UPSTREAM_KEY_V2",
        actor="tester",
    )
    svc.validate_pending(ref.id, actor="tester")
    updated = svc.cutover(ref.id, actor="tester")
    assert updated.secret_reference == "RAI_TEST_PROVIDER_UPSTREAM_KEY_V2"
    assert updated.pending_secret_reference is None
    resolved = EnvironmentSecretResolver().resolve(
        SecretBackend.ENVIRONMENT.value, updated.secret_reference
    )
    assert resolved.payload["value"] == "rotated-key"
