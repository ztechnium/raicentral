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


def build_delivery(
    provider: str,
    *,
    event_type: str,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str, dict[str, str], bytes | None]:
    """Return (url, headers, body_bytes). body_bytes None means use default JSON envelope."""
    webhook_url = (config.get("webhook_url") or config.get("incoming_webhook_url") or "").strip()
    if not webhook_url:
        raise ValueError(f"{provider_label(provider)} webhook URL is required in config")

    summary = _summary_text(event_type, payload)
    detail = json.dumps(payload, separators=(",", ":"), sort_keys=True)

    headers: dict[str, str] = {"Content-Type": "application/json"}

    if provider == "slack":
        body = json.dumps({"text": summary, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{summary}*\n```{detail[:2800]}```"}}]}).encode()
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

    if provider == "whatsapp":
        # Meta / Twilio-style connectors: POST JSON to configured webhook URL.
        body = json.dumps(
            {
                "messaging_product": "whatsapp",
                "to": config.get("phone_number"),
                "type": "text",
                "text": {"body": f"{summary}\n{detail[:3500]}"},
            }
        ).encode()
        return webhook_url, headers, body

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
