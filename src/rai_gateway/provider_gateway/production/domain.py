"""Shared types for gateway production hardening."""

from __future__ import annotations

from enum import StrEnum


class PolicyCacheNamespace(StrEnum):
    RUNTIME_IDENTITY = "runtime_identity"
    AUTHORITY_SNAPSHOT = "authority_snapshot"
    DELEGATION = "delegation"
    MODEL_PERMISSIONS = "model_permissions"
    PROVIDER_PERMISSIONS = "provider_permissions"
    BUDGET_STATE = "budget_state"
    POLICY_BUNDLE = "policy_bundle"
