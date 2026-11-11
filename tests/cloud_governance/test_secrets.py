"""Unit tests for cloud connection secret encryption."""

from __future__ import annotations

from rai_gateway.cloud_governance.secrets import (
    decrypt_connection_config,
    encrypt_connection_config,
)


def test_roundtrip_connection_config():
    original = {
        "service_account_email": "raicentral-discover@example.iam.gserviceaccount.com",
        "default_location": "us-central1",
    }
    token = encrypt_connection_config(original)
    assert token != original["service_account_email"]
    restored = decrypt_connection_config(token)
    assert restored == original
