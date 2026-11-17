"""Shared runtime gateway boundary.

Transport adapters build a :class:`GatewayAction` and call this module; all
governance (identity, authority, policy, risk, budget, approval, receipts) is
delegated to :class:`RuntimeEnforcementService`. This class owns only:

* projecting the canonical action onto the enforcement service,
* applying the OBSERVE / WARN / ENFORCE posture,
* failing closed on gateway-side errors,
* producing the stable :class:`GatewayDecision` contract.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.runtime_enforcement.domain import (
    EnforcementDecisionType,
    EnforcementResult,
    RuntimeEnforcementReasonCode,
)
from rai_gateway.runtime_enforcement.gateway_action import (
    GatewayAction,
    GatewayDecision,
    GatewayEnforcementMode,
)
from rai_gateway.runtime_enforcement.repository import RuntimeEnforcementRepository
from rai_gateway.runtime_enforcement.service import RuntimeEnforcementService

logger = logging.getLogger(__name__)

OBLIGATION_VIOLATION_NOT_BLOCKED = "VIOLATION_NOT_BLOCKED"
OBLIGATION_APPROVAL_NOT_OBTAINED = "APPROVAL_NOT_OBTAINED"
OBLIGATION_NOT_EXECUTED = "NOT_EXECUTED_BY_GATEWAY"
OBLIGATION_MONITOR = "MONITOR"

# Denials that are never downgraded by OBSERVE/WARN: without an authenticated,
# eligible runtime principal (or under emergency stop) there is nothing
# attributable to observe, so the gateway always fails closed.
_NEVER_DOWNGRADED = frozenset(
    {
        RuntimeEnforcementReasonCode.RUNTIME_AUTHENTICATION_FAILED.value,
        RuntimeEnforcementReasonCode.RUNTIME_IDENTITY_INELIGIBLE.value,
        RuntimeEnforcementReasonCode.EMERGENCY_STOPPED.value,
        RuntimeEnforcementReasonCode.DUPLICATE_RUNTIME_ACTION.value,
    }
)


def resolve_gateway_mode(
    mode: GatewayEnforcementMode | str | None = None,
) -> GatewayEnforcementMode:
    """Resolve the posture; unknown values fail closed to ENFORCE."""
    raw = mode if mode is not None else settings.gateway_enforcement_mode
    try:
        return GatewayEnforcementMode(str(getattr(raw, "value", raw)).upper())
    except ValueError:
        logger.warning("Unknown gateway enforcement mode %r; using ENFORCE", raw)
        return GatewayEnforcementMode.ENFORCE


class RuntimeGateway:
    """Single enforcement boundary for provider, MCP, tool and SDK traffic."""

    def __init__(
        self,
        db: Session,
        *,
        mode: GatewayEnforcementMode | str | None = None,
        enforcement: RuntimeEnforcementService | None = None,
    ) -> None:
        self._db = db
        self._mode = resolve_gateway_mode(mode)
        self._enforcement = enforcement or RuntimeEnforcementService(db)

    @property
    def mode(self) -> GatewayEnforcementMode:
        return self._mode

    def authorize_gateway_action(
        self,
        action: GatewayAction,
        *,
        credential_secret: str,
        mode: GatewayEnforcementMode | str | None = None,
    ) -> GatewayDecision:
        """Decide only. The caller (e.g. a provider proxy) performs the call."""
        return self._run(action, credential_secret, mode, execute=False)

    def execute_authorized_gateway_action(
        self,
        action: GatewayAction,
        *,
        credential_secret: str,
        mode: GatewayEnforcementMode | str | None = None,
    ) -> GatewayDecision:
        """Govern, then run the registered downstream executor when allowed.

        Authorization and execution stay in one enforcement pass so approval
        consumption, receipt persistence and TOCTOU revalidation are atomic
        with the downstream call. In OBSERVE/WARN a violating action is
        allowed through but *not* executed by the gateway (no authorizing
        receipt exists); the decision carries ``NOT_EXECUTED_BY_GATEWAY``.
        """
        return self._run(action, credential_secret, mode, execute=True)

    def authorize_for_execution(
        self,
        action: GatewayAction,
        *,
        credential_secret: str,
        mode: GatewayEnforcementMode | str | None = None,
    ) -> GatewayDecision:
        """Govern now, let the transport perform the call, finalize afterwards.

        Used by the provider proxy: the receipt is persisted (STARTED) before
        any upstream traffic and later completed with
        :meth:`finalize_execution` from the actual gateway-observed outcome.
        """
        return self._run(action, credential_secret, mode, execute=True, defer=True)

    def finalize_execution(
        self,
        receipt_id: UUID,
        *,
        succeeded: bool,
        duration_ms: int | None = None,
        downstream_reference: str | None = None,
        safe_result_metadata: dict | None = None,
        execution_status: str | None = None,
    ) -> bool:
        """Finalize a receipt from a real gateway execution (idempotent)."""
        return self._enforcement.finalize_gateway_execution(
            receipt_id,
            succeeded=succeeded,
            duration_ms=duration_ms,
            downstream_reference=downstream_reference,
            safe_result_metadata=safe_result_metadata,
            execution_status=execution_status,
        )

    def _run(
        self,
        action: GatewayAction,
        credential_secret: str,
        mode: GatewayEnforcementMode | str | None,
        *,
        execute: bool,
        defer: bool = False,
    ) -> GatewayDecision:
        effective = resolve_gateway_mode(mode) if mode is not None else self._mode
        request = action.to_runtime_request(mode=effective)
        try:
            if defer:
                result = self._enforcement.authorize_for_gateway_execution(
                    request, credential_secret=credential_secret
                )
            elif execute:
                result = self._enforcement.enforce_and_execute(
                    request, credential_secret=credential_secret
                )
            else:
                result = self._enforcement.authorize(
                    request, credential_secret=credential_secret
                )
        except Exception as exc:  # noqa: BLE001 - boundary must not leak
            logger.exception("Gateway evaluation failed")
            self._db.rollback()
            return self._evaluation_failed(action, effective, exc)
        return self._to_decision(result, action, effective)

    def _evaluation_failed(
        self,
        action: GatewayAction,
        mode: GatewayEnforcementMode,
        exc: Exception,
    ) -> GatewayDecision:
        enforced = mode == GatewayEnforcementMode.ENFORCE
        return GatewayDecision(
            allowed=not enforced,
            decision=EnforcementDecisionType.DENY.value,
            reason_codes=[RuntimeEnforcementReasonCode.GATEWAY_EVALUATION_FAILED.value],
            obligations=[] if enforced else [OBLIGATION_VIOLATION_NOT_BLOCKED],
            mode=mode.value,
            enforced=enforced,
            execution_source=action.resolved_execution_source(),
            human_explanation=f"Gateway evaluation failed: {type(exc).__name__}",
            correlation_id=action.correlation_id,
            trace_id=action.trace_id,
        )

    def _to_decision(
        self,
        result: EnforcementResult,
        action: GatewayAction,
        mode: GatewayEnforcementMode,
    ) -> GatewayDecision:
        reason_codes = [result.reason_code] if result.reason_code else []
        obligations: list[str] = []
        if result.monitor:
            obligations.append(OBLIGATION_MONITOR)

        allowed = bool(result.allowed)
        enforced = True
        violation = result.effect in {
            EnforcementDecisionType.DENY.value,
            EnforcementDecisionType.REQUIRE_APPROVAL.value,
        }
        if violation and not allowed and mode != GatewayEnforcementMode.ENFORCE:
            if result.reason_code not in _NEVER_DOWNGRADED:
                allowed = True
                enforced = False
                obligations.append(OBLIGATION_VIOLATION_NOT_BLOCKED)
                if result.effect == EnforcementDecisionType.REQUIRE_APPROVAL.value:
                    obligations.append(OBLIGATION_APPROVAL_NOT_OBTAINED)
                if not result.execution_attempted:
                    obligations.append(OBLIGATION_NOT_EXECUTED)
                logger.warning(
                    "Gateway %s mode: %s not blocked (%s)",
                    mode.value,
                    result.effect,
                    result.reason_code,
                )

        authority_snapshot_id = None
        policy_version = None
        if result.receipt_id:
            receipt = RuntimeEnforcementRepository(self._db).get_receipt(
                result.receipt_id
            )
            if receipt is not None:
                authority_snapshot_id = receipt.authority_digest
                policy_version = receipt.policy_bundle_digest

        return GatewayDecision(
            allowed=allowed,
            decision=result.effect,
            reason_codes=reason_codes,
            approval_request_id=_as_uuid(result.approval_request_id),
            authority_snapshot_id=authority_snapshot_id,
            receipt_id=result.receipt_id,
            obligations=obligations,
            policy_version=policy_version,
            mode=mode.value,
            enforced=enforced,
            execution_source=result.execution_source
            or action.resolved_execution_source(),
            execution_attempted=result.execution_attempted,
            execution_status=result.execution_status,
            downstream_reference=result.downstream_reference,
            human_explanation=result.human_explanation,
            correlation_id=result.correlation_id,
            trace_id=result.trace_id,
            enforcement_result=result,
        )


def _as_uuid(value: UUID | str | None) -> UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))
