"""Operational endpoints for gateway production hardening (no sensitive data)."""

from __future__ import annotations

from fastapi import APIRouter

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.production.fail_closed import FailClosedMatrix
from rai_gateway.provider_gateway.production.lifecycle import gateway_lifecycle
from rai_gateway.provider_gateway.production.limits import gateway_limits
from rai_gateway.provider_gateway.production.metrics import gateway_metrics
from rai_gateway.provider_gateway.production.policy_cache import shared_policy_cache
from rai_gateway.provider_gateway.production.rollout import rollout_guidance

router = APIRouter(prefix="/v1/providers/_gateway", tags=["provider-gateway-ops"])


@router.get("/status")
async def gateway_production_status() -> dict[str, object]:
    matrix = FailClosedMatrix(mode=settings.gateway_enforcement_mode)
    return {
        "enforcement_mode": settings.gateway_enforcement_mode,
        "rollout": rollout_guidance(),
        "lifecycle": gateway_lifecycle.readiness(),
        "limits": gateway_limits.snapshot(),
        "policy_cache": shared_policy_cache.stats().snapshot(),
        "slo": gateway_metrics.snapshot().to_dict(),
        "fail_closed_matrix": matrix.matrix_snapshot(),
    }


@router.post("/lifecycle/drain")
async def start_gateway_drain() -> dict[str, str]:
    gateway_lifecycle.start_drain()
    return {"status": "draining"}


@router.post("/lifecycle/resume")
async def resume_gateway() -> dict[str, str]:
    gateway_lifecycle.clear_drain()
    return {"status": "active"}
