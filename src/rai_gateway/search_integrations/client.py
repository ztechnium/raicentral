"""Minimal Elasticsearch HTTP client (no elasticsearch-py dependency)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ElasticsearchClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        verify_tls: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        url = base_url.rstrip("/")
        if not url.startswith("http"):
            url = f"https://{url}"
        self._base = url
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"ApiKey {api_key}"
        self._client = httpx.Client(
            base_url=self._base,
            headers=headers,
            verify=verify_tls,
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def ping(self) -> dict[str, Any]:
        response = self._client.get("/")
        response.raise_for_status()
        return response.json()

    def ensure_index(self, index: str, *, mappings: dict[str, Any] | None = None) -> None:
        head = self._client.head(f"/{index}")
        if head.status_code == 200:
            return
        body: dict[str, Any] = {}
        if mappings:
            body["mappings"] = mappings
        response = self._client.put(f"/{index}", content=json.dumps(body))
        if response.status_code not in {200, 201}:
            response.raise_for_status()

    def index_document(self, index: str, doc_id: str, body: dict[str, Any]) -> None:
        response = self._client.put(
            f"/{index}/_doc/{doc_id}",
            content=json.dumps(body),
            params={"refresh": "false"},
        )
        response.raise_for_status()

    def search(
        self,
        index: str,
        *,
        query: dict[str, Any],
        size: int,
        sort: list[dict[str, Any]] | None = None,
        search_after: list[Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query, "size": size}
        if sort:
            payload["sort"] = sort
        if search_after:
            payload["search_after"] = search_after
        response = self._client.post(
            f"/{index}/_search",
            content=json.dumps(payload),
        )
        response.raise_for_status()
        return response.json()
