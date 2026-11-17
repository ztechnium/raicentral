"""Provider-neutral connector contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class VerifyResult:
    ok: bool
    capabilities: dict[str, bool]
    diagnostics: list[dict[str, Any]]
    health: dict[str, Any]


@dataclass(slots=True)
class DiscoveredResource:
    canonical_name: str
    asset_type: str
    display_name: str | None
    location: str | None
    config_summary: dict[str, Any] | None


class CloudProviderConnector(Protocol):
    provider_id: str

    def verify(self, *, scope: str, auth_config: dict[str, Any]) -> VerifyResult: ...

    def discover_assets(
        self,
        *,
        scope: str,
        auth_config: dict[str, Any],
        page_token: str | None = None,
    ) -> tuple[list[DiscoveredResource], str | None, list[str]]: ...

    def inspect_model_armor(
        self, *, scope: str, auth_config: dict[str, Any]
    ) -> dict[str, Any]: ...

    def preview_model_armor_change(
        self, *, scope: str, auth_config: dict[str, Any], desired: dict[str, Any]
    ) -> dict[str, Any]: ...

    def apply_model_armor_change(
        self,
        *,
        scope: str,
        auth_config: dict[str, Any],
        desired: dict[str, Any],
        etag: str | None = None,
    ) -> dict[str, Any]: ...
