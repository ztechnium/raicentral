"""Elasticsearch-backed queries for Audit Explorer and trajectories."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.core.cursor import encode_time_id_cursor
from rai_gateway.evidence.audit_search import AuditSearchFilters
from rai_gateway.models.productization import SearchBackendIntegration
from rai_gateway.search_integrations.service import SearchIntegrationService

logger = logging.getLogger(__name__)


def _decode_es_cursor(cursor: str | None) -> list[Any] | None:
    if not cursor:
        return None
    if cursor.startswith("es:"):
        try:
            raw = base64.urlsafe_b64decode(cursor[3:].encode())
            return json.loads(raw.decode())
        except Exception:
            return None
    return None


def _encode_es_cursor(search_after: list[Any]) -> str:
    payload = json.dumps(search_after).encode()
    return "es:" + base64.urlsafe_b64encode(payload).decode()


def _bool_filter(filters: AuditSearchFilters) -> list[dict[str, Any]]:
    must: list[dict[str, Any]] = [
        {
            "range": {
                "occurred_at": {
                    "gte": filters.date_from.isoformat(),
                    "lte": filters.date_to.isoformat(),
                }
            }
        }
    ]
    if filters.entity_types:
        must.append({"terms": {"record_type": list(filters.entity_types)}})
    mapping = {
        "ai_system_id": filters.ai_system_id,
        "agent_id": filters.agent_id,
        "runtime_identity_id": filters.runtime_identity_id,
        "trajectory_id": filters.trajectory_id,
    }
    for field, value in mapping.items():
        if value is not None:
            must.append({"term": {field: str(value)}})
    if filters.decision:
        must.append({"term": {"decision": filters.decision}})
    if filters.execution_status:
        must.append({"term": {"execution_status": filters.execution_status}})
    if filters.correlation_id:
        must.append({"term": {"correlation_id": filters.correlation_id}})
    if filters.conversation_id:
        must.append({"term": {"conversation_id": filters.conversation_id}})
    if filters.principal:
        must.append({"term": {"principal": filters.principal}})
    if filters.channel:
        must.append({"term": {"channel": filters.channel}})
    if filters.content_search:
        must.append(
            {
                "match": {
                    "content_text": {
                        "query": filters.content_search,
                        "operator": "and",
                    }
                }
            }
        )
    return must


class ElasticsearchAuditSearch:
    def __init__(self, db: Session, integration: SearchBackendIntegration) -> None:
        self._db = db
        self._integration = integration
        self._svc = SearchIntegrationService(db)

    def search(
        self,
        *,
        filters: AuditSearchFilters,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any] | None:
        client = self._svc.build_client(self._integration)
        index = self._svc.audit_index_name(self._integration)
        page_size = max(1, min(limit, 100))
        search_after = _decode_es_cursor(cursor)
        sort = [
            {"occurred_at": {"order": "desc"}},
            {"_id": {"order": "desc"}},
        ]
        query = {"bool": {"filter": _bool_filter(filters)}}
        try:
            result = client.search(
                index,
                query=query,
                size=page_size + 1,
                sort=sort,
                search_after=search_after,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("elasticsearch_audit_search_failed", error=str(exc))
            return None
        finally:
            client.close()

        hits = result.get("hits", {}).get("hits", [])
        has_more = len(hits) > page_size
        hits = hits[:page_size]
        items: list[dict[str, Any]] = []
        for hit in hits:
            src = hit.get("_source") or {}
            doc_id = hit.get("_id", "")
            record_type = src.get("record_type") or "EVIDENCE_EVENT"
            occurred = src.get("occurred_at")
            items.append(
                {
                    "id": doc_id if record_type != "EXECUTION" else doc_id,
                    "record_type": record_type,
                    "occurred_at": occurred,
                    "conversation_id": src.get("conversation_id"),
                    "trajectory_id": src.get("trajectory_id"),
                    "action": src.get("action"),
                    "decision": src.get("decision"),
                    "execution_status": src.get("execution_status"),
                    "status": src.get("decision") if record_type == "ENFORCEMENT" else src.get("action"),
                    "principal": src.get("principal"),
                    "agent_id": src.get("agent_id"),
                    "ai_system_id": src.get("ai_system_id"),
                    "runtime_identity_id": src.get("runtime_identity_id"),
                    "correlation_id": src.get("correlation_id"),
                    "assurance": src.get("assurance"),
                    "risk": None,
                    "detail_reference": _detail_reference(record_type, doc_id, src),
                }
            )
        next_cursor = None
        if has_more and hits:
            last = hits[-1]
            sort_vals = last.get("sort")
            if isinstance(sort_vals, list):
                next_cursor = _encode_es_cursor(sort_vals)
        elif not cursor and items:
            # Preserve legacy cursor when falling back mixed — ES uses es: prefix
            last_item = items[-1]
            if last_item.get("occurred_at"):
                try:
                    ts = datetime.fromisoformat(
                        str(last_item["occurred_at"]).replace("Z", "+00:00")
                    )
                    rid = str(last_item["id"]).replace("exec:", "")
                    next_cursor = encode_time_id_cursor(
                        occurred_at=ts,
                        record_id=rid,
                    )
                except Exception:
                    pass
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "limit": page_size,
            "search_backend": "elasticsearch",
            "filters": {
                "date_from": filters.date_from.isoformat(),
                "date_to": filters.date_to.isoformat(),
                "entity_types": list(filters.entity_types),
            },
        }


def _detail_reference(record_type: str, doc_id: str, src: dict[str, Any]) -> str:
    if record_type == "EVIDENCE_EVENT" and src.get("trajectory_id"):
        return f"/runtime/trajectories/{src['trajectory_id']}"
    rid = doc_id.replace("exec:", "")
    return f"/runtime/enforcement/{rid}"


def search_trajectory_ids(
    db: Session,
    *,
    query_text: str,
    limit: int = 200,
) -> list[UUID] | None:
    integration = SearchIntegrationService(db).get_for_search()
    if not integration:
        return None
    text = query_text.strip()
    if not text:
        return None
    svc = SearchIntegrationService(db)
    client = svc.build_client(integration)
    index = svc.trajectory_index_name(integration)
    es_query = {
        "bool": {
            "should": [
                {"multi_match": {"query": text, "fields": [
                    "conversation_id^3",
                    "client_session_id^3",
                    "correlation_id^2",
                    "ai_system_name",
                    "agent_name",
                    "runtime_identity_name",
                    "model",
                ]}},
            ],
            "minimum_should_match": 1,
        }
    }
    try:
        result = client.search(index, query=es_query, size=min(limit, 500))
    except Exception as exc:  # noqa: BLE001
        logger.warning("elasticsearch_trajectory_search_failed", error=str(exc))
        return None
    finally:
        client.close()
    ids: list[UUID] = []
    for hit in result.get("hits", {}).get("hits", []):
        try:
            ids.append(UUID(str(hit.get("_id"))))
        except ValueError:
            continue
    return ids
