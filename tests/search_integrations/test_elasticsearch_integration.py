"""Elasticsearch search integration service."""

from __future__ import annotations

import pytest

from rai_gateway.search_integrations.service import (
    PROVIDER_ELASTICSEARCH,
    SearchIntegrationService,
)


def test_upsert_elasticsearch_requires_url(db_session) -> None:
    svc = SearchIntegrationService(db_session)
    with pytest.raises(Exception):
        svc.upsert_elasticsearch(
            name="ES",
            config={},
            enabled=True,
            sync_logs=True,
            use_for_search=True,
            created_by="tester",
        )


def test_upsert_and_list_elasticsearch(db_session) -> None:
    svc = SearchIntegrationService(db_session)
    row = svc.upsert_elasticsearch(
        name="Primary ES",
        config={
            "url": "https://es.example.test:9200",
            "index_prefix": "rai-test",
            "api_key": "secret-key",
        },
        enabled=True,
        sync_logs=True,
        use_for_search=True,
        created_by="tester",
    )
    assert row["provider"] == PROVIDER_ELASTICSEARCH
    assert row["config"]["api_key"] == "••••••••"
    listed = svc.list_integrations()
    assert any(i["id"] == row["id"] for i in listed)
    active = svc.get_for_search()
    assert active is not None
    assert svc.audit_index_name(active) == "rai-test-runtime-audit"
