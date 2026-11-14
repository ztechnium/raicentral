"""Provider-compatible HTTP routes (OpenAI first)."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from rai_gateway.core.database import get_db
from rai_gateway.provider_gateway.adapters.openai import OpenAIAdapter
from rai_gateway.provider_gateway.api import handle_provider_request
from rai_gateway.provider_gateway.proxy import AdapterRegistry, ProviderProxy

router = APIRouter(tags=["provider-gateway"])


@lru_cache
def _openai_proxy() -> ProviderProxy:
    return ProviderProxy(AdapterRegistry([OpenAIAdapter()]))


@router.api_route(
    "/providers/openai/v1/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    operation_id="openai_provider_proxy_path",
)
async def openai_provider_proxy(
    request: Request, db: Session = Depends(get_db)
) -> object:
    return await handle_provider_request(request, proxy=_openai_proxy(), db=db)


@router.api_route(
    "/providers/openai/v1",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    operation_id="openai_provider_proxy_root",
)
async def openai_provider_proxy_root(
    request: Request, db: Session = Depends(get_db)
) -> object:
    return await handle_provider_request(request, proxy=_openai_proxy(), db=db)
