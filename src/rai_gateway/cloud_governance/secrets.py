"""Encrypt connection configuration at rest."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from rai_gateway.core.config import settings


def _fernet():
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(settings.evidence_encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_connection_config(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(payload).decode("utf-8")


def decrypt_connection_config(token: str) -> dict[str, Any]:
    raw = _fernet().decrypt(token.encode("utf-8"))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("connection config must be a JSON object")
    return data
