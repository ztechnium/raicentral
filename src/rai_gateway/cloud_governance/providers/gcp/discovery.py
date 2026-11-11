"""Cloud Asset Inventory discovery for Vertex AI resources."""

from __future__ import annotations

from rai_gateway.cloud_governance.domain import GCP_DISCOVERY_ASSET_TYPES
from rai_gateway.cloud_governance.protocols import DiscoveredResource


def _asset_filter() -> str:
    return " OR ".join(f"assetType={asset_type}" for asset_type in GCP_DISCOVERY_ASSET_TYPES)


def search_vertex_assets(
    *,
    project_id: str,
    credentials,
    page_token: str | None = None,
    page_size: int = 100,
) -> tuple[list[DiscoveredResource], str | None, list[str]]:
    from google.cloud import asset_v1

    client = asset_v1.AssetServiceClient(credentials=credentials)
    scope = f"projects/{project_id}"
    request = asset_v1.SearchAllResourcesRequest(
        scope=scope,
        query=_asset_filter(),
        page_size=page_size,
        page_token=page_token or "",
    )
    errors: list[str] = []
    resources: list[DiscoveredResource] = []
    pager = client.search_all_resources(request=request)
    for asset in pager:
        resources.append(
            DiscoveredResource(
                canonical_name=asset.name,
                asset_type=asset.asset_type or "unknown",
                display_name=asset.display_name or None,
                location=asset.location or None,
                config_summary={
                    "labels": dict(asset.labels or {}),
                    "state": getattr(asset, "state", None),
                },
            )
        )
    next_token = pager.next_page_token or None
    return resources, next_token, errors
