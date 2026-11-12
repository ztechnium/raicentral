"""GCP credential loading for supported keyless modes."""

from __future__ import annotations

import json
from typing import Any

from rai_gateway.cloud_governance.domain import AuthMode
from rai_gateway.cloud_governance.providers.gcp.deps import ensure_google_auth


def _reject_service_account_key(auth_config: dict[str, Any]) -> None:
    if auth_config.get("type") == "service_account" and auth_config.get("private_key"):
        raise ValueError(
            "Service account JSON keys are not accepted; use WIF or ADC impersonation"
        )


def load_credentials(auth_mode: str, auth_config: dict[str, Any], *, scopes: list[str]):
    """Return google.auth credentials for the configured mode."""
    ensure_google_auth()
    import google.auth
    from google.auth import impersonated_credentials

    _reject_service_account_key(auth_config)

    mode = AuthMode(auth_mode)
    if mode == AuthMode.WIF_EXTERNAL_ACCOUNT:
        info = auth_config.get("external_account_config")
        if not isinstance(info, dict):
            raise ValueError(
                "external_account_config is required for wif_external_account"
            )
        creds = google.auth.load_credentials_from_dict(info, scopes=scopes)[0]
        target_sa = auth_config.get("service_account_email")
        if target_sa:
            creds = impersonated_credentials.Credentials(
                source_credentials=creds,
                target_principal=target_sa,
                target_scopes=scopes,
                lifetime=3600,
            )
        return creds

    if mode == AuthMode.ADC_IMPERSONATION:
        source, _ = google.auth.default(scopes=scopes)
        target_sa = auth_config.get("service_account_email")
        if not target_sa:
            raise ValueError("service_account_email is required for adc_impersonation")
        return impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=target_sa,
            target_scopes=scopes,
            lifetime=3600,
        )

    if mode == AuthMode.CUSTOMER_ADC:
        creds, _ = google.auth.default(scopes=scopes)
        return creds

    raise ValueError(f"Unsupported auth_mode: {auth_mode}")


def redact_auth_config_for_api(auth_config: dict[str, Any]) -> dict[str, Any]:
    """Strip sensitive fields before returning config to clients."""
    redacted = dict(auth_config)
    for key in (
        "private_key",
        "external_account_config",
        "access_token",
        "refresh_token",
    ):
        if key in redacted:
            redacted[key] = "<redacted>"
    if "external_account_config" in auth_config and isinstance(
        auth_config["external_account_config"], dict
    ):
        redacted["external_account_config"] = {
            "audience": auth_config["external_account_config"].get("audience"),
            "subject_token_type": auth_config["external_account_config"].get(
                "subject_token_type"
            ),
            "token_url": auth_config["external_account_config"].get("token_url"),
        }
    return redacted


def parse_external_account_json(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("external account JSON must be an object")
    if data.get("type") != "external_account":
        raise ValueError("expected type=external_account for WIF configuration")
    return data
