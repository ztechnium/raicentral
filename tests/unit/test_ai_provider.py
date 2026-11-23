"""The unified RAI_AI_* configuration: validation, wire formats, evaluator + onboarding wiring."""

from __future__ import annotations

import json

import httpx
import pytest

from rai_gateway.core import ai_provider
from rai_gateway.core.config import settings
from rai_gateway.onboarding import ai_adapter
from rai_gateway.onboarding.ai_configuration import (
    ENV_TARGET_KEY,
    current_effective,
    load_targets,
)

SA = {
    "type": "service_account",
    "client_email": "ai@proj.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
}


@pytest.fixture()
def env(monkeypatch):
    def apply(**values):
        defaults = {
            "rai_ai_provider": None,
            "rai_ai_model": None,
            "rai_ai_models": "",
            "rai_ai_api_key": None,
            "rai_ai_base_url": None,
            "rai_ai_gcp_project_id": None,
            "rai_ai_location": None,
            "rai_ai_gcp_service_account_json": None,
            "rai_ai_structured_mode": None,
            "gcp_project_id": None,
        }
        for key, value in {**defaults, **values}.items():
            monkeypatch.setattr(settings, key, value)

    return apply


# ------------------------------------------------------------------ validation
def test_unset_means_no_config(env):
    env()
    assert ai_provider.load_ai_config() is None
    assert ai_provider.try_load_ai_config() == (None, None)


@pytest.mark.parametrize(
    "values,message",
    [
        ({"rai_ai_provider": "nope", "rai_ai_model": "m"}, "must be one of"),
        ({"rai_ai_provider": "openai"}, "RAI_AI_MODEL"),
        ({"rai_ai_provider": "openai", "rai_ai_model": "m"}, "RAI_AI_API_KEY"),
        ({"rai_ai_provider": "anthropic", "rai_ai_model": "m"}, "RAI_AI_API_KEY"),
        ({"rai_ai_provider": "openai_compatible", "rai_ai_model": "m"}, "BASE_URL"),
        ({"rai_ai_provider": "vertex", "rai_ai_model": "m"}, "RAI_AI_GCP_PROJECT_ID"),
        (
            {
                "rai_ai_provider": "vertex",
                "rai_ai_model": "m",
                "rai_ai_gcp_project_id": "p",
                "rai_ai_gcp_service_account_json": "not json",
            },
            "not valid JSON",
        ),
        (
            {
                "rai_ai_provider": "anthropic",
                "rai_ai_model": "m",
                "rai_ai_api_key": "k",
                "rai_ai_structured_mode": "JSON_SCHEMA",
            },
            "not supported",
        ),
    ],
)
def test_invalid_configuration_is_reported(env, values, message):
    env(**values)
    config, problem = ai_provider.try_load_ai_config()
    assert config is None and message in problem


def test_vertex_accepts_api_key_or_service_account(env):
    env(rai_ai_provider="vertex_ai", rai_ai_model="gemini-x", rai_ai_api_key="AIza-key")
    by_key = ai_provider.load_ai_config()
    assert by_key.provider == "vertex" and by_key.auth == "api_key"

    env(
        rai_ai_provider="vertex",
        rai_ai_model="gemini-x",
        rai_ai_gcp_project_id="proj",
        rai_ai_gcp_service_account_json=json.dumps(SA),
    )
    by_account = ai_provider.load_ai_config()
    assert by_account.auth == "service_account"


def test_vertex_falls_back_to_gateway_gcp_settings(env, monkeypatch):
    env(rai_ai_provider="vertex", rai_ai_model="gemini-x", gcp_project_id="gw-proj")
    monkeypatch.setattr(settings, "gcp_location", "europe-west4")
    cfg = ai_provider.load_ai_config()
    assert cfg.gcp_project_id == "gw-proj" and cfg.location == "europe-west4"
    assert cfg.service_account_json is None  # token comes from GCP_SERVICE_ACCOUNT_JSON


def test_models_are_deduplicated_with_primary_first(env):
    env(
        rai_ai_provider="openai",
        rai_ai_model="a",
        rai_ai_models="b, a ,c",
        rai_ai_api_key="k",
    )
    assert ai_provider.load_ai_config().models == ("a", "b", "c")


# ----------------------------------------------------------------- wire formats
@pytest.mark.parametrize(
    "provider,auth,expect_base,expect_path",
    [
        ("openai", "api_key", "https://api.openai.com/v1", "/chat/completions"),
        ("anthropic", "api_key", "https://api.anthropic.com/v1", "/messages"),
        (
            "gemini",
            "api_key",
            "https://generativelanguage.googleapis.com/v1beta",
            "/models/m1:generateContent",
        ),
        (
            "vertex",
            "api_key",
            "https://aiplatform.googleapis.com",
            "/v1/publishers/google/models/m1:generateContent",
        ),
        (
            "vertex",
            "service_account",
            "https://aiplatform.googleapis.com",
            "/v1/projects/p/locations/global/publishers/google/models/m1:generateContent",
        ),
    ],
)
def test_endpoints(provider, auth, expect_base, expect_path):
    assert ai_provider.api_base(provider, location="global") == expect_base
    assert (
        ai_provider.request_path(provider, "m1", auth=auth, project="p", location="global")
        == expect_path
    )


def test_regional_vertex_host():
    assert ai_provider.api_base("vertex", location="europe-west4") == (
        "https://europe-west4-aiplatform.googleapis.com"
    )


def test_auth_headers_per_provider():
    assert ai_provider.auth_headers("openai", "api_key", "k") == {"Authorization": "Bearer k"}
    assert ai_provider.auth_headers("anthropic", "api_key", "k")["x-api-key"] == "k"
    assert ai_provider.auth_headers("gemini", "api_key", "k") == {"x-goog-api-key": "k"}
    assert ai_provider.auth_headers("vertex", "api_key", "k") == {"x-goog-api-key": "k"}
    assert ai_provider.auth_headers("vertex", "service_account", "tok") == {
        "Authorization": "Bearer tok"
    }
    assert ai_provider.auth_headers("openai_compatible", "none", None) == {}


def test_bodies_per_provider():
    common = dict(model="m", system="S", user="U", max_output_tokens=50)
    oa = ai_provider.build_body(
        "openai", structured_mode="JSON_OBJECT", token_param="max_completion_tokens", **common
    )
    assert oa["response_format"] == {"type": "json_object"} and oa["max_completion_tokens"] == 50
    an = ai_provider.build_body("anthropic", structured_mode="JSON_PROMPT", **common)
    assert an["system"] == "S" and an["max_tokens"] == 50 and "temperature" not in an
    ge = ai_provider.build_body("gemini", structured_mode="JSON_OBJECT", temperature=0.0, **common)
    assert ge["generationConfig"]["responseMimeType"] == "application/json"
    assert ge["systemInstruction"]["parts"][0]["text"] == "S"
    with pytest.raises(ai_provider.AIProviderError):
        ai_provider.build_body("openai", structured_mode="JSON_SCHEMA", **common)  # no schema


def test_response_extraction_per_provider():
    oa = ai_provider.extract_response(
        "openai",
        {
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    )
    assert oa.text == "{}" and oa.usage["total_tokens"] == 3 and not oa.truncated
    an = ai_provider.extract_response(
        "anthropic",
        {
            "content": [{"type": "text", "text": "{}"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 4, "output_tokens": 5},
        },
    )
    assert an.truncated and an.usage == {
        "prompt_tokens": 4,
        "completion_tokens": 5,
        "total_tokens": 9,
    }
    ge = ai_provider.extract_response(
        "vertex",
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "think", "thought": True}, {"text": "{}"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        },
    )
    assert ge.text == "{}" and ge.usage["total_tokens"] == 2
    blocked = ai_provider.extract_response("gemini", {"promptFeedback": {"blockReason": "SAFETY"}})
    assert blocked.refused
    with pytest.raises(ai_provider.AIProviderError):
        ai_provider.extract_response("openai", {"nope": 1})


def test_generate_json_text_sends_provider_request(env, monkeypatch):
    env(rai_ai_provider="anthropic", rai_ai_model="claude-x", rai_ai_api_key="sk-ant")
    seen = {}

    def fake_post(url, headers=None, content=None, timeout=None, follow_redirects=None):
        seen.update(url=url, headers=headers, body=json.loads(content))
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "end_turn"},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = ai_provider.generate_json_text(
        ai_provider.load_ai_config(), system="s", user="u"
    )
    assert result.text == '{"ok": true}'
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant"
    assert seen["body"]["model"] == "claude-x"


def test_generate_json_text_maps_failures(env, monkeypatch):
    env(rai_ai_provider="openai", rai_ai_model="m", rai_ai_api_key="k")
    cfg = ai_provider.load_ai_config()
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": "bad key"})
    )
    with pytest.raises(ai_provider.AIProviderError) as err:
        ai_provider.generate_json_text(cfg, system="s", user="u")
    assert err.value.kind == "auth"

    def boom(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(ai_provider.AIProviderError) as err:
        ai_provider.generate_json_text(cfg, system="s", user="u")
    assert err.value.kind == "timeout"


def test_vertex_service_account_uses_google_token(env, monkeypatch):
    from rai_gateway.provider_gateway.credentials import gcp_token

    env(
        rai_ai_provider="vertex",
        rai_ai_model="gemini-x",
        rai_ai_gcp_project_id="proj",
        rai_ai_gcp_service_account_json=json.dumps(SA),
    )
    monkeypatch.setattr(
        gcp_token.gcp_token_provider, "get_token_for_info", lambda info: "tok-" + info["client_email"][:2]
    )
    assert ai_provider.resolve_credential(ai_provider.load_ai_config()) == "tok-ai"


# ------------------------------------------------------------------- onboarding
def test_env_is_the_single_onboarding_target(env, monkeypatch):
    monkeypatch.setattr(
        settings, "onboarding_ai_targets_json", json.dumps([{"key": "legacy"}])
    )
    env(rai_ai_provider="vertex", rai_ai_model="gemini-x", rai_ai_api_key="AIza")
    targets, problems = load_targets()
    assert list(targets) == [ENV_TARGET_KEY] and problems == []
    target = targets[ENV_TARGET_KEY]
    assert target.managed and target.provider == "vertex"
    assert target.base_url == "https://aiplatform.googleapis.com"
    assert target.models == ("gemini-x",) and target.structured_modes == ("JSON_OBJECT",)


def test_invalid_env_is_reported_not_silently_ignored(env):
    env(rai_ai_provider="openai", rai_ai_model="m")  # no key
    targets, problems = load_targets()
    assert targets == {} and any("RAI_AI_API_KEY" in p for p in problems)


def test_http_base_url_is_refused_for_onboarding(env):
    env(
        rai_ai_provider="openai_compatible",
        rai_ai_model="m",
        rai_ai_base_url="http://ollama:11434/v1",
    )
    targets, problems = load_targets()
    assert targets == {} and any("https" in p for p in problems)


@pytest.mark.parametrize(
    "values,host,path_part,header",
    [
        (
            {"rai_ai_provider": "anthropic", "rai_ai_model": "claude-x", "rai_ai_api_key": "k"},
            "api.anthropic.com",
            "/v1/messages",
            "x-api-key",
        ),
        (
            {"rai_ai_provider": "gemini", "rai_ai_model": "g-x", "rai_ai_api_key": "k"},
            "generativelanguage.googleapis.com",
            "/v1beta/models/g-x:generateContent",
            "x-goog-api-key",
        ),
        (
            {"rai_ai_provider": "vertex", "rai_ai_model": "g-x", "rai_ai_api_key": "k"},
            "aiplatform.googleapis.com",
            "/v1/publishers/google/models/g-x:generateContent",
            "x-goog-api-key",
        ),
        (
            {"rai_ai_provider": "openai", "rai_ai_model": "gpt-x", "rai_ai_api_key": "k"},
            "api.openai.com",
            "/v1/chat/completions",
            "Authorization",
        ),
    ],
)
def test_probe_request_per_provider(env, monkeypatch, values, host, path_part, header):
    env(**values)
    monkeypatch.setattr(ai_adapter, "resolve_host", lambda h: ["93.184.216.34"])
    target = load_targets()[0][ENV_TARGET_KEY]

    class Row:
        enabled = True
        target_key = ENV_TARGET_KEY
        model = target.models[0]
        structured_mode = target.structured_modes[0]
        max_output_tokens = 64
        config_version = 1

    _t, snapshot, _fp = current_effective(Row())
    prepared = ai_adapter.prepare_request(snapshot, "sk-secret-zzz", "nonce123")
    assert prepared.sni_hostname == host
    assert path_part in prepared.url
    assert header in prepared.headers
    assert b"sk-secret-zzz" not in prepared.body  # key never in the body


def test_probe_validates_each_provider_response():
    nonce = "n1"
    ok_text = json.dumps({"ok": True, "nonce": nonce})
    shapes = {
        "openai": {"choices": [{"message": {"content": ok_text}, "finish_reason": "stop"}]},
        "anthropic": {"content": [{"type": "text", "text": ok_text}], "stop_reason": "end_turn"},
        "gemini": {"candidates": [{"content": {"parts": [{"text": ok_text}]}, "finishReason": "STOP"}]},
        "vertex": {
            "candidates": [{"content": {"parts": [{"text": "```json\n" + ok_text + "\n```"}]}}]
        },
    }
    for provider, payload in shapes.items():
        result = ai_adapter.validate_response(
            json.dumps(payload).encode(), 200, nonce, started=0.0, provider=provider
        )
        assert result.outcome == ai_adapter.OUTCOME_PASSED, provider
    wrong = ai_adapter.validate_response(
        json.dumps(shapes["anthropic"]).encode(), 200, "other", started=0.0, provider="anthropic"
    )
    assert wrong.error_code == "NONCE_MISMATCH"


# -------------------------------------------------------------------- evaluator
def test_evaluator_defaults_to_unified_config(env):
    from rai_gateway.risk_intelligence.repository import RiskIntelligenceRepository

    env(rai_ai_provider="anthropic", rai_ai_model="claude-x", rai_ai_api_key="k")
    cfg = RiskIntelligenceRepository._env_evaluator_config()
    assert cfg is not None and cfg.provider == "anthropic" and cfg.model == "claude-x"


def test_content_evaluator_settings_override_unified(env, monkeypatch):
    from rai_gateway.risk_intelligence.repository import RiskIntelligenceRepository

    env(rai_ai_provider="anthropic", rai_ai_model="claude-x", rai_ai_api_key="k")
    monkeypatch.setattr(settings, "content_evaluator_provider", "vertex")
    monkeypatch.setattr(settings, "content_evaluator_model", "gemini-x")
    cfg = RiskIntelligenceRepository._env_evaluator_config()
    assert cfg.provider == "vertex" and cfg.model == "gemini-x"


def test_vertex_evaluator_supports_api_key(monkeypatch):
    import io
    import urllib.request

    from rai_gateway.risk_intelligence.providers import (
        EvaluatorRequest,
        ProviderConfig,
        VertexContentEvaluator,
    )

    rule_id = "11111111-1111-1111-1111-111111111111"
    seen = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["key"] = req.headers.get("X-goog-api-key")
        seen["auth"] = req.headers.get("Authorization")
        text = json.dumps({"evaluations": [{"rule_id": rule_id, "violated": False, "confidence": 0.1}]})
        return _Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    VertexContentEvaluator().evaluate(
        EvaluatorRequest(
            content="hi",
            content_type="INPUT",
            applicable_rules=[{"id": rule_id, "title": "t", "content": "c", "effect": "DENY"}],
        ),
        ProviderConfig(provider="vertex", model="gemini-x", location="global", api_key="AIza"),
    )
    assert seen["url"] == (
        "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-x:generateContent"
    )
    assert seen["key"] == "AIza" and seen["auth"] is None
