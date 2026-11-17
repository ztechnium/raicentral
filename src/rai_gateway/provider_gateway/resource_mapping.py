"""Registry-oriented resource keys derived from provider-native requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rai_gateway.runtime_enforcement.gateway_action import GatewayAction


@dataclass
class ProviderResourceMapping:
    provider: str | None = None
    provider_account: str | None = None
    model: str | None = None
    deployment: str | None = None
    region: str | None = None
    endpoint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = {
            "provider": self.provider,
            "provider_account": self.provider_account,
            "model": self.model,
            "deployment": self.deployment,
            "region": self.region,
            "endpoint": self.endpoint,
        }
        base = {k: v for k, v in base.items() if v}
        base.update(self.extra)
        return base


def resource_mapping_from_action(action: GatewayAction) -> ProviderResourceMapping:
    meta = action.input_metadata.get("resource_mapping") or {}
    return ProviderResourceMapping(
        provider=action.provider,
        provider_account=meta.get("provider_account"),
        model=action.model,
        deployment=meta.get("deployment"),
        region=meta.get("region"),
        endpoint=meta.get("endpoint"),
        extra={
            k: v
            for k, v in meta.items()
            if k
            not in {
                "provider_account",
                "deployment",
                "region",
                "endpoint",
            }
        },
    )