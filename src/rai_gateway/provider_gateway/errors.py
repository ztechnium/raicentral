"""Gateway-originated errors with a stable, clearly namespaced body.

Responses never pretend to come from the upstream provider: the body carries an
``error`` object in the common provider shape *and* a top-level ``rai`` object
with machine-readable governance metadata.

HTTP contract
-------------
401  missing / invalid runtime identity
403  policy, authority, risk or budget denial (nothing sent upstream)
409  approval required (``rai.approval_request_id`` identifies the request;
     retry with the approval id once approved)
413  request body too large
404  no provider adapter matches the route
502  upstream unreachable / invalid
503  gateway cannot evaluate or has no upstream credential (fail closed)
504  upstream timeout
"""

from __future__ import annotations

from typing import Any

from rai_gateway.runtime_enforcement.domain import RuntimeEnforcementReasonCode
from rai_gateway.runtime_enforcement.gateway_action import GatewayDecision

_AUTH_REASONS = {
    RuntimeEnforcementReasonCode.RUNTIME_AUTHENTICATION_FAILED.value,
    RuntimeEnforcementReasonCode.RUNTIME_IDENTITY_INELIGIBLE.value,
}
_UNAVAILABLE_REASONS = {
    RuntimeEnforcementReasonCode.GATEWAY_EVALUATION_FAILED.value,
    RuntimeEnforcementReasonCode.POLICY_EVALUATION_FAILED.value,
    RuntimeEnforcementReasonCode.RISK_CONTROL_UNAVAILABLE.value,
    RuntimeEnforcementReasonCode.APPROVAL_CONTROL_UNAVAILABLE.value,
}


class ProviderGatewayError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        rai: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.rai = rai or {}
        self.headers = headers or {}

    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": "rai_gateway_error",
                "code": self.code,
            },
            "rai": {"gateway_generated": True, "code": self.code, **self.rai},
        }


def missing_identity() -> ProviderGatewayError:
    return ProviderGatewayError(
        401,
        "RUNTIME_IDENTITY_REQUIRED",
        "Missing runtime credential (X-RAI-Agent-Key or Bearer token)",
    )


def body_too_large(limit: int) -> ProviderGatewayError:
    return ProviderGatewayError(
        413, "REQUEST_TOO_LARGE", f"Request body exceeds {limit} bytes"
    )


def no_adapter(path: str) -> ProviderGatewayError:
    return ProviderGatewayError(
        404, "NO_PROVIDER_ADAPTER", f"No provider adapter handles '{path}'"
    )


def credential_unavailable(
    provider: str, *, decision: GatewayDecision | None = None
) -> ProviderGatewayError:
    rai: dict[str, Any] = {}
    if decision and decision.receipt_id:
        rai["receipt_id"] = str(decision.receipt_id)
    return ProviderGatewayError(
        503,
        "PROVIDER_CREDENTIAL_UNAVAILABLE",
        f"Gateway has no upstream credential for provider '{provider}'",
        rai=rai,
    )


def credential_scope_denied(
    reason: str, *, decision: GatewayDecision | None = None
) -> ProviderGatewayError:
    rai: dict[str, Any] = {}
    if decision and decision.receipt_id:
        rai["receipt_id"] = str(decision.receipt_id)
    return ProviderGatewayError(
        403,
        "PROVIDER_CREDENTIAL_SCOPE_DENIED",
        reason,
        rai=rai,
    )


def upstream_unreachable(detail: str) -> ProviderGatewayError:
    return ProviderGatewayError(502, "UPSTREAM_UNREACHABLE", detail)


def upstream_timeout() -> ProviderGatewayError:
    return ProviderGatewayError(504, "UPSTREAM_TIMEOUT", "Upstream provider timed out")


def from_decision(decision: GatewayDecision) -> ProviderGatewayError:
    """Map a non-allowed gateway decision onto the HTTP contract."""
    rai: dict[str, Any] = {
        "decision": decision.decision,
        "reason_codes": sorted(decision.reason_codes),
        "receipt_id": str(decision.receipt_id) if decision.receipt_id else None,
        "approval_request_id": str(decision.approval_request_id)
        if decision.approval_request_id
        else None,
        "mode": decision.mode,
    }
    reasons = set(decision.reason_codes)
    message = decision.human_explanation or "Request denied by RaiCentral governance"
    if decision.decision == "REQUIRE_APPROVAL":
        return ProviderGatewayError(409, "APPROVAL_REQUIRED", message, rai=rai)
    if reasons & _AUTH_REASONS:
        return ProviderGatewayError(401, "RUNTIME_IDENTITY_INVALID", message, rai=rai)
    if reasons & _UNAVAILABLE_REASONS:
        return ProviderGatewayError(503, "GOVERNANCE_UNAVAILABLE", message, rai=rai)
    return ProviderGatewayError(403, "POLICY_DENIED", message, rai=rai)
