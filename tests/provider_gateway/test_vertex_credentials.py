"""Vertex AI upstream credential and URL handling."""

from __future__ import annotations

import pytest

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.adapters.vertex import VertexAIAdapter
from rai_gateway.provider_gateway.auth import (
    ProviderCredentialUnavailable,
    SettingsCredentialResolver,
)
from rai_gateway.provider_gateway.credentials import gcp_token
from rai_gateway.provider_gateway.domain import ProviderCredential, ProxyRequest


def _request(path: str) -> ProxyRequest:
    return ProxyRequest(
        method="POST",
        path=path,
        query="",
        headers={},
        body=b"{}",
    )


def test_vertex_upstream_url_pins_governed_project_and_location(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gcp_project_id", "governed-proj")
    monkeypatch.setattr(settings, "gcp_location", "us-central1")
    adapter = VertexAIAdapter()
    url = adapter.upstream_url(
        _request(
            "/providers/vertex-ai/v1/projects/evil/locations/europe-west1/"
            "publishers/google/models/gemini-3.1-flash-lite:generateContent"
        ),
        ProviderCredential(
            provider="vertex",
            base_url="https://us-central1-aiplatform.googleapis.com/v1",
        ),
    )
    assert url == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/governed-proj/"
        "locations/us-central1/publishers/google/models/"
        "gemini-3.1-flash-lite:generateContent"
    )


def test_vertex_resolver_injects_bearer_and_global_host(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gcp_project_id", "governed-proj")
    monkeypatch.setattr(settings, "gcp_location", "global")
    monkeypatch.setattr(gcp_token.gcp_token_provider, "get_token", lambda: "tok-123")
    cred = SettingsCredentialResolver().resolve("vertex")
    assert cred.base_url == "https://aiplatform.googleapis.com/v1"
    assert cred.headers["Authorization"] == "Bearer tok-123"


def test_vertex_resolver_unavailable_without_google_credentials(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gcp_project_id", "governed-proj")

    def boom() -> str:
        raise gcp_token.GcpTokenError("no creds")

    monkeypatch.setattr(gcp_token.gcp_token_provider, "get_token", boom)
    with pytest.raises(ProviderCredentialUnavailable):
        SettingsCredentialResolver().resolve("vertex")
