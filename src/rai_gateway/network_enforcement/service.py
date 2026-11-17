"""Bypass detection ingestion, attestation, and assurance metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from rai_gateway.governance.service import GovernanceService
from rai_gateway.models.governance import AIIncident
from rai_gateway.models.identity import AgentRuntimeIdentity
from rai_gateway.models.network_bypass import ObservedConnectionEvent, RuntimeAttestationState
from rai_gateway.models.registry import Agent
from rai_gateway.network_enforcement.attestation import compute_runtime_attestation_status
from rai_gateway.network_enforcement.authorized_providers import authorized_providers_for_runtime
from rai_gateway.network_enforcement.bypass_rules import evaluate_bypass
from rai_gateway.network_enforcement.dedup import observed_event_dedup_key
from rai_gateway.network_enforcement.destination_classifier import (
    PROVIDER_DESTINATION_CLASSIFIER_VERSION,
    classify_destination,
)
from rai_gateway.network_enforcement.domain import (
    AssuranceMetrics,
    DeclaredObservedAuthorized,
    ObservedConnectionPayload,
    RuntimeAttestationStatus,
)


class BypassDetectionService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def ingest_observed_connections(
        self,
        payloads: list[ObservedConnectionPayload],
        *,
        reported_by: str = "network-observation",
    ) -> dict[str, Any]:
        accepted = 0
        duplicates = 0
        bypass_events = 0
        incidents: list[str] = []

        for payload in payloads:
            provider = classify_destination(payload.destination_hostname)
            if provider is None:
                continue
            dedup_key = observed_event_dedup_key(payload)
            authorized: frozenset[str] | None = None
            if payload.runtime_identity_id is not None:
                authorized = authorized_providers_for_runtime(
                    self._db, payload.runtime_identity_id
                )

            evaluation = evaluate_bypass(payload, provider, authorized_providers=authorized)

            if (
                self._db.query(ObservedConnectionEvent.id)
                .filter(ObservedConnectionEvent.dedup_key == dedup_key)
                .first()
                is not None
            ):
                duplicates += 1
                continue

            row = ObservedConnectionEvent(
                observed_at=payload.observed_at,
                source_workload=payload.source_workload,
                source_process=payload.source_process,
                destination_hostname=payload.destination_hostname,
                destination_ip=payload.destination_ip,
                destination_port=payload.destination_port,
                protocol=payload.protocol,
                cloud_resource_identity=payload.cloud_resource_identity,
                runtime_identity_id=payload.runtime_identity_id,
                gateway_used=payload.gateway_used,
                provider_guess=provider.value if provider else None,
                action=payload.action.value,
                collector_source=payload.collector_source,
                classifier_version=PROVIDER_DESTINATION_CLASSIFIER_VERSION,
                dedup_key=dedup_key,
                bypass_detected=evaluation.is_bypass,
                correlation=payload.correlation,
            )
            self._db.add(row)
            self._db.flush()
            accepted += 1

            if evaluation.is_bypass and payload.runtime_identity_id is not None:
                incident_id = self._maybe_open_bypass_incident(
                    payload,
                    provider.value if provider else "UNKNOWN_AI",
                    evaluation.severity or "MEDIUM",
                    reported_by=reported_by,
                    dedup_key=dedup_key,
                )
                if incident_id is not None:
                    row.incident_id = incident_id
                    incidents.append(str(incident_id))
                    bypass_events += 1

            if payload.runtime_identity_id is not None:
                self._refresh_runtime_attestation(payload.runtime_identity_id)

        self._db.commit()
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "bypass_events": bypass_events,
            "incident_ids": incidents,
            "classifier_version": PROVIDER_DESTINATION_CLASSIFIER_VERSION,
        }

    def _maybe_open_bypass_incident(
        self,
        payload: ObservedConnectionPayload,
        provider: str,
        severity: str,
        *,
        reported_by: str,
        dedup_key: str,
    ) -> UUID | None:
        existing = (
            self._db.query(AIIncident)
            .filter(AIIncident.source == "NETWORK_BYPASS")
            .filter(AIIncident.source_ref == dedup_key)
            .one_or_none()
        )
        if existing is not None:
            return existing.id

        ctx = self._runtime_context(payload.runtime_identity_id)
        if ctx is None:
            return None

        ai_system_id, agent_version_id = ctx
        title = f"Shadow AI egress detected ({provider})"
        description = (
            f"Observed direct connection to {payload.destination_hostname} "
            f"without gateway mediation for governed runtime."
        )
        svc = GovernanceService(self._db)
        result = svc.create_incident(
            ai_system_id,
            title=title,
            reported_by=reported_by,
            severity=severity,
            category="SHADOW_AI",
            description=description,
            source="NETWORK_BYPASS",
            source_ref=dedup_key,
            agent_version_id=agent_version_id,
            runtime_identity_id=payload.runtime_identity_id,
            evidence_refs=[
                {
                    "type": "observed_connection",
                    "destination": payload.destination_hostname,
                    "provider": provider,
                    "gateway_used": payload.gateway_used,
                }
            ],
        )
        return UUID(result["id"])

    def _runtime_context(
        self, runtime_identity_id: UUID | None
    ) -> tuple[UUID, UUID] | None:
        if runtime_identity_id is None:
            return None
        runtime = (
            self._db.query(AgentRuntimeIdentity)
            .filter(AgentRuntimeIdentity.id == runtime_identity_id)
            .one_or_none()
        )
        if runtime is None:
            return None
        agent = (
            self._db.query(Agent).filter(Agent.id == runtime.agent_id).one_or_none()
        )
        if agent is None:
            return None
        return agent.ai_system_id, runtime.agent_version_id

    def _refresh_runtime_attestation(self, runtime_identity_id: UUID) -> None:
        ctx = self._runtime_context(runtime_identity_id)
        authorized = authorized_providers_for_runtime(self._db, runtime_identity_id)

        observed_rows = (
            self._db.query(ObservedConnectionEvent.provider_guess)
            .filter(ObservedConnectionEvent.runtime_identity_id == runtime_identity_id)
            .filter(ObservedConnectionEvent.provider_guess.isnot(None))
            .all()
        )
        observed = frozenset(
            r[0] for r in observed_rows if r[0] is not None
        )
        declared = authorized  # version-approved models are the declared set for v1

        bypass_detected = (
            self._db.query(func.count(ObservedConnectionEvent.id))
            .filter(ObservedConnectionEvent.runtime_identity_id == runtime_identity_id)
            .filter(ObservedConnectionEvent.bypass_detected.is_(True))
            .scalar()
            or 0
        ) > 0

        gateway_only = (
            self._db.query(func.count(ObservedConnectionEvent.id))
            .filter(ObservedConnectionEvent.runtime_identity_id == runtime_identity_id)
            .filter(ObservedConnectionEvent.gateway_used.is_(True))
            .scalar()
            or 0
        ) > 0 and not observed

        exposure = DeclaredObservedAuthorized(
            declared=declared,
            observed=observed,
            authorized=authorized,
        )
        status = compute_runtime_attestation_status(
            exposure,
            bypass_detected=bypass_detected,
            observed_gateway_only=gateway_only,
        )

        row = (
            self._db.query(RuntimeAttestationState)
            .filter(RuntimeAttestationState.runtime_identity_id == runtime_identity_id)
            .one_or_none()
        )
        if row is None:
            row = RuntimeAttestationState(runtime_identity_id=runtime_identity_id)
            self._db.add(row)

        if ctx:
            ai_system_id, agent_version_id = ctx
            row.ai_system_id = ai_system_id
            row.agent_version_id = agent_version_id

        row.status = status.value
        row.declared_providers = sorted(declared)
        row.observed_providers = sorted(observed)
        row.authorized_providers = sorted(authorized)
        if status is RuntimeAttestationStatus.BYPASS_DETECTED:
            row.last_bypass_at = datetime.now(timezone.utc)
        self._db.flush()

    def get_runtime_attestation(self, runtime_identity_id: UUID) -> dict[str, Any] | None:
        row = (
            self._db.query(RuntimeAttestationState)
            .filter(RuntimeAttestationState.runtime_identity_id == runtime_identity_id)
            .one_or_none()
        )
        if row is None:
            return None
        return {
            "runtime_identity_id": str(row.runtime_identity_id),
            "status": row.status,
            "declared_providers": row.declared_providers or [],
            "observed_providers": row.observed_providers or [],
            "authorized_providers": row.authorized_providers or [],
            "agent_version_id": str(row.agent_version_id) if row.agent_version_id else None,
            "ai_system_id": str(row.ai_system_id) if row.ai_system_id else None,
            "last_bypass_at": row.last_bypass_at.isoformat() if row.last_bypass_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def assurance_metrics(self) -> AssuranceMetrics:
        gateway_requests = (
            self._db.query(func.count(ObservedConnectionEvent.id))
            .filter(ObservedConnectionEvent.gateway_used.is_(True))
            .scalar()
            or 0
        )
        direct_ai_egress = (
            self._db.query(func.count(ObservedConnectionEvent.id))
            .filter(ObservedConnectionEvent.bypass_detected.is_(True))
            .scalar()
            or 0
        )
        blocked_bypass = (
            self._db.query(func.count(ObservedConnectionEvent.id))
            .filter(ObservedConnectionEvent.bypass_detected.is_(True))
            .filter(ObservedConnectionEvent.action == "BLOCKED")
            .scalar()
            or 0
        )
        unclassified = (
            self._db.query(func.count(ObservedConnectionEvent.id))
            .filter(ObservedConnectionEvent.provider_guess == "UNKNOWN_AI")
            .scalar()
            or 0
        )
        without_network = (
            self._db.query(func.count(RuntimeAttestationState.id))
            .filter(
                RuntimeAttestationState.status != RuntimeAttestationStatus.NETWORK_ENFORCED.value
            )
            .scalar()
            or 0
        )
        return AssuranceMetrics(
            gateway_requests=gateway_requests,
            direct_ai_egress_attempts=direct_ai_egress,
            blocked_bypass_attempts=blocked_bypass,
            unclassified_ai_destinations=unclassified,
            runtimes_without_network_enforcement=without_network,
        )