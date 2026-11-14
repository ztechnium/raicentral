"""Liveness, readiness, and version endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from rai_gateway.core.config import settings
from rai_gateway.core.database import engine
from rai_gateway.core.version_info import (
    SERVICE_NAME,
    get_build_time,
    get_commit_sha,
    get_schema_revision,
    get_service_version,
)
from rai_gateway.provider_gateway.production.lifecycle import gateway_lifecycle
from rai_gateway.provider_gateway.production.policy_cache import shared_policy_cache

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Process liveness probe — no dependency checks."""
    return {"status": "healthy", "service": SERVICE_NAME}


@router.get("/ready")
async def readiness() -> dict[str, Any]:
    """Readiness probe — checks critical dependencies without exposing secrets."""
    checks: dict[str, str] = {}
    all_ready = True

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgresql"] = "ok"
    except SQLAlchemyError:
        checks["postgresql"] = "unavailable"
        all_ready = False

    cache = shared_policy_cache.stats()
    checks["policy_cache"] = "ok" if cache.healthy() else "degraded"
    if not cache.healthy():
        all_ready = False

    lifecycle = gateway_lifecycle.readiness()
    if lifecycle.get("draining") and not lifecycle.get("accept_new_requests"):
        checks["gateway_drain"] = "draining"
        all_ready = False
    else:
        checks["gateway_drain"] = (
            "draining" if lifecycle.get("draining") else "accepting"
        )

    if settings.enable_cache:
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5
            )
            client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
            if shared_policy_cache.stats().backend == "redis":
                all_ready = False

    return {
        "status": "ready" if all_ready else "not_ready",
        "service": SERVICE_NAME,
        "checks": checks,
        "gateway_in_flight": gateway_lifecycle.in_flight(),
    }


@router.get("/version")
async def version_info() -> dict[str, str]:
    """Non-sensitive build and schema metadata."""
    return {
        "service": SERVICE_NAME,
        "version": get_service_version(),
        "commit": get_commit_sha(),
        "build_time": get_build_time(),
        "schema_revision": get_schema_revision(),
    }
