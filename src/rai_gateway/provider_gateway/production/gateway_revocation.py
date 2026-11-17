"""Fail-closed revocation gate using the shared policy cache (cross-replica)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from rai_gateway.provider_gateway.production.policy_cache import (
    PolicyCacheRevocationError,
    shared_policy_cache,
)

__all__ = ["PolicyCacheRevocationError", "assert_not_revoked_for_authority"]


def _as_str(value: UUID | str | None) -> str | None:
    if value is None:
        return None
    return str(value)


def assert_not_revoked_for_authority(authority: Any) -> None:
    """Raise when a revoked/suspended runtime identity or delegation is cached."""
    identity = getattr(authority, "runtime_identity", None)
    if identity is None:
        return
    delegation_id = getattr(authority, "delegation_id", None)
    if delegation_id is None:
        grant = getattr(authority, "delegation", None)
        if grant is not None:
            delegation_id = getattr(grant, "id", None)
    runtime_id = _as_str(getattr(identity, "id", None))
    if not runtime_id:
        return
    shared_policy_cache.fail_closed_if_revoked(
        runtime_id,
        delegation_id=_as_str(delegation_id),
    )