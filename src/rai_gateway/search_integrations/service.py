"""CRUD and connection tests for Elasticsearch search integrations."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.models.productization import SearchBackendIntegration
from rai_gateway.sdk.domain import SdkErrorCode
from rai_gateway.sdk.errors import SdkError
from rai_gateway.search_integrations.client import ElasticsearchClient

logger = logging.getLogger(__name__)

PROVIDER_ELASTICSEARCH = "elasticsearch"

_AUDIT_INDEX_MAPPINGS = {
    "properties": {
        "record_type": {"type": "keyword"},
        "occurred_at": {"type": "date"},
        "conversation_id": {"type": "keyword"},
        "trajectory_id": {"type": "keyword"},
        "ai_system_id": {"type": "keyword"},
        "agent_id": {"type": "keyword"},
        "runtime_identity_id": {"type": "keyword"},
        "decision": {"type": "keyword"},
        "execution_status": {"type": "keyword"},
        "correlation_id": {"type": "keyword"},
        "action": {"type": "text"},
        "content_text": {"type": "text"},
        "principal": {"type": "keyword"},
        "channel": {"type": "keyword"},
    }
}

_TRAJECTORY_INDEX_MAPPINGS = {
    "properties": {
        "started_at": {"type": "date"},
        "status": {"type": "keyword"},
        "conversation_id": {"type": "keyword"},
        "client_session_id": {"type": "keyword"},
        "ai_system_name": {"type": "text"},
        "agent_name": {"type": "text"},
        "runtime_identity_name": {"type": "text"},
        "model": {"type": "keyword"},
        "correlation_id": {"type": "keyword"},
    }
}


def _mask_config(config: dict[str, Any]) -> dict[str, Any]:
    out = dict(config or {})
    if out.get("api_key"):
        out["api_key"] = "••••••••"
    return out


class SearchIntegrationService:
    def __init__(self, db: Session) -> None:
        self._db = db

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        return [
            {
                "id": PROVIDER_ELASTICSEARCH,
                "label": "Elasticsearch",
                "description": (
                    "Index runtime audit records and trajectories for fast search."
                ),
                "config_fields": [
                    {
                        "key": "url",
                        "label": "Cluster URL",
                        "type": "url",
                        "placeholder": "https://es.example.com:9200",
                    },
                    {
                        "key": "api_key",
                        "label": "API key (optional)",
                        "type": "secret",
                    },
                    {
                        "key": "index_prefix",
                        "label": "Index name prefix",
                        "type": "text",
                        "placeholder": "rai",
                    },
                    {
                        "key": "verify_tls",
                        "label": "Verify TLS certificates (true/false)",
                        "type": "text",
                    },
                ],
            }
        ]

    def get_active(self) -> SearchBackendIntegration | None:
        return (
            self._db.query(SearchBackendIntegration)
            .filter(
                SearchBackendIntegration.provider == PROVIDER_ELASTICSEARCH,
                SearchBackendIntegration.enabled.is_(True),
                SearchBackendIntegration.status == "ACTIVE",
            )
            .order_by(SearchBackendIntegration.updated_at.desc())
            .first()
        )

    def get_for_search(self) -> SearchBackendIntegration | None:
        row = self.get_active()
        if row and row.use_for_search:
            return row
        return None

    def get_for_indexing(self) -> SearchBackendIntegration | None:
        row = self.get_active()
        if row and row.sync_logs:
            return row
        return None

    def list_integrations(self) -> list[dict[str, Any]]:
        rows = (
            self._db.query(SearchBackendIntegration)
            .order_by(SearchBackendIntegration.created_at.desc())
            .all()
        )
        return [self._serialize(r) for r in rows]

    def upsert_elasticsearch(
        self,
        *,
        name: str,
        config: dict[str, Any],
        enabled: bool,
        sync_logs: bool,
        use_for_search: bool,
        created_by: str,
    ) -> dict[str, Any]:
        url = (config.get("url") or "").strip()
        if not url:
            raise SdkError(SdkErrorCode.INVALID_REQUEST, "Elasticsearch URL is required")
        prefix = (config.get("index_prefix") or "rai").strip() or "rai"
        verify_raw = str(config.get("verify_tls", "true")).lower()
        verify_tls = verify_raw not in {"0", "false", "no"}
        stored_config = {
            "url": url,
            "api_key": (config.get("api_key") or "").strip(),
            "index_prefix": prefix,
            "verify_tls": verify_tls,
        }
        row = (
            self._db.query(SearchBackendIntegration)
            .filter(SearchBackendIntegration.provider == PROVIDER_ELASTICSEARCH)
            .first()
        )
        if row is None:
            row = SearchBackendIntegration(
                name=name.strip() or "Elasticsearch",
                provider=PROVIDER_ELASTICSEARCH,
                config=stored_config,
                enabled=enabled,
                sync_logs=sync_logs,
                use_for_search=use_for_search,
                created_by=created_by,
            )
            self._db.add(row)
        else:
            if not stored_config["api_key"] and row.config:
                stored_config["api_key"] = (row.config or {}).get("api_key") or ""
            row.name = name.strip() or row.name
            row.config = stored_config
            row.enabled = enabled
            row.sync_logs = sync_logs
            row.use_for_search = use_for_search
        self._db.flush()
        return self._serialize(row)

    def delete_integration(self, integration_id: UUID) -> None:
        row = self._db.get(SearchBackendIntegration, integration_id)
        if not row:
            raise SdkError(SdkErrorCode.NOT_FOUND, "Integration not found")
        self._db.delete(row)

    def test_connection(self, integration_id: UUID | None = None) -> dict[str, Any]:
        row: SearchBackendIntegration | None
        if integration_id:
            row = self._db.get(SearchBackendIntegration, integration_id)
        else:
            row = (
                self._db.query(SearchBackendIntegration)
                .filter(SearchBackendIntegration.provider == PROVIDER_ELASTICSEARCH)
                .first()
            )
        if not row:
            raise SdkError(SdkErrorCode.NOT_FOUND, "Elasticsearch integration not configured")
        client = self.build_client(row)
        try:
            info = client.ping()
            audit_index = self.audit_index_name(row)
            trajectory_index = self.trajectory_index_name(row)
            client.ensure_index(audit_index, mappings=_AUDIT_INDEX_MAPPINGS)
            client.ensure_index(trajectory_index, mappings=_TRAJECTORY_INDEX_MAPPINGS)
            return {
                "ok": True,
                "cluster_name": info.get("cluster_name"),
                "audit_index": audit_index,
                "trajectory_index": trajectory_index,
            }
        finally:
            client.close()

    def build_client(self, row: SearchBackendIntegration) -> ElasticsearchClient:
        config = row.config if isinstance(row.config, dict) else {}
        return ElasticsearchClient(
            base_url=str(config.get("url") or ""),
            api_key=(config.get("api_key") or None),
            verify_tls=bool(config.get("verify_tls", True)),
        )

    @staticmethod
    def audit_index_name(row: SearchBackendIntegration) -> str:
        prefix = (row.config or {}).get("index_prefix") or "rai"
        return f"{prefix}-runtime-audit"

    @staticmethod
    def trajectory_index_name(row: SearchBackendIntegration) -> str:
        prefix = (row.config or {}).get("index_prefix") or "rai"
        return f"{prefix}-trajectories"

    def _serialize(self, row: SearchBackendIntegration) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "name": row.name,
            "provider": row.provider,
            "provider_label": "Elasticsearch",
            "enabled": bool(row.enabled),
            "sync_logs": bool(row.sync_logs),
            "use_for_search": bool(row.use_for_search),
            "status": row.status,
            "config": _mask_config(row.config if isinstance(row.config, dict) else {}),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
