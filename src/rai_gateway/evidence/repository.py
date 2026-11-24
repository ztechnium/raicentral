"""Persistence helpers for WP5 runtime trajectory evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from rai_gateway.models.governance import OrganizationMembership
from rai_gateway.models.identity import DelegationGrant
from rai_gateway.models.metering import RuntimeMeteringSpan
from rai_gateway.models.evidence import (
    EvidenceArtifact,
    EvidenceEvent,
    EvidenceFinding,
    EvidenceRelation,
    RuntimeTrajectory,
)

_NUMERIC_OPS = {
    "eq": lambda c, v: c == v,
    "ne": lambda c, v: c != v,
    "gt": lambda c, v: c > v,
    "gte": lambda c, v: c >= v,
    "lt": lambda c, v: c < v,
    "lte": lambda c, v: c <= v,
}


def parse_numeric_filter(raw: str | None) -> tuple[str, float] | None:
    """Parse "gt:100" into ("gt", 100.0). Raises ValueError when malformed."""
    if not raw:
        return None
    op, sep, value = raw.partition(":")
    if not sep or op not in _NUMERIC_OPS:
        raise ValueError(
            f"Invalid numeric filter '{raw}'; expected <eq|ne|gt|gte|lt|lte>:<number>"
        )
    return op, float(value)


def _apply_trajectory_filters(
    query,
    db: Session,
    *,
    ai_system_id=None,
    agent_id=None,
    agent_version_id=None,
    runtime_identity_id=None,
    client_integration_id=None,
    status=None,
    started_after=None,
    started_before=None,
    model: str | None = None,
    delegator_id: str | None = None,
    department_id: UUID | None = None,
    events: tuple[str, float] | None = None,
    findings: tuple[str, float] | None = None,
    completeness: tuple[str, float] | None = None,
    tokens: tuple[str, float] | None = None,
    cost: tuple[str, float] | None = None,
    trajectory_ids: tuple[UUID, ...] | None = None,
):
    T = RuntimeTrajectory
    if trajectory_ids:
        query = query.filter(T.id.in_(list(trajectory_ids)))
    if ai_system_id:
        query = query.filter(T.ai_system_id == ai_system_id)
    if agent_id:
        query = query.filter(T.agent_id == agent_id)
    if agent_version_id:
        query = query.filter(T.agent_version_id == agent_version_id)
    if runtime_identity_id:
        query = query.filter(T.runtime_identity_id == runtime_identity_id)
    if client_integration_id:
        query = query.filter(T.client_integration_id == client_integration_id)
    if status:
        query = query.filter(T.status == status)
    if started_after:
        query = query.filter(T.started_at >= started_after)
    if started_before:
        query = query.filter(T.started_at <= started_before)

    if model:
        like = f"%{model.strip()}%"
        query = query.filter(
            db.query(RuntimeMeteringSpan.id)
            .filter(
                RuntimeMeteringSpan.trajectory_id == T.id,
                RuntimeMeteringSpan.model.ilike(like),
            )
            .exists()
        )

    if delegator_id or department_id:
        grants = db.query(DelegationGrant.id).filter(
            DelegationGrant.id == T.delegation_id
        )
        if delegator_id:
            grants = grants.filter(DelegationGrant.delegator_id == delegator_id)
        if department_id:
            grants = grants.filter(
                DelegationGrant.delegator_id.in_(
                    db.query(OrganizationMembership.user_id).filter(
                        OrganizationMembership.department_id == department_id
                    )
                )
            )
        query = query.filter(grants.exists())

    for flt, column in (
        (events, T.event_count),
        (findings, T.finding_count),
        (completeness, T.completeness_pct),
    ):
        if flt:
            query = query.filter(_NUMERIC_OPS[flt[0]](column, flt[1]))

    # Tokens / cost live on metering spans: compare per-trajectory sums.
    cost_expr = func.coalesce(
        RuntimeMeteringSpan.billed_cost,
        RuntimeMeteringSpan.actual_cost,
        RuntimeMeteringSpan.metered_cost,
        RuntimeMeteringSpan.estimated_cost,
        0,
    )
    for flt, value_col in (
        (tokens, func.coalesce(RuntimeMeteringSpan.total_tokens, 0)),
        (cost, cost_expr),
    ):
        if not flt:
            continue
        total = func.sum(value_col)
        matching = (
            db.query(RuntimeMeteringSpan.trajectory_id)
            .filter(RuntimeMeteringSpan.trajectory_id.isnot(None))
            .group_by(RuntimeMeteringSpan.trajectory_id)
            .having(_NUMERIC_OPS[flt[0]](total, flt[1]))
        )
        cond = T.id.in_(matching)
        if _NUMERIC_OPS[flt[0]](0, flt[1]):
            # Trajectories with no spans count as zero tokens / cost.
            with_spans = db.query(RuntimeMeteringSpan.trajectory_id).filter(
                RuntimeMeteringSpan.trajectory_id.isnot(None)
            )
            cond = cond | ~T.id.in_(with_spans)
        query = query.filter(cond)
    return query


class EvidenceRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _owned_trajectory_query(
        self,
    ):
        query = self._db.query(RuntimeTrajectory)
        return query

    # --- Trajectories -----------------------------------------------------

    def get_trajectory(
        self,
        trajectory_id: UUID,
    ) -> RuntimeTrajectory | None:
        return (
            self._owned_trajectory_query()
            .filter(RuntimeTrajectory.id == trajectory_id)
            .first()
        )

    def find_trajectory_by_session(
        self,
        *,
        runtime_identity_id: UUID,
        session_key: str,
    ) -> RuntimeTrajectory | None:
        return (
            self._db.query(RuntimeTrajectory)
            .filter(
                RuntimeTrajectory.runtime_identity_id == runtime_identity_id,
                RuntimeTrajectory.session_key == session_key,
            )
            .first()
        )

    def find_trajectory_by_correlation(
        self,
        *,
        runtime_identity_id: UUID,
        correlation_id: str,
    ) -> RuntimeTrajectory | None:
        return (
            self._db.query(RuntimeTrajectory)
            .filter(
                RuntimeTrajectory.runtime_identity_id == runtime_identity_id,
                RuntimeTrajectory.correlation_id == correlation_id,
            )
            .order_by(RuntimeTrajectory.last_event_at.desc())
            .first()
        )

    def find_active_trajectory_for_session_fields(
        self,
        *,
        runtime_identity_id: UUID,
        conversation_id: str | None = None,
        client_session_id: str | None = None,
    ) -> RuntimeTrajectory | None:
        """Find an ACTIVE trajectory by turn/session id — never by conversation alone.

        ``conversation_id`` is retained for API compatibility but is not used as a
        trajectory lookup key (it only groups many turns under one conversation).
        """
        del conversation_id  # grouping only; do not merge turns by conversation
        if not client_session_id:
            return None
        return (
            self._db.query(RuntimeTrajectory)
            .filter(
                RuntimeTrajectory.runtime_identity_id == runtime_identity_id,
                RuntimeTrajectory.status == "ACTIVE",
                RuntimeTrajectory.client_session_id == client_session_id,
            )
            .order_by(RuntimeTrajectory.last_event_at.desc())
            .first()
        )

    def create_trajectory(self, **fields: Any) -> RuntimeTrajectory:
        trajectory = RuntimeTrajectory(**fields)
        self._db.add(trajectory)
        self._db.flush()
        return trajectory

    def update_trajectory(self, trajectory: RuntimeTrajectory, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(trajectory, key, value)
        self._db.flush()

    def list_trajectories(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        **filters: Any,
    ) -> list[RuntimeTrajectory]:
        query = _apply_trajectory_filters(
            self._owned_trajectory_query(), self._db, **filters
        )
        return (
            query.order_by(RuntimeTrajectory.started_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count_trajectories(self, **filters: Any) -> int:
        return _apply_trajectory_filters(
            self._owned_trajectory_query(), self._db, **filters
        ).count()

    def trajectory_metrics(self) -> dict[str, int]:
        """Totals across all trajectories (independent of paging/filters)."""
        base = self._owned_trajectory_query()
        return {
            "total": base.count(),
            "active": base.filter(RuntimeTrajectory.status == "ACTIVE").count(),
            "incomplete": base.filter(RuntimeTrajectory.status == "INCOMPLETE").count(),
            "with_findings": base.filter(RuntimeTrajectory.finding_count > 0).count(),
        }

    # --- Events -----------------------------------------------------------

    def get_event_by_idempotency(
        self,
        *,
        trajectory_id: UUID,
        source_type: str,
        source_reference: str,
        event_type: str,
    ) -> EvidenceEvent | None:
        return (
            self._db.query(EvidenceEvent)
            .filter(
                EvidenceEvent.trajectory_id == trajectory_id,
                EvidenceEvent.source_type == source_type,
                EvidenceEvent.source_reference == source_reference,
                EvidenceEvent.event_type == event_type,
            )
            .first()
        )

    def next_sequence_number(self, trajectory_id: UUID) -> int:
        current = (
            self._db.query(EvidenceEvent.sequence_number)
            .filter(EvidenceEvent.trajectory_id == trajectory_id)
            .order_by(EvidenceEvent.sequence_number.desc())
            .first()
        )
        return (current[0] + 1) if current else 1

    def create_event(self, **fields: Any) -> EvidenceEvent:
        event = EvidenceEvent(**fields)
        self._db.add(event)
        self._db.flush()
        return event

    def list_events(
        self,
        trajectory_id: UUID,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[EvidenceEvent]:
        return (
            self._db.query(EvidenceEvent)
            .filter(EvidenceEvent.trajectory_id == trajectory_id)
            .order_by(
                EvidenceEvent.sequence_number.asc(),
                EvidenceEvent.occurred_at.asc(),
            )
            .offset(offset)
            .limit(limit)
            .all()
        )

    # --- Relations --------------------------------------------------------

    def get_relation(
        self,
        *,
        trajectory_id: UUID,
        source_entity_type: str,
        source_entity_id: str,
        relation_type: str,
        target_entity_type: str,
        target_entity_id: str,
    ) -> EvidenceRelation | None:
        return (
            self._db.query(EvidenceRelation)
            .filter(
                EvidenceRelation.trajectory_id == trajectory_id,
                EvidenceRelation.source_entity_type == source_entity_type,
                EvidenceRelation.source_entity_id == source_entity_id,
                EvidenceRelation.relation_type == relation_type,
                EvidenceRelation.target_entity_type == target_entity_type,
                EvidenceRelation.target_entity_id == target_entity_id,
            )
            .first()
        )

    def create_relation(self, **fields: Any) -> EvidenceRelation:
        relation = EvidenceRelation(**fields)
        self._db.add(relation)
        self._db.flush()
        return relation

    def list_relations(self, trajectory_id: UUID) -> list[EvidenceRelation]:
        return (
            self._db.query(EvidenceRelation)
            .filter(EvidenceRelation.trajectory_id == trajectory_id)
            .order_by(EvidenceRelation.created_at.asc())
            .all()
        )

    # --- Findings ---------------------------------------------------------

    def find_open_finding(
        self,
        *,
        trajectory_id: UUID,
        finding_type: str,
    ) -> EvidenceFinding | None:
        return (
            self._db.query(EvidenceFinding)
            .filter(
                EvidenceFinding.trajectory_id == trajectory_id,
                EvidenceFinding.finding_type == finding_type,
                EvidenceFinding.status == "OPEN",
            )
            .first()
        )

    def create_finding(self, **fields: Any) -> EvidenceFinding:
        finding = EvidenceFinding(**fields)
        self._db.add(finding)
        self._db.flush()
        return finding

    def list_findings(self, trajectory_id: UUID) -> list[EvidenceFinding]:
        return (
            self._db.query(EvidenceFinding)
            .filter(EvidenceFinding.trajectory_id == trajectory_id)
            .order_by(EvidenceFinding.created_at.asc())
            .all()
        )

    # --- Artifacts --------------------------------------------------------

    def create_artifact(self, **fields: Any) -> EvidenceArtifact:
        artifact = EvidenceArtifact(**fields)
        self._db.add(artifact)
        self._db.flush()
        return artifact

    def get_artifact(
        self, *, trajectory_id: UUID, artifact_id: UUID
    ) -> EvidenceArtifact | None:
        return (
            self._db.query(EvidenceArtifact)
            .filter(
                EvidenceArtifact.trajectory_id == trajectory_id,
                EvidenceArtifact.id == artifact_id,
            )
            .first()
        )

    def find_artifact_by_digest(
        self, *, trajectory_id: UUID, content_digest: str
    ) -> EvidenceArtifact | None:
        return (
            self._db.query(EvidenceArtifact)
            .filter(
                EvidenceArtifact.trajectory_id == trajectory_id,
                EvidenceArtifact.content_digest == content_digest,
            )
            .first()
        )

    def list_artifacts(self, trajectory_id: UUID) -> list[EvidenceArtifact]:
        return (
            self._db.query(EvidenceArtifact)
            .filter(EvidenceArtifact.trajectory_id == trajectory_id)
            .order_by(EvidenceArtifact.created_at.asc())
            .all()
        )

    def get_event(self, *, trajectory_id: UUID, event_id: UUID) -> EvidenceEvent | None:
        return (
            self._db.query(EvidenceEvent)
            .filter(
                EvidenceEvent.trajectory_id == trajectory_id,
                EvidenceEvent.id == event_id,
            )
            .first()
        )

    def list_artifacts_for_event(
        self, *, trajectory_id: UUID, event_id: UUID
    ) -> list[EvidenceArtifact]:
        return (
            self._db.query(EvidenceArtifact)
            .filter(
                EvidenceArtifact.trajectory_id == trajectory_id,
                EvidenceArtifact.event_id == event_id,
            )
            .order_by(EvidenceArtifact.created_at.asc())
            .all()
        )
