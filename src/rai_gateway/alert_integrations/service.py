"""CRUD and delivery for third-party alert channel integrations."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.alert_integrations.providers import (
    SUPPORTED_PROVIDERS,
    build_delivery,
    provider_label,
)
from rai_gateway.events.alert_catalog import any_subscription_matches
from rai_gateway.models.productization import AlertChannelIntegration
from rai_gateway.sdk.domain import SdkErrorCode
from rai_gateway.sdk.errors import SdkError
from rai_gateway.sdk.webhooks import validate_webhook_url

logger = logging.getLogger(__name__)

_RESERVED_HEADER_PREFIXES = ("x-rai-", "content-type", "host", "content-length")


def _sanitize_custom_headers(raw: dict[str, Any] | None) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        normalized = key.strip()
        lower = normalized.lower()
        if any(lower.startswith(p) for p in _RESERVED_HEADER_PREFIXES):
            continue
        out[normalized] = str(value)
    return out


class AlertIntegrationService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "id": p,
                "label": provider_label(p),
                "config_fields": self._config_fields(p),
            }
            for p in sorted(SUPPORTED_PROVIDERS)
        ]

    @staticmethod
    def _config_fields(provider: str) -> list[dict[str, str]]:
        common = [
            {"key": "webhook_url", "label": "Incoming webhook URL", "type": "url"},
        ]
        if provider == "telegram":
            return [
                {"key": "bot_token", "label": "Bot token", "type": "secret"},
                {"key": "chat_id", "label": "Chat ID", "type": "text"},
            ]
        if provider == "whatsapp":
            return [
                {"key": "webhook_url", "label": "WhatsApp / BSP webhook URL", "type": "url"},
                {"key": "phone_number", "label": "Destination phone (E.164)", "type": "text"},
            ]
        if provider in {"jira", "trello", "clickup"}:
            return [
                {
                    "key": "webhook_url",
                    "label": f"{provider_label(provider)} automation webhook URL",
                    "type": "url",
                },
            ]
        return common

    def list_integrations(self) -> list[dict[str, Any]]:
        rows = (
            self._db.query(AlertChannelIntegration)
            .order_by(AlertChannelIntegration.created_at.desc())
            .all()
        )
        return [self._serialize(r) for r in rows]

    def create_integration(
        self,
        *,
        name: str,
        provider: str,
        event_types: list[str],
        config: dict[str, Any],
        custom_headers: dict[str, Any] | None,
        created_by: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        provider = provider.lower().strip()
        if provider not in SUPPORTED_PROVIDERS:
            raise SdkError(
                SdkErrorCode.INVALID_REQUEST,
                f"Unsupported provider: {provider}",
            )
        if not event_types:
            raise SdkError(SdkErrorCode.INVALID_REQUEST, "Select at least one event type")
        url = config.get("webhook_url") or config.get("incoming_webhook_url")
        if provider != "telegram" and url:
            validate_webhook_url(str(url))

        row = AlertChannelIntegration(
            name=name.strip(),
            provider=provider,
            event_types=event_types,
            config=config,
            custom_headers=_sanitize_custom_headers(custom_headers),
            enabled=enabled,
            status="ACTIVE",
            created_by=created_by,
        )
        self._db.add(row)
        self._db.flush()
        return self._serialize(row)

    def update_integration(
        self,
        integration_id: UUID,
        *,
        name: str | None = None,
        event_types: list[str] | None = None,
        config: dict[str, Any] | None = None,
        custom_headers: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        row = self._get(integration_id)
        if name is not None:
            row.name = name.strip()
        if event_types is not None:
            if not event_types:
                raise SdkError(SdkErrorCode.INVALID_REQUEST, "Select at least one event type")
            row.event_types = event_types
        if config is not None:
            url = config.get("webhook_url") or config.get("incoming_webhook_url")
            if row.provider != "telegram" and url:
                validate_webhook_url(str(url))
            row.config = config
        if custom_headers is not None:
            row.custom_headers = _sanitize_custom_headers(custom_headers)
        if enabled is not None:
            row.enabled = enabled
        self._db.flush()
        return self._serialize(row)

    def delete_integration(self, integration_id: UUID) -> None:
        row = self._get(integration_id)
        self._db.delete(row)
        self._db.flush()

    def broadcast(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
    ) -> int:
        """Deliver to all matching enabled integrations (best-effort)."""
        rows = (
            self._db.query(AlertChannelIntegration)
            .filter(
                AlertChannelIntegration.enabled.is_(True),
                AlertChannelIntegration.status == "ACTIVE",
            )
            .all()
        )
        sent = 0
        for row in rows:
            types = row.event_types or []
            if not any_subscription_matches(types, event_type):
                continue
            try:
                self._deliver_one(row, event_type=event_type, payload=payload, event_id=event_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001 — do not block other channels
                logger.warning(
                    "alert integration delivery failed id=%s provider=%s: %s",
                    row.id,
                    row.provider,
                    exc,
                )
        return sent

    def _deliver_one(
        self,
        row: AlertChannelIntegration,
        *,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
    ) -> None:
        import httpx

        url, headers, body = build_delivery(
            row.provider,
            event_type=event_type,
            payload=payload,
            config=row.config or {},
        )
        if row.provider != "telegram":
            validate_webhook_url(url)
        headers.update(_sanitize_custom_headers(row.custom_headers))
        headers.setdefault("X-RAI-Event-Type", event_type)
        headers.setdefault("X-RAI-Event-Id", event_id)
        with httpx.Client(timeout=12.0, follow_redirects=False) as client:
            resp = client.post(url, content=body, headers=headers)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    def _get(self, integration_id: UUID) -> AlertChannelIntegration:
        row = (
            self._db.query(AlertChannelIntegration)
            .filter(AlertChannelIntegration.id == integration_id)
            .first()
        )
        if not row:
            raise SdkError(SdkErrorCode.INVALID_REQUEST, "Integration not found")
        return row

    def _serialize(self, row: AlertChannelIntegration) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "name": row.name,
            "provider": row.provider,
            "provider_label": provider_label(row.provider),
            "event_types": row.event_types,
            "config": row.config,
            "custom_headers": row.custom_headers or {},
            "enabled": row.enabled,
            "status": row.status,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }