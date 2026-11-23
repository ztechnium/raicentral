"""Explicit AI configuration and worker-side synthetic probes.

All provider traffic here goes through a mocked httpx transport. Nothing in this
file contacts a real provider, so these tests prove protocol/ownership behavior,
not live availability of any model.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from onboarding_support import Intake

from rai_gateway.core.config import settings
from rai_gateway.governance.domain import GovernanceRole
from rai_gateway.models.database import AdminAudit
from rai_gateway.models.onboarding import (
    OnboardingAIConfiguration,
    OnboardingProviderProbe,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding import ai_adapter, preparation, provider_probe
from rai_gateway.onboarding.ai_configuration import AIConfigService
from rai_gateway.onboarding.service import PROBE_JOB_TYPE, IntakeError
from rai_gateway.onboarding.storage import FilesystemSourceStore
from rai_gateway.sdk.jobs import JobQueue

BASE = "/v1/productization/onboarding"
SECRET = "sk-test-secret-value-123456"
PUBLIC_IP = "93.184.216.34"
TARGET = {
    "key": "acme-chat",
    "label": "Acme Chat",
    "protocol": "OPENAI_COMPATIBLE_CHAT",
    "base_url": "https://llm.acme.example/v1",
    "models": ["acme-1", "acme-2"],
    "structured_modes": ["JSON_SCHEMA", "JSON_OBJECT"],
    "token_param": "max_tokens",
    "max_output_tokens": 512,
    "credential_alias": "ACME",
    "credential_version": 1,
    "timeout_seconds": 5,
}


class Wire:
    """Programmable mocked provider transport that records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responder = self.ok

    @staticmethod
    def nonce_of(request: httpx.Request) -> str:
        body = json.loads(request.content)
        instruction = body["messages"][1]["content"]
        return json.loads(instruction.split(": ", 1)[1])["nonce"]

    def reply(self, request: httpx.Request, content, **extra) -> httpx.Response:
        payload = {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 9, "total_tokens": 39},
        }
        payload.update(extra)
        return httpx.Response(200, json=payload)

    def ok(self, request: httpx.Request) -> httpx.Response:
        return self.reply(
            request, json.dumps({"ok": True, "nonce": self.nonce_of(request)})
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request)


@pytest.fixture()
def wire(monkeypatch):
    w = Wire()
    monkeypatch.setattr(settings, "onboarding_ai_targets_json", json.dumps([TARGET]))
    monkeypatch.setenv("ONBOARDING_AI_KEY_ACME", SECRET)
    monkeypatch.setattr(ai_adapter, "resolve_host", lambda host: [PUBLIC_IP])
    monkeypatch.setattr(
        ai_adapter, "build_transport", lambda: httpx.MockTransport(w.handler)
    )
    return w


@pytest.fixture()
def store(storage_root):
    return FilesystemSourceStore(storage_root)


def put(api, version=None, **over):
    current = api.client.get(f"{BASE}/ai-configuration", headers=api.headers).json()
    body = {
        "expected_configuration_version": version or current["configuration_version"],
        "enabled": True,
        "target_key": "acme-chat",
        "model": "acme-1",
        "structured_mode": "JSON_SCHEMA",
        "max_output_tokens": 256,
    }
    body.update(over)
    return api.client.put(f"{BASE}/ai-configuration", json=body, headers=api.headers)


def status(api):
    return api.client.get(f"{BASE}/ai-configuration", headers=api.headers).json()


def ask_test(api, key=None, current=None, version=None, fp=None):
    current = current or status(api)
    return api.client.post(
        f"{BASE}/ai-configuration/test",
        json={
            "expected_configuration_version": version
            or current["configuration_version"],
            "expected_target_fingerprint": fp or current["target_fingerprint"],
        },
        headers={**api.headers, "Idempotency-Key": key or uuid.uuid4().hex},
    )


def worker(session_factory, store):
    return preparation.run_pass(session_factory, store, worker_id="ai-test")


def probe(db_session, number=None) -> OnboardingProviderProbe:
    db_session.expire_all()
    query = db_session.query(OnboardingProviderProbe).order_by(
        OnboardingProviderProbe.probe_number.desc()
    )
    return query.first()


# ------------------------------------------------------------ configuration
def test_initial_state_is_unconfigured_and_targets_are_sanitized(api, wire):
    state = status(api)
    assert state["state"] == "UNCONFIGURED" and state["enabled"] is False
    targets = api.client.get(f"{BASE}/provider-targets", headers=api.headers).json()
    assert targets["items"][0]["key"] == "acme-chat"
    assert targets["items"][0]["credential_configured"] is True
    assert targets["items"][0]["destination_label"] == "llm.acme.example"
    text = json.dumps(targets)
    for leak in (SECRET, "ACME", "ONBOARDING_AI_KEY", "credential_alias"):
        assert leak not in text


def test_no_targets_means_actionable_empty_state_not_a_default(api, monkeypatch):
    monkeypatch.setattr(settings, "onboarding_ai_targets_json", "")
    assert (
        api.client.get(f"{BASE}/provider-targets", headers=api.headers).json()["items"]
        == []
    )
    refused = put(api)
    assert (
        refused.status_code == 422
        and refused.json()["error"]["code"] == "UNKNOWN_TARGET"
    )


def test_save_makes_no_provider_call_and_validates_the_approved_choice(api, wire):
    saved = put(api)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert wire.requests == []  # saving never contacts the provider
    assert body["state"] == "CONFIGURED_UNTESTED" and body["configuration_version"] == 2
    assert (
        body["credential_configured"] is True
        and body["destination_label"] == "llm.acme.example"
    )
    assert SECRET not in json.dumps(body)

    for bad, code in (
        ({"model": "rogue-model"}, "MODEL_NOT_ALLOWED"),
        ({"target_key": "evil"}, "UNKNOWN_TARGET"),
        ({"max_output_tokens": 100_000}, "INVALID_TOKEN_LIMIT"),
    ):
        res = put(api, **bad)
        assert res.status_code in (422,), (bad, res.text)
    stale = put(api, version=1)
    assert (
        stale.status_code == 409
        and stale.json()["error"]["code"] == "CONFIGURATION_CONFLICT"
    )
    assert (
        api.client.put(
            f"{BASE}/ai-configuration",
            json={
                "expected_configuration_version": 2,
                "enabled": True,
                "target_key": "acme-chat",
                "model": "acme-1",
                "structured_mode": "STREAMING",
            },
            headers=api.headers,
        ).status_code
        == 422
    )


def test_invalid_registry_entries_are_dropped_not_trusted(api, monkeypatch):
    bad = [
        {**TARGET, "key": "http-one", "base_url": "http://insecure.example/v1"},
        {**TARGET, "key": "creds", "base_url": "https://user:pw@llm.example/v1"},
        {**TARGET, "key": "meta", "base_url": "https://169.254.169.254/v1"},
        {**TARGET, "key": "native", "protocol": "ANTHROPIC_NATIVE"},
        {**TARGET, "key": "query", "base_url": "https://llm.example/v1?api_key=x"},
        {**TARGET, "key": "alias", "credential_alias": "bad alias; rm -rf"},
        TARGET,
    ]
    monkeypatch.setattr(settings, "onboarding_ai_targets_json", json.dumps(bad))
    view = api.client.get(f"{BASE}/provider-targets", headers=api.headers).json()
    assert [t["key"] for t in view["items"]] == ["acme-chat"] and view[
        "invalid_entries"
    ] == 6


def test_provider_permission_is_separate_from_source_and_manage(
    client, make_user, wire
):
    officer = Intake(
        client, make_user(GovernanceRole.RAI_OFFICER.value)
    )  # sdk:manage + sources
    assert put(officer, version=1).status_code == 403
    assert (
        officer.client.get(
            f"{BASE}/provider-targets", headers=officer.headers
        ).status_code
        == 403
    )
    assert (
        officer.client.get(
            f"{BASE}/ai-configuration/tests", headers=officer.headers
        ).status_code
        == 403
    )
    reader = Intake(client, make_user(GovernanceRole.AI_SYSTEM_OWNER.value))
    summary = reader.client.get(f"{BASE}/ai-configuration", headers=reader.headers)
    assert summary.status_code == 200 and set(summary.json()) <= {
        "state",
        "enabled",
        "available_targets",
        "last_test_at",
    }
    assert "acme" not in summary.text
    admin = Intake(client, make_user(GovernanceRole.ORGANIZATION_ADMIN.value))
    assert put(admin).status_code == 200
    ctx = admin.client.get("/v1/auth/context", headers=admin.headers)
    assert "onboarding:providers:manage" in json.dumps(ctx.json())


# --------------------------------------------------------------------- probes
def test_synthetic_probe_runs_in_the_worker_and_sends_only_the_synthetic_request(
    api, wire, session_factory, store, db_session
):
    put(api)
    before = status(api)
    accepted = ask_test(api, key="test-key-0001")
    assert accepted.status_code == 202, accepted.text
    again = ask_test(api, key="test-key-0002")  # concurrent click: same active probe
    assert accepted.json()["probe"]["id"] == again.json()["probe"]["id"]
    assert status(api)["state"] == "TEST_PENDING" and wire.requests == []

    stats = worker(session_factory, store)
    assert stats["succeeded"] == 1 and len(wire.requests) == 1
    done = status(api)
    assert done["state"] == "TEST_PASSED"
    last = done["last_test"]
    assert (
        last["outcome"] == "PASSED"
        and last["configuration_version"] == before["configuration_version"]
    )
    assert last["usage"] == {
        "prompt_tokens": 30,
        "completion_tokens": 9,
        "total_tokens": 39,
    }
    assert last["latency_ms"] is not None and last["dispatch_started"] is True

    request = wire.requests[0]
    body = json.loads(request.content)
    assert request.headers["host"] == "llm.acme.example"
    assert request.url.host == PUBLIC_IP  # connected to the validated, pinned address
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert (
        body["model"] == "acme-1"
        and body["max_tokens"] == 256
        and body["stream"] is False
    )
    assert body["response_format"]["type"] == "json_schema"
    assert (
        body["response_format"]["json_schema"]["schema"]["additionalProperties"]
        is False
    )
    assert set(body) == {"model", "messages", "max_tokens", "stream", "response_format"}
    sent = json.dumps(body)
    for forbidden in ("policy", "inventory", "Onboard", "extract"):
        assert forbidden not in sent
    assert request.extensions["sni_hostname"] == "llm.acme.example"

    # No secret anywhere we persist or return.
    dump = json.dumps(done) + json.dumps(
        api.client.get(f"{BASE}/ai-configuration/tests", headers=api.headers).json()
    )
    db_session.expire_all()
    for model in (
        BackgroundJob,
        AdminAudit,
        OnboardingProviderProbe,
        OnboardingAIConfiguration,
    ):
        dump += " ".join(str(vars(r)) for r in db_session.query(model).all())
    assert SECRET not in dump


def test_json_object_mode_is_explicit_and_locally_validated(
    api, wire, session_factory, store
):
    put(api, structured_mode="JSON_OBJECT")
    ask_test(api)
    worker(session_factory, store)
    body = json.loads(wire.requests[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert "JSON" in body["messages"][1]["content"]
    assert status(api)["state"] == "TEST_PASSED"


@pytest.mark.parametrize(
    "responder,code",
    [
        (
            lambda w, r: w.reply(r, json.dumps({"ok": True, "nonce": "wrong"})),
            "NONCE_MISMATCH",
        ),
        (
            lambda w, r: w.reply(r, json.dumps({"ok": "true", "nonce": w.nonce_of(r)})),
            "SCHEMA_MISMATCH",
        ),
        (
            lambda w, r: w.reply(
                r, json.dumps({"ok": True, "nonce": w.nonce_of(r), "extra": 1})
            ),
            "SCHEMA_MISMATCH",
        ),
        (lambda w, r: w.reply(r, "not json at all"), "INVALID_JSON"),
        (lambda w, r: w.reply(r, ""), "EMPTY_RESPONSE"),
        (
            lambda w, r: w.reply(
                r,
                json.dumps({"ok": True, "nonce": w.nonce_of(r)}),
                choices=[{"message": {"content": "{"}, "finish_reason": "length"}],
            ),
            "TRUNCATED_OUTPUT",
        ),
        (
            lambda w, r: w.reply(
                r,
                None,
                choices=[
                    {
                        "message": {"content": None, "refusal": "no"},
                        "finish_reason": "stop",
                    }
                ],
            ),
            "REFUSAL",
        ),
        (lambda w, r: httpx.Response(401, json={"error": "bad key"}), "AUTH_REJECTED"),
        (lambda w, r: httpx.Response(429), "RATE_LIMITED"),
        (
            lambda w, r: httpx.Response(
                400, json={"error": "unsupported response_format"}
            ),
            "REQUEST_REJECTED",
        ),
        (lambda w, r: httpx.Response(404), "MODEL_OR_ENDPOINT_NOT_FOUND"),
        (lambda w, r: httpx.Response(503), "PROVIDER_ERROR"),
        (
            lambda w, r: httpx.Response(
                302, headers={"location": "https://evil.example/x"}
            ),
            "REDIRECT_REJECTED",
        ),
        (
            lambda w, r: httpx.Response(200, content=b"x" * (70 * 1024)),
            "RESPONSE_TOO_LARGE",
        ),
        (
            lambda w, r: httpx.Response(200, json={"unexpected": True}),
            "INVALID_RESPONSE",
        ),
    ],
)
def test_failures_are_explicit_never_repaired_never_retried(
    api, wire, session_factory, store, db_session, responder, code
):
    put(api)
    wire.responder = lambda r: responder(wire, r)
    ask_test(api)
    worker(session_factory, store)
    assert len(wire.requests) == 1  # no automatic retry, no fallback provider
    p = probe(db_session)
    assert p.outcome == "FAILED" and p.error_code == code
    assert status(api)["state"] == "TEST_FAILED"
    job = db_session.get(BackgroundJob, p.job_id)
    assert job.status == "FAILED" and job.attempt_count == 1 and job.max_attempts == 1


def test_missing_usage_is_unknown_not_zero(
    api, wire, session_factory, store, db_session
):
    put(api)
    wire.responder = lambda r: httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"ok": True, "nonce": wire.nonce_of(r)})
                    },
                    "finish_reason": "stop",
                }
            ]
        },
    )
    ask_test(api)
    worker(session_factory, store)
    assert probe(db_session).outcome == "PASSED" and probe(db_session).usage is None


def test_timeout_after_dispatch_is_unknown_and_never_resent(
    api, wire, session_factory, store, db_session
):
    put(api)

    def lost(request):
        raise httpx.ReadTimeout("slow", request=request)

    wire.responder = lost
    ask_test(api)
    worker(session_factory, store)
    worker(session_factory, store)  # a second pass must not resend
    assert len(wire.requests) == 1
    p = probe(db_session)
    assert p.outcome == "OUTCOME_UNKNOWN" and p.dispatch_started_at is not None
    assert status(api)["state"] == "TEST_UNKNOWN"
    # An explicit retest with a new key is a deliberate new probe.
    wire.responder = wire.ok
    again = ask_test(api)
    assert again.json()["probe"]["probe_number"] == p.probe_number + 1
    worker(session_factory, store)
    assert status(api)["state"] == "TEST_PASSED" and len(wire.requests) == 2


def test_connect_failure_is_a_known_failure_not_unknown(
    api, wire, session_factory, store, db_session
):
    put(api)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    wire.responder = refused
    ask_test(api)
    worker(session_factory, store)
    assert (
        probe(db_session).error_code == "CONNECT_FAILED"
        and probe(db_session).outcome == "FAILED"
    )


def test_missing_credential_fails_before_any_network_and_has_no_fallback(
    api, wire, session_factory, store, db_session, monkeypatch
):
    put(api)
    ask_test(api)
    monkeypatch.delenv("ONBOARDING_AI_KEY_ACME")
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "sk-runtime-key-must-not-be-used")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-key-must-not-be-used")
    worker(session_factory, store)
    p = probe(db_session)
    assert p.error_code == "CREDENTIAL_UNAVAILABLE" and p.dispatch_started_at is None
    assert wire.requests == []


@pytest.mark.parametrize(
    "addresses,allow_private,rejected",
    [
        (["169.254.169.254"], False, True),
        (["169.254.169.254"], True, True),  # metadata is never allowed
        (["127.0.0.1"], False, True),
        (["::1"], False, True),
        (["10.1.2.3"], False, True),
        (["192.168.0.4"], False, True),
        (["fe80::1"], False, True),
        (["::ffff:10.0.0.9"], False, True),
        (["fd00:ec2::254"], True, True),
        (["0.0.0.0"], False, True),
        (
            [PUBLIC_IP, "10.9.9.9"],
            False,
            True,
        ),  # one bad address in the set rejects all
        (["10.1.2.3"], True, False),
        (["2001:4860:4860::8888"], False, False),
    ],
)
def test_destination_policy_blocks_unsafe_and_metadata_addresses(
    api,
    wire,
    session_factory,
    store,
    db_session,
    monkeypatch,
    addresses,
    allow_private,
    rejected,
):
    monkeypatch.setattr(
        settings,
        "onboarding_ai_targets_json",
        json.dumps([{**TARGET, "allow_private_network": allow_private}]),
    )
    monkeypatch.setattr(ai_adapter, "resolve_host", lambda host: addresses)
    put(api)
    ask_test(api)
    worker(session_factory, store)
    p = probe(db_session)
    if rejected:
        assert p.error_code == "DESTINATION_REJECTED" and wire.requests == []
        assert p.dispatch_started_at is None  # blocked before the dispatch marker
    else:
        assert p.outcome == "PASSED" and len(wire.requests) == 1


def test_dns_change_between_probes_is_validated_each_time_and_pinned(
    api, wire, session_factory, store, db_session, monkeypatch
):
    put(api)
    ask_test(api)
    worker(session_factory, store)
    assert wire.requests[0].url.host == PUBLIC_IP
    monkeypatch.setattr(
        ai_adapter, "resolve_host", lambda host: ["127.0.0.1"]
    )  # rebinding
    ask_test(api)
    worker(session_factory, store)
    assert (
        probe(db_session).error_code == "DESTINATION_REJECTED"
        and len(wire.requests) == 1
    )


def test_api_worker_target_policy_mismatch_fails_before_secret_or_network(
    api, wire, session_factory, store, db_session, monkeypatch
):
    put(api)
    ask_test(api)  # probe frozen with credential_version 1
    monkeypatch.setattr(
        settings,
        "onboarding_ai_targets_json",
        json.dumps([{**TARGET, "credential_version": 2}]),
    )
    resolved = []
    monkeypatch.setattr(
        provider_probe, "resolve_secret", lambda target: resolved.append(1) or SECRET
    )
    worker(session_factory, store)
    p = probe(db_session)
    assert (
        p.error_code == "TARGET_POLICY_MISMATCH"
        and resolved == []
        and wire.requests == []
    )
    # The old test no longer certifies the new policy.
    assert status(api)["state"] in ("TEST_STALE", "CONFIGURED_UNTESTED")


def test_config_and_policy_changes_invalidate_matching_but_not_history(
    api, wire, session_factory, store, monkeypatch
):
    put(api)
    ask_test(api)
    worker(session_factory, store)
    assert status(api)["state"] == "TEST_PASSED"
    put(api, model="acme-2")  # new configuration version
    stale = status(api)
    assert stale["state"] == "TEST_STALE" and stale["last_test"] is None
    history = api.client.get(
        f"{BASE}/ai-configuration/tests", headers=api.headers
    ).json()
    assert (
        history["total"] == 1 and history["items"][0]["outcome"] == "PASSED"
    )  # kept as history
    monkeypatch.setattr(
        settings,
        "onboarding_ai_targets_json",
        json.dumps([{**TARGET, "credential_version": 9}]),
    )
    assert status(api)["target_fingerprint"] != stale["target_fingerprint"]


def test_disabled_or_changed_before_dispatch_never_sends(
    api, wire, session_factory, store, db_session
):
    put(api)
    ask_test(api)
    put(api, enabled=False)  # disabled after the click, before the worker runs
    worker(session_factory, store)
    p = probe(db_session)
    assert (
        p.outcome == "FAILED" and p.dispatch_started_at is None and wire.requests == []
    )


def test_change_between_validation_and_marker_blocks_the_send(
    api, wire, session_factory, store, db_session, monkeypatch
):
    put(api)
    ask_test(api)
    real = ai_adapter.prepare_request

    def prepare_then_disable(snapshot, secret, nonce):
        prepared = real(snapshot, secret, nonce)
        db = session_factory()
        db.query(OnboardingAIConfiguration).update({"enabled": False})
        db.commit()
        db.close()
        return prepared

    monkeypatch.setattr(ai_adapter, "prepare_request", prepare_then_disable)
    worker(session_factory, store)
    p = probe(db_session)
    assert (
        p.error_code == "CONFIGURATION_CHANGED_BEFORE_DISPATCH" and wire.requests == []
    )
    assert p.dispatch_started_at is None


def test_reaper_terminalizes_probes_and_unknown_follows_the_marker(
    api, wire, session_factory, store, db_session
):
    put(api)
    ask_test(api)
    p0 = probe(db_session)
    db = session_factory()
    claimed = JobQueue(db).claim_leased(
        job_types=[PROBE_JOB_TYPE], worker_id="w", lease_seconds=60
    )
    token = claimed.lease_token
    db.commit()
    # Crash AFTER the dispatch marker: outcome is unknown, never failed/auto-resent.
    assert provider_probe._mark_dispatch(
        db, p0.id, token, p0.configuration_version, p0.target_fingerprint
    )
    db.query(BackgroundJob).filter(BackgroundJob.id == claimed.id).update(
        {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}
    )
    db.commit()
    db.close()
    worker(session_factory, store)
    p = probe(db_session)
    assert (
        p.outcome == "OUTCOME_UNKNOWN" and p.error_code == "WORKER_LOST_AFTER_DISPATCH"
    )
    assert p.ended_at is not None and wire.requests == []
    job = db_session.get(BackgroundJob, p.job_id)
    assert job.status == "DEAD_LETTER"

    # A delayed old pass cannot overwrite a newer result.
    ask_test(api)  # probe 2
    wire.responder = lambda r: httpx.Response(401)
    worker(session_factory, store)
    assert status(api)["state"] == "TEST_FAILED"
    db = session_factory()
    assert (
        provider_probe._close(db, p.id, token, outcome="PASSED", error_code=None)
        is False
    )
    db.close()
    assert (
        status(api)["state"] == "TEST_FAILED"
        and status(api)["last_test"]["probe_number"] == 2
    )


def test_probe_lost_before_dispatch_is_a_known_failure(
    api, wire, session_factory, store, db_session
):
    put(api)
    ask_test(api)
    db = session_factory()
    claimed = JobQueue(db).claim_leased(
        job_types=[PROBE_JOB_TYPE], worker_id="w", lease_seconds=60
    )
    db.commit()
    db.query(BackgroundJob).filter(BackgroundJob.id == claimed.id).update(
        {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=5)}
    )
    db.commit()
    db.close()
    worker(session_factory, store)
    p = probe(db_session)
    assert (
        p.outcome == "FAILED"
        and p.error_code == "WORKER_LOST"
        and p.ended_at is not None
    )


def test_cancelled_probe_fences_late_results(
    api, wire, session_factory, store, db_session
):
    put(api)
    pid = ask_test(api).json()["probe"]["id"]
    cancelled = api.client.post(
        f"{BASE}/ai-configuration/tests/{pid}/cancel", headers=api.headers
    )
    assert cancelled.status_code == 200 and cancelled.json()["outcome"] == "CANCELLED"
    assert worker(session_factory, store)["processed"] == 0  # nothing left to claim
    assert wire.requests == []
    assert status(api)["state"] == "CONFIGURED_UNTESTED"


def test_probe_job_requires_its_persisted_binding_and_is_reserved(
    api, wire, session_factory, store, db_session
):
    db = session_factory()
    db.add(
        BackgroundJob(
            job_type=PROBE_JOB_TYPE,
            status="PENDING",
            payload={"probe_id": str(uuid.uuid4())},
            max_attempts=1,
            next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    db.commit()
    db.close()
    stats = worker(session_factory, store)
    assert stats["failed"] == 1 and wire.requests == []
    rejected = api.client.post(
        "/v1/productization/jobs",
        json={"job_type": PROBE_JOB_TYPE, "payload": {}},
        headers=api.headers,
    )
    assert rejected.status_code == 422


def test_concurrent_test_clicks_create_one_probe_and_retests_are_ordered(
    api, wire, session_factory, store, db_session
):
    put(api)
    current = status(api)
    results: list[uuid.UUID] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def click(n: int) -> None:
        service = AIConfigService(session_factory(), actor="thread-user")
        try:
            barrier.wait()
            p = service.request_test(
                expected_configuration_version=current["configuration_version"],
                expected_target_fingerprint=current["target_fingerprint"],
                idempotency_key=f"concurrent-test-{n}",
            )
            results.append(p.id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            service.db.close()

    threads = [threading.Thread(target=click, args=(i,)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert len(set(results)) == 1
    db_session.expire_all()
    assert db_session.query(OnboardingProviderProbe).count() == 1
    worker(session_factory, store)
    second = ask_test(api).json()["probe"]
    assert second["probe_number"] == 2  # a deliberate retest after completion


def test_test_request_validates_version_fingerprint_and_idempotency(api, wire):
    assert (
        ask_test(
            api, current={"configuration_version": 1, "target_fingerprint": "x" * 16}
        ).status_code
        == 409
    )
    put(api)
    current = status(api)
    stale = ask_test(api, current=current, version=current["configuration_version"] - 1)
    assert (
        stale.status_code == 409
        and stale.json()["error"]["code"] == "CONFIGURATION_CONFLICT"
    )
    changed = ask_test(api, current=current, fp="f" * 32)
    assert (
        changed.status_code == 409
        and changed.json()["error"]["code"] == "TARGET_POLICY_CHANGED"
    )
    first = ask_test(api, current=current, key="idem-key-0001")
    replay = ask_test(api, current=current, key="idem-key-0001")
    assert first.json()["probe"]["id"] == replay.json()["probe"]["id"]
    conflict = api.client.post(
        f"{BASE}/ai-configuration/test",
        json={
            "expected_configuration_version": current["configuration_version"],
            "expected_target_fingerprint": "z" * 16,
        },
        headers={**api.headers, "Idempotency-Key": "idem-key-0001"},
    )
    assert conflict.status_code in (409,)
    disabled = put(api, enabled=False)
    res = ask_test(
        api,
        current=status(api),
        fp="a" * 16,
        version=disabled.json()["configuration_version"],
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "AI_NOT_CONFIGURED"


def test_unsupported_modes_are_rejected_without_downgrade(api, wire):
    res = put(api, structured_mode="JSON_OBJECT")
    assert res.status_code == 200
    only_object = {**TARGET, "structured_modes": ["JSON_OBJECT"]}
    settings.onboarding_ai_targets_json = json.dumps([only_object])
    refused = put(api, structured_mode="JSON_SCHEMA")
    assert (
        refused.status_code == 422
        and refused.json()["error"]["code"] == "MODE_NOT_SUPPORTED"
    )
    assert isinstance(IntakeError, type)


# ------------------------------------------------------- unified RAI_AI_* config
def test_env_configuration_applies_itself_and_blocks_ui_changes(
    api, session_factory, store, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "rai_ai_provider", "vertex")
    monkeypatch.setattr(settings, "rai_ai_model", "gemini-x")
    monkeypatch.setattr(settings, "rai_ai_api_key", "AIza-test-key-123456")
    monkeypatch.setattr(ai_adapter, "resolve_host", lambda host: [PUBLIC_IP])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        nonce = json.loads(
            json.loads(request.content)["contents"][0]["parts"][0]["text"].split(": ", 1)[1]
        )["nonce"]
        text = json.dumps({"ok": True, "nonce": nonce})
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 4, "totalTokenCount": 9},
            },
        )

    monkeypatch.setattr(ai_adapter, "build_transport", lambda: httpx.MockTransport(handler))

    # Nothing to choose or save: the environment is applied on first read.
    state = status(api)
    assert state["managed_by_environment"] is True
    assert state["enabled"] is True and state["model"] == "gemini-x"
    assert state["provider"] == "vertex" and state["credential_configured"] is True
    assert state["state"] == "CONFIGURED_UNTESTED"
    assert "AIza-test-key" not in json.dumps(state)

    # The UI cannot override it.
    refused = put(api, target_key="rai-ai", model="gemini-x", structured_mode="JSON_OBJECT")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "AI_MANAGED_BY_ENVIRONMENT"

    # The connection test reaches the Vertex express endpoint with the key header only.
    assert ask_test(api).status_code in (200, 202)
    worker(session_factory, store)
    request = seen[0]
    assert request.url.path == "/v1/publishers/google/models/gemini-x:generateContent"
    assert request.headers["x-goog-api-key"] == "AIza-test-key-123456"
    assert "authorization" not in request.headers
    assert probe(db_session).outcome == "PASSED"
    assert status(api)["state"] == "TEST_PASSED"

    # Reading again does not churn the configuration version.
    version = status(api)["configuration_version"]
    assert status(api)["configuration_version"] == version
