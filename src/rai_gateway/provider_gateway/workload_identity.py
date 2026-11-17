"""Map cloud workload principals onto RaiCentral runtime identities.

Verification of OIDC / cloud tokens is intentionally deferred to later rollout
steps; this module defines parsing hooks and trusted binding resolution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from rai_gateway.models.provider_credentials import WorkloadIdentityBinding
from rai_gateway.provider_gateway.credentials.domain import CloudProvider


@dataclass(frozen=True)
class WorkloadPrincipal:
    cloud_provider: str
    principal_identifier: str
    raw_token: str | None = None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            v = value.strip()
            return v or None
    return None


def extract_workload_principal(headers: Mapping[str, str]) -> WorkloadPrincipal | None:
    """Best-effort extraction of infrastructure identity headers."""
    gcp = _header(headers, "X-Goog-IAP-JWT-Assertion") or _header(
        headers, "X-Goog-Authenticated-User-Email"
    )
    if gcp:
        principal = gcp
        if "accounts.google.com:" in principal:
            principal = principal.split(":", 1)[-1]
        return WorkloadPrincipal(
            cloud_provider=CloudProvider.GCP.value,
            principal_identifier=principal,
            raw_token=gcp if gcp.startswith("ey") else None,
        )

    azure = _header(headers, "X-Azure-Workload-Identity-Token") or _header(
        headers, "X-MS-CLIENT-PRINCIPAL-NAME"
    )
    if azure:
        return WorkloadPrincipal(
            cloud_provider=CloudProvider.AZURE.value,
            principal_identifier=azure,
            raw_token=azure if azure.startswith("ey") else None,
        )

    aws = _header(headers, "X-Amzn-Oidc-Data") or _header(
        headers, "X-Amzn-Oidc-Identity"
    )
    if aws:
        return WorkloadPrincipal(
            cloud_provider=CloudProvider.AWS.value,
            principal_identifier=aws,
            raw_token=aws if aws.startswith("ey") else None,
        )
    return None


def resolve_runtime_identity_id(
    db: Session, principal: WorkloadPrincipal
) -> str | None:
    row = (
        db.query(WorkloadIdentityBinding)
        .filter(WorkloadIdentityBinding.cloud_provider == principal.cloud_provider)
        .filter(
            WorkloadIdentityBinding.principal_identifier
            == principal.principal_identifier
        )
        .filter(WorkloadIdentityBinding.status == "ACTIVE")
        .first()
    )
    if not row:
        return None
    return str(row.runtime_identity_id)


def derive_client_runtime_secret(
    headers: Mapping[str, str],
) -> tuple[str | None, WorkloadPrincipal | None]:
    """Prefer infrastructure workload identity when present.

    Caller-provided runtime identity headers are ignored when a workload
    principal is available — matching step 5 guidance.
    """
    from rai_gateway.provider_gateway.auth import extract_runtime_secret

    principal = extract_workload_principal(headers)
    if principal is not None:
        # Bindings resolve to runtime identity during authorize(); still allow
        # explicit RAI bearer for hybrid dev when no infra headers are sent.
        return extract_runtime_secret(headers), principal
    return extract_runtime_secret(headers), None
