"""Format outbound alert bodies per channel provider."""

from __future__ import annotations

import json
from typing import Any

SUPPORTED_PROVIDERS = frozenset(
    {
        "slack",
        "microsoft_teams",
        "discord",
        "telegram",
        "whatsapp",
        "jira",
        "trello",
        "clickup",
    }
)

# https://developers.facebook.com/docs/whatsapp/cloud-api
META_WHATSAPP_GRAPH_VERSION = "v21.0"

_PROVIDER_LABELS = {
    "slack": "Slack",
    "microsoft_teams": "Microsoft Teams",
    "discord": "Discord",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "jira": "Jira",
    "trello": "Trello",
    "clickup": "ClickUp",
}


def provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider)


def _normalize_whatsapp_recipient(phone: str) -> str:
    """E.164 without leading + (Meta Cloud API ``to`` field)."""
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    if not cleaned.isdigit():
        raise ValueError("WhatsApp recipient must be an E.164 phone number")
    return cleaned


def _summary_text(event_type: str, payload: dict[str, Any]) -> str:
    decision = payload.get("enforcement_decision") or payload.get("status")
    title = payload.get("title") or payload.get("finding_type")
    parts = [f"[RaiCentral] {event_type}"]
    if decision:
        parts.append(f"decision={decision}")
    if title:
        parts.append(str(title))
    if payload.get("severity"):
        parts.append(f"severity={payload['severity']}")
    if payload.get("reason_code"):
        parts.append(f"reason={payload['reason_code']}")
    return " · ".join(parts)


def build_links(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Console deep links (label, absolute URL) for the ids present in ``payload``."""
    from urllib.parse import urlencode

    from rai_gateway.core.config import settings

    base = (settings.app_public_url or "").strip().rstrip("/")
    if not base:
        return []
    links: list[tuple[str, str]] = []
    if payload.get("finding_id"):
        query = {"finding_id": payload["finding_id"]}
        if payload.get("ai_system_id"):
            query["ai_system_id"] = payload["ai_system_id"]
        links.append(("Finding", f"{base}/risk/findings?{urlencode(query)}"))
    if payload.get("trajectory_id"):
        links.append(("Trajectory", f"{base}/runtime/trajectories/{payload['trajectory_id']}"))
    return links


def build_delivery(
    provider: str,
    *,
    event_type: str,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, dict[str, str], bytes | None]:
    """Return (url, headers, body_bytes). body_bytes None means use default JSON envelope."""
    summary = _summary_text(event_type, payload)
    detail = json.dumps(payload, separators=(",", ":"), sort_keys=True)

    headers: dict[str, str] = {"Content-Type": "application/json"}

    if provider == "whatsapp":
        access_token = (config.get("access_token") or "").strip()
        phone_number_id = (config.get("phone_number_id") or "").strip()
        recipient = (
            config.get("recipient_phone") or config.get("phone_number") or ""
        ).strip()
        if not access_token or not phone_number_id or not recipient:
            raise ValueError(
                "WhatsApp requires access_token, phone_number_id, and recipient_phone in config"
            )
        to = _normalize_whatsapp_recipient(recipient)
        url = (
            f"https://graph.facebook.com/{META_WHATSAPP_GRAPH_VERSION}/"
            f"{phone_number_id}/messages"
        )
        headers["Authorization"] = f"Bearer {access_token}"
        body = json.dumps(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": f"{summary}\n{detail[:3500]}"},
            }
        ).encode()
        return url, headers, body

    webhook_url = (config.get("webhook_url") or config.get("incoming_webhook_url") or "").strip()
    if not webhook_url:
        raise ValueError(f"{provider_label(provider)} webhook URL is required in config")

    if provider == "slack":
        blocks: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{summary}*"}}
        ]
        links = build_links(payload)
        if links:
            link_text = "  |  ".join(f"<{url}|{label}>" for label, url in links)
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": link_text}})
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{detail[:2800]}```"}}
        )
        body = json.dumps({"text": summary, "blocks": blocks}).encode()
        return webhook_url, headers, body

    if provider == "discord":
        body = json.dumps({"content": summary[:1900], "embeds": [{"description": detail[:4000]}]}).encode()
        return webhook_url, headers, body

    if provider == "microsoft_teams":
        body = json.dumps(
            {
                "@type": "MessageCard",
                "@context": "https://schema.org/extensions",
                "summary": summary,
                "themeColor": "0078D4",
                "title": summary,
                "text": detail[:4000],
            }
        ).encode()
        return webhook_url, headers, body

    if provider == "telegram":
        token = (config.get("bot_token") or "").strip()
        chat_id = (config.get("chat_id") or "").strip()
        if not token or not chat_id:
            raise ValueError("Telegram requires bot_token and chat_id in config")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({"chat_id": chat_id, "text": f"{summary}\n{detail[:3500]}"}).encode()
        return url, headers, body

    # Jira automation, Trello, ClickUp incoming webhooks — generic JSON card.
    body = json.dumps(
        {
            "source": "raicentral",
            "event_type": event_type,
            "summary": summary,
            "payload": payload,
        }
    ).encode()
    return webhook_url, headers, body
