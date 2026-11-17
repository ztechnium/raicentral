"""Explicit fail-closed behavior for gateway dependency failures."""

from __future__ import annotations

from enum import StrEnum

from rai_gateway.runtime_enforcement.gateway_action import GatewayEnforcementMode


class FailClosedDependency(StrEnum):
    POLICY_SERVICE = "policy_service"
    AUTHORITY = "authority"
    PROVIDER_CREDENTIAL = "provider_credential"
    RECEIPT_PERSISTENCE = "receipt_persistence"
    COST_ESTIMATION = "cost_estimation"


class FailClosedOutcome(StrEnum):
    ALLOW_LOG = "allow+log"
    ALLOW_WARN = "allow+warn"
    DENY = "deny"
    FAIL = "fail"


_OBSERVE: dict[FailClosedDependency, FailClosedOutcome] = {
    FailClosedDependency.POLICY_SERVICE: FailClosedOutcome.ALLOW_LOG,
    FailClosedDependency.AUTHORITY: FailClosedOutcome.ALLOW_LOG,
    FailClosedDependency.PROVIDER_CREDENTIAL: FailClosedOutcome.FAIL,
    FailClosedDependency.RECEIPT_PERSISTENCE: FailClosedOutcome.ALLOW_LOG,
    FailClosedDependency.COST_ESTIMATION: FailClosedOutcome.ALLOW_LOG,
}

_WARN: dict[FailClosedDependency, FailClosedOutcome] = {
    FailClosedDependency.POLICY_SERVICE: FailClosedOutcome.ALLOW_WARN,
    FailClosedDependency.AUTHORITY: FailClosedOutcome.ALLOW_WARN,
    FailClosedDependency.PROVIDER_CREDENTIAL: FailClosedOutcome.FAIL,
    FailClosedDependency.RECEIPT_PERSISTENCE: FailClosedOutcome.ALLOW_WARN,
    FailClosedDependency.COST_ESTIMATION: FailClosedOutcome.ALLOW_WARN,
}

_ENFORCE: dict[FailClosedDependency, FailClosedOutcome] = {
    FailClosedDependency.POLICY_SERVICE: FailClosedOutcome.DENY,
    FailClosedDependency.AUTHORITY: FailClosedOutcome.DENY,
    FailClosedDependency.PROVIDER_CREDENTIAL: FailClosedOutcome.FAIL,
    FailClosedDependency.RECEIPT_PERSISTENCE: FailClosedOutcome.DENY,
    FailClosedDependency.COST_ESTIMATION: FailClosedOutcome.ALLOW_WARN,
}


class FailClosedMatrix:
    """Resolve dependency failure handling by gateway enforcement mode."""

    def __init__(
        self,
        *,
        mode: GatewayEnforcementMode | str | None = None,
        receipt_high_risk_deny: bool = True,
    ) -> None:
        raw = str(getattr(mode, "value", mode) or GatewayEnforcementMode.ENFORCE.value)
        try:
            self._mode = GatewayEnforcementMode(raw.upper())
        except ValueError:
            self._mode = GatewayEnforcementMode.ENFORCE
        self._receipt_high_risk_deny = receipt_high_risk_deny

    @property
    def mode(self) -> GatewayEnforcementMode:
        return self._mode

    def resolve(
        self,
        dependency: FailClosedDependency | str,
        *,
        high_risk: bool = False,
    ) -> FailClosedOutcome:
        dep = FailClosedDependency(str(dependency))
        table = self._table()
        outcome = table[dep]
        if (
            dep == FailClosedDependency.RECEIPT_PERSISTENCE
            and high_risk
            and self._receipt_high_risk_deny
            and self._mode == GatewayEnforcementMode.ENFORCE
        ):
            return FailClosedOutcome.DENY
        return outcome

    def should_deny(self, dependency: FailClosedDependency | str, **kw: object) -> bool:
        outcome = self.resolve(dependency, **kw)
        return outcome in {FailClosedOutcome.DENY, FailClosedOutcome.FAIL}

    def matrix_snapshot(self) -> dict[str, dict[str, str]]:
        return {
            m.value: {d.value: self._table_for(m)[d].value for d in FailClosedDependency}
            for m in GatewayEnforcementMode
        }

    def _table(self) -> dict[FailClosedDependency, FailClosedOutcome]:
        return self._table_for(self._mode)

    @staticmethod
    def _table_for(
        mode: GatewayEnforcementMode,
    ) -> dict[FailClosedDependency, FailClosedOutcome]:
        if mode == GatewayEnforcementMode.OBSERVE:
            return dict(_OBSERVE)
        if mode == GatewayEnforcementMode.WARN:
            return dict(_WARN)
        return dict(_ENFORCE)
