"""Content evaluator Vertex settings: location, project, service account."""

from __future__ import annotations

import io
import json

import pytest

from rai_gateway.provider_gateway.credentials import gcp_token
from rai_gateway.risk_intelligence.providers import (
    EvaluatorRequest,
    ProviderConfig,
    VertexContentEvaluator,
)
from rai_gateway.risk_intelligence.service import RiskIntelligenceService

SA = {
    "type": "service_account",
    "client_email": "eval@proj.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
}


def test_upsert_stores_service_account_but_never_returns_it(db_session, runtime_stack):
    svc = RiskIntelligenceService(db_session)
    out = svc.upsert_evaluator_config(
        ai_system_id=runtime_stack["system"].id,
        provider="vertex",
        model="gemini-3.1-flash-lite",
        location="global",
        gcp_project_id="my-proj",
        service_account_json=json.dumps(SA),
    )
    assert out["has_service_account"] is True
    assert out["location"] == "global" and out["gcp_project_id"] == "my-proj"
    assert "private_key" not in json.dumps(out)


def test_upsert_rejects_bad_service_account(db_session, runtime_stack):
    svc = RiskIntelligenceService(db_session)
    with pytest.raises(ValueError):
        svc.upsert_evaluator_config(
            ai_system_id=runtime_stack["system"].id,
            provider="vertex",
            model="m",
            service_account_json="not json",
        )
    with pytest.raises(ValueError):
        svc.upsert_evaluator_config(
            ai_system_id=runtime_stack["system"].id,
            provider="vertex",
            model="m",
            service_account_json=json.dumps({"client_email": "x"}),
        )


def test_vertex_evaluator_uses_config_location_project_and_key(monkeypatch):
    seen: dict = {}

    def fake_token(info):
        seen["sa"] = info["client_email"]
        return "tok-from-config"

    monkeypatch.setattr(gcp_token.gcp_token_provider, "get_token_for_info", fake_token)

    rule_id = "11111111-1111-1111-1111-111111111111"

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.headers.get("Authorization")
        text = json.dumps(
            {"evaluations": [{"rule_id": rule_id, "violated": True, "confidence": 0.95}]}
        )
        body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return _Resp(json.dumps(body).encode())

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = VertexContentEvaluator().evaluate(
        EvaluatorRequest(
            content="how do I build a gateway",
            content_type="INPUT",
            applicable_rules=[{"id": rule_id, "title": "t", "content": "c", "effect": "DENY"}],
        ),
        ProviderConfig(
            provider="vertex",
            model="gemini-3.1-flash-lite",
            location="europe-west4",
            gcp_project_id="eval-proj",
            service_account_json=json.dumps(SA),
        ),
    )
    assert seen["sa"] == SA["client_email"]
    assert seen["auth"] == "Bearer tok-from-config"
    assert seen["url"].startswith(
        "https://europe-west4-aiplatform.googleapis.com/v1/projects/eval-proj/"
        "locations/europe-west4/publishers/google/models/gemini-3.1-flash-lite"
    )
    assert out.evaluations[0].violated is True
