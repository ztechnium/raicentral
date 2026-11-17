"""FastAPI routes for provider-compatible gateway mounts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.core.database import get_db
from rai_gateway.provider_gateway.adapters.registry import default_adapter_registry
from rai_gateway.provider_gateway.api import handle_provider_request
from rai_gateway.provider_gateway.auth import SettingsCredentialResolver
from rai_gateway.provider_gateway.credentials.isolated_resolver import (
    IsolatedProviderCredentialResolver,
)
from rai_gateway.provider_gateway.production.api import router as production_router
from rai_gateway.provider_gateway.proxy import ProviderProxy

router = APIRouter(tags=["provider-gateway"])
router.include_router(production_router)

_proxy: ProviderProxy | None = None


def get_provider_proxy(db: Session | None = None) -> ProviderProxy:
    global _proxy
    if _proxy is None:
        credentials = SettingsCredentialResolver()
        _proxy = ProviderProxy(
            default_adapter_registry(),
            mode=settings.gateway_enforcement_mode,
            credentials=credentials,
        )
    if (
        settings.provider_gateway_isolated_credentials
        and db is not None
        and not isinstance(_proxy._credentials, IsolatedProviderCredentialResolver)
    ):
        _proxy._credentials = IsolatedProviderCredentialResolver(
            db, fallback=SettingsCredentialResolver()
        )
    return _proxy


@router.api_route(
    "/providers/{provider_name}/{api_version}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def provider_gateway_route(
    request: Request,
    provider_name: str,
    api_version: str,
    path: str,
    db: Session = Depends(get_db),
) -> Response:
    del provider_name, api_version, path
    return await handle_provider_request(
        request, proxy=get_provider_proxy(db), db=db
    )