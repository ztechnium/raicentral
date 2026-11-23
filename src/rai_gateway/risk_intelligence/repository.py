"""Persistence helpers for WP6 risk intelligence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from rai_gateway.models.evidence import EvidenceFinding, RuntimeTrajectory
from rai_gateway.models.governance import (
    AISystemAccountability,
    OrganizationMembership,
)
from rai_gateway.models.registry import AgentVersionDependency
from rai_gateway.models.risk_intelligence import (
    ContentEvaluationResult,
    ContentEvaluatorConfig,
    InterventionDecision,
    PolicyRecommendation,
    RiskMonitor,
    RiskMonitorRule,
    RiskSignal,
    RiskState,
    RuntimeConstraint,
    RuntimeRiskFinding,
)


class RiskIntelligenceRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _owned_query(
        self,
        model: Any,
    ):
        query = self._db.query(model)
        return query

    # --- Evaluator config -------------------------------------------------

    def get_evaluator_config(
        self,
        ai_system_id: UUID | None = None,
    ) -> ContentEvaluatorConfig | None:
        """Evaluator for an AI system: CONTENT_EVALUATOR_* env first, then saved config."""
        env = self._env_evaluator_config()
        if env is not None:
            return env
        if ai_system_id is not None:
            row = (
                self._db.query(ContentEvaluatorConfig)
                .filter(ContentEvaluatorConfig.ai_system_id == ai_system_id)
                .first()
            )
            if row:
                return row
        # Platform-global / default (ai_system_id IS NULL) - runtime only
        return (
            self._db.query(ContentEvaluatorConfig)
            .filter(ContentEvaluatorConfig.ai_system_id.is_(None))
            .first()
        )

    def get_saved_evaluator_config(
        self, ai_system_id: UUID
    ) -> ContentEvaluatorConfig | None:
        """The UI/API-saved row only (used by the settings screen and upsert)."""
        return (
            self._db.query(ContentEvaluatorConfig)
            .filter(ContentEvaluatorConfig.ai_system_id == ai_system_id)
            .first()
        )

    @staticmethod
    def _env_evaluator_config() -> ContentEvaluatorConfig | None:
        """Detached default built from CONTENT_EVALUATOR_* settings (never persisted)."""
        from rai_gateway.core.ai_provider import normalize_provider, try_load_ai_config
        from rai_gateway.core.config import settings

        # CONTENT_EVALUATOR_* wins when set; otherwise the unified RAI_AI_* applies.
        unified, _problem = try_load_ai_config()
        provider = (settings.content_evaluator_provider or "").strip().lower()
        if provider:
            same = unified is not None and normalize_provider(provider) == unified.provider
            model = settings.content_evaluator_model or (unified.model if same else "")
        elif unified is not None:
            provider, model = unified.provider, unified.model
        else:
            return None
        return ContentEvaluatorConfig(
            ai_system_id=None,
            provider=provider,
            model=model,
            temperature=settings.content_evaluator_temperature,
            timeout_seconds=settings.content_evaluator_timeout_seconds,
            max_rules_per_call=20,
            confidence_threshold=0.5,
            minimum_confidence_to_mark=0.5,
            minimum_confidence_to_monitor=0.7,
            minimum_confidence_to_intervene=(
                settings.content_evaluator_min_confidence_to_intervene
            ),
            fail_closed=settings.content_evaluator_fail_closed,
            enabled=True,
            health_status="UNKNOWN",
        )

    def upsert_evaluator_config(self, **fields: Any) -> ContentEvaluatorConfig:
        ai_system_id = fields.get("ai_system_id")
        # Prefer exact match for upsert when system-scoped
        if ai_system_id is not None:
            existing = (
                self._db.query(ContentEvaluatorConfig)
                .filter(ContentEvaluatorConfig.ai_system_id == ai_system_id)
                .first()
            )
        else:
            existing = (
                self._db.query(ContentEvaluatorConfig)
                .filter(ContentEvaluatorConfig.ai_system_id.is_(None))
                .first()
            )
        if existing:
            for key, value in fields.items():
                if value is not None or key in (
                    "api_key_encrypted",
                    "fallback_provider",
                ):
                    setattr(existing, key, value)
            self._db.flush()
            return existing
        row = ContentEvaluatorConfig(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    # --- Signals ----------------------------------------------------------

    def create_signal(self, **fields: Any) -> RiskSignal:
        row = RiskSignal(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def list_signals(
        self,
        *,
        ai_system_id: UUID | None = None,
        trajectory_id: UUID | None = None,
        runtime_identity_id: UUID | None = None,
        signal_type: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RiskSignal]:
        q = self._owned_query(RiskSignal)
        if ai_system_id:
            q = q.filter(RiskSignal.ai_system_id == ai_system_id)
        if trajectory_id:
            q = q.filter(RiskSignal.trajectory_id == trajectory_id)
        if runtime_identity_id:
            q = q.filter(RiskSignal.runtime_identity_id == runtime_identity_id)
        if signal_type:
            q = q.filter(RiskSignal.signal_type == signal_type)
        if since:
            q = q.filter(RiskSignal.occurred_at >= since)
        return (
            q.order_by(RiskSignal.occurred_at.desc()).offset(offset).limit(limit).all()
        )

    def count_signals_in_window(
        self,
        *,
        ai_system_id: UUID,
        runtime_identity_id: UUID | None,
        signal_type: str | None,
        window_seconds: int,
        tool_name: str | None = None,
    ) -> int:
        since = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        q = self._db.query(func.count(RiskSignal.id)).filter(
            RiskSignal.ai_system_id == ai_system_id,
            RiskSignal.occurred_at >= since,
        )
        if runtime_identity_id:
            q = q.filter(RiskSignal.runtime_identity_id == runtime_identity_id)
        if signal_type:
            q = q.filter(RiskSignal.signal_type == signal_type)
        if tool_name:
            q = q.filter(RiskSignal.payload["tool_name"].as_string() == tool_name)
        return int(q.scalar() or 0)

    # --- Risk state -------------------------------------------------------

    def get_risk_state(self, scope_type: str, scope_id: str) -> RiskState | None:
        return (
            self._db.query(RiskState)
            .filter(RiskState.scope_type == scope_type, RiskState.scope_id == scope_id)
            .first()
        )

    def upsert_risk_state(self, **fields: Any) -> RiskState:
        existing = self.get_risk_state(fields["scope_type"], fields["scope_id"])
        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing
        row = RiskState(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def list_risk_states(
        self,
        *,
        ai_system_id: UUID | None = None,
        monitoring_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RiskState]:
        q = self._owned_query(RiskState)
        if ai_system_id:
            q = q.filter(RiskState.ai_system_id == ai_system_id)
        if monitoring_only:
            q = q.filter(RiskState.monitoring_status == "ACTIVE")
        return q.order_by(RiskState.updated_at.desc()).offset(offset).limit(limit).all()

    # --- Findings ---------------------------------------------------------

    def create_finding(self, **fields: Any) -> RuntimeRiskFinding:
        row = RuntimeRiskFinding(**fields)
        self._db.add(row)
        self._db.flush()
        from rai_gateway.events.outbox import enqueue_finding_alert

        enqueue_finding_alert(self._db, row)
        return row

    def get_finding(
        self,
        finding_id: UUID,
    ) -> RuntimeRiskFinding | None:
        return (
            self._owned_query(RuntimeRiskFinding)
            .filter(RuntimeRiskFinding.id == finding_id)
            .first()
        )


    _ACCOUNTABILITY_COLUMNS = (
        "business_owner_membership_id",
        "technical_owner_membership_id",
        "rai_officer_membership_id",
        "risk_owner_membership_id",
        "incident_owner_membership_id",
    )

    def _apply_scope_filters(
        self,
        q: Any,
        *,
        system_col: Any,
        agent_col: Any,
        version_col: Any,
        identity_col: Any,
        created_col: Any,
        agent_id: UUID | None = None,
        model_id: UUID | None = None,
        runtime_identity_id: UUID | None = None,
        department_id: UUID | None = None,
        member_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> Any:
        """Filters shared by runtime findings and trajectory evidence findings."""
        if agent_id:
            q = q.filter(agent_col == agent_id)
        if runtime_identity_id:
            q = q.filter(identity_col == runtime_identity_id)
        if model_id:
            q = q.filter(
                version_col.in_(
                    self._db.query(AgentVersionDependency.agent_version_id).filter(
                        AgentVersionDependency.resource_type == "model",
                        AgentVersionDependency.resource_id == model_id,
                    )
                )
            )
        if member_id or department_id:
            owner_cols = [
                getattr(AISystemAccountability, name)
                for name in self._ACCOUNTABILITY_COLUMNS
            ]
            acc = self._db.query(AISystemAccountability.ai_system_id)
            if member_id:
                acc = acc.filter(or_(*[c == member_id for c in owner_cols]))
            if department_id:
                dept_members = self._db.query(OrganizationMembership.id).filter(
                    OrganizationMembership.department_id == department_id
                )
                acc = acc.filter(or_(*[c.in_(dept_members) for c in owner_cols]))
            q = q.filter(system_col.in_(acc))
        if date_from:
            q = q.filter(created_col >= date_from)
        if date_to:
            q = q.filter(created_col <= date_to)
        return q

    def _runtime_findings_query(self, q: Any, **scope: Any) -> Any:
        return self._apply_scope_filters(
            q,
            system_col=RuntimeRiskFinding.ai_system_id,
            agent_col=RuntimeRiskFinding.agent_id,
            version_col=RuntimeRiskFinding.agent_version_id,
            identity_col=RuntimeRiskFinding.runtime_identity_id,
            created_col=RuntimeRiskFinding.created_at,
            **scope,
        )

    def list_findings(
        self,
        *,
        ai_system_id: UUID | None = None,
        trajectory_id: UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        finding_type: str | None = None,
        agent_id: UUID | None = None,
        model_id: UUID | None = None,
        runtime_identity_id: UUID | None = None,
        department_id: UUID | None = None,
        member_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RuntimeRiskFinding]:
        q = self._owned_query(RuntimeRiskFinding)
        if ai_system_id:
            q = q.filter(RuntimeRiskFinding.ai_system_id == ai_system_id)
        if trajectory_id:
            q = q.filter(RuntimeRiskFinding.trajectory_id == trajectory_id)
        if severity:
            q = q.filter(func.upper(RuntimeRiskFinding.severity) == severity.upper())
        if status:
            q = q.filter(func.upper(RuntimeRiskFinding.status) == status.upper())
        if finding_type:
            q = q.filter(RuntimeRiskFinding.finding_type == finding_type)
        q = self._runtime_findings_query(
            q,
            agent_id=agent_id,
            model_id=model_id,
            runtime_identity_id=runtime_identity_id,
            department_id=department_id,
            member_id=member_id,
            date_from=date_from,
            date_to=date_to,
        )
        return (
            q.order_by(RuntimeRiskFinding.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count_findings(
        self,
        *,
        ai_system_id: UUID | None = None,
        trajectory_id: UUID | None = None,
        status: str | None = None,
        severity: str | None = None,
        finding_type: str | None = None,
        agent_id: UUID | None = None,
        model_id: UUID | None = None,
        runtime_identity_id: UUID | None = None,
        department_id: UUID | None = None,
        member_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        q = self._db.query(func.count(RuntimeRiskFinding.id))
        if ai_system_id:
            q = q.filter(RuntimeRiskFinding.ai_system_id == ai_system_id)
        if trajectory_id:
            q = q.filter(RuntimeRiskFinding.trajectory_id == trajectory_id)
        if status:
            q = q.filter(func.upper(RuntimeRiskFinding.status) == status.upper())
        if severity:
            q = q.filter(func.upper(RuntimeRiskFinding.severity) == severity.upper())
        if finding_type:
            q = q.filter(RuntimeRiskFinding.finding_type == finding_type)
        q = self._runtime_findings_query(
            q,
            agent_id=agent_id,
            model_id=model_id,
            runtime_identity_id=runtime_identity_id,
            department_id=department_id,
            member_id=member_id,
            date_from=date_from,
            date_to=date_to,
        )
        return int(q.scalar() or 0)

    def _evidence_findings_query(
        self,
        *,
        ai_system_id: UUID | None = None,
        trajectory_id: UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        finding_type: str | None = None,
        agent_id: UUID | None = None,
        model_id: UUID | None = None,
        runtime_identity_id: UUID | None = None,
        department_id: UUID | None = None,
        member_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        """Trajectory evidence findings, the same set reports count as open findings."""
        query = self._db.query(EvidenceFinding, RuntimeTrajectory).join(
            RuntimeTrajectory, EvidenceFinding.trajectory_id == RuntimeTrajectory.id
        )
        if ai_system_id is not None:
            query = query.filter(RuntimeTrajectory.ai_system_id == ai_system_id)
        if trajectory_id is not None:
            query = query.filter(EvidenceFinding.trajectory_id == trajectory_id)
        if status:
            query = query.filter(func.upper(EvidenceFinding.status) == status.upper())
        if severity:
            query = query.filter(
                func.upper(EvidenceFinding.severity) == severity.upper()
            )
        if finding_type:
            query = query.filter(EvidenceFinding.finding_type == finding_type)
        return self._apply_scope_filters(
            query,
            system_col=RuntimeTrajectory.ai_system_id,
            agent_col=RuntimeTrajectory.agent_id,
            version_col=RuntimeTrajectory.agent_version_id,
            identity_col=RuntimeTrajectory.runtime_identity_id,
            created_col=EvidenceFinding.created_at,
            agent_id=agent_id,
            model_id=model_id,
            runtime_identity_id=runtime_identity_id,
            department_id=department_id,
            member_id=member_id,
            date_from=date_from,
            date_to=date_to,
        )

    def list_evidence_findings(
        self,
        *,
        ai_system_id: UUID | None = None,
        trajectory_id: UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        finding_type: str | None = None,
        agent_id: UUID | None = None,
        model_id: UUID | None = None,
        runtime_identity_id: UUID | None = None,
        department_id: UUID | None = None,
        member_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[EvidenceFinding, RuntimeTrajectory]]:
        return (
            self._evidence_findings_query(
                ai_system_id=ai_system_id,
                trajectory_id=trajectory_id,
                severity=severity,
                status=status,
                finding_type=finding_type,
                agent_id=agent_id,
                model_id=model_id,
                runtime_identity_id=runtime_identity_id,
                department_id=department_id,
                member_id=member_id,
                date_from=date_from,
                date_to=date_to,
            )
            .order_by(EvidenceFinding.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count_evidence_findings(
        self,
        *,
        ai_system_id: UUID | None = None,
        trajectory_id: UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        finding_type: str | None = None,
        agent_id: UUID | None = None,
        model_id: UUID | None = None,
        runtime_identity_id: UUID | None = None,
        department_id: UUID | None = None,
        member_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> int:
        return int(
            self._evidence_findings_query(
                ai_system_id=ai_system_id,
                trajectory_id=trajectory_id,
                severity=severity,
                status=status,
                finding_type=finding_type,
                agent_id=agent_id,
                model_id=model_id,
                runtime_identity_id=runtime_identity_id,
                department_id=department_id,
                member_id=member_id,
                date_from=date_from,
                date_to=date_to,
            )
            .with_entities(func.count(EvidenceFinding.id))
            .scalar()
            or 0
        )

    def get_evidence_finding(
        self,
        finding_id: UUID,
    ) -> tuple[EvidenceFinding, RuntimeTrajectory] | None:
        query = self._db.query(EvidenceFinding, RuntimeTrajectory).join(
            RuntimeTrajectory, EvidenceFinding.trajectory_id == RuntimeTrajectory.id
        )
        return query.filter(EvidenceFinding.id == finding_id).first()

    # --- Monitor rules / monitors -----------------------------------------

    def create_monitor_rule(self, **fields: Any) -> RiskMonitorRule:
        row = RiskMonitorRule(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def list_monitor_rules(
        self,
        *,
        ai_system_id: UUID,
        enabled_only: bool = False,
    ) -> list[RiskMonitorRule]:
        q = self._owned_query(RiskMonitorRule).filter(
            RiskMonitorRule.ai_system_id == ai_system_id
        )
        if enabled_only:
            q = q.filter(RiskMonitorRule.enabled.is_(True))
        return q.order_by(RiskMonitorRule.created_at.desc()).all()

    def get_monitor_rule(
        self,
        rule_id: UUID,
    ) -> RiskMonitorRule | None:
        return (
            self._owned_query(RiskMonitorRule)
            .filter(RiskMonitorRule.id == rule_id)
            .first()
        )

    def create_monitor(self, **fields: Any) -> RiskMonitor:
        row = RiskMonitor(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def list_monitors(
        self,
        *,
        ai_system_id: UUID | None = None,
        status: str | None = "ACTIVE",
        limit: int = 50,
        offset: int = 0,
    ) -> list[RiskMonitor]:
        q = self._owned_query(RiskMonitor)
        if ai_system_id:
            q = q.filter(RiskMonitor.ai_system_id == ai_system_id)
        if status:
            q = q.filter(RiskMonitor.status == status)
        return (
            q.order_by(RiskMonitor.started_at.desc()).offset(offset).limit(limit).all()
        )

    def get_active_monitor(
        self, *, scope_type: str, scope_id: str
    ) -> RiskMonitor | None:
        now = datetime.now(timezone.utc)
        return (
            self._db.query(RiskMonitor)
            .filter(
                RiskMonitor.scope_type == scope_type,
                RiskMonitor.scope_id == scope_id,
                RiskMonitor.status == "ACTIVE",
                or_(RiskMonitor.expires_at.is_(None), RiskMonitor.expires_at > now),
            )
            .order_by(RiskMonitor.started_at.desc())
            .first()
        )

    # --- Constraints ------------------------------------------------------

    def create_constraint(self, **fields: Any) -> RuntimeConstraint:
        row = RuntimeConstraint(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def list_active_constraints(
        self,
        *,
        ai_system_id: UUID,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> list[RuntimeConstraint]:
        now = datetime.now(timezone.utc)
        q = self._db.query(RuntimeConstraint).filter(
            RuntimeConstraint.ai_system_id == ai_system_id,
            RuntimeConstraint.status == "ACTIVE",
            or_(
                RuntimeConstraint.expires_at.is_(None),
                RuntimeConstraint.expires_at > now,
            ),
        )
        if scope_type and scope_id:
            q = q.filter(
                RuntimeConstraint.scope_type == scope_type,
                RuntimeConstraint.scope_id == scope_id,
            )
        return q.all()

    # --- Interventions ----------------------------------------------------

    def create_intervention(self, **fields: Any) -> InterventionDecision:
        row = InterventionDecision(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def get_intervention(
        self,
        intervention_id: UUID,
    ) -> InterventionDecision | None:
        return (
            self._owned_query(InterventionDecision)
            .filter(InterventionDecision.id == intervention_id)
            .first()
        )

    def list_interventions(
        self,
        *,
        ai_system_id: UUID | None = None,
        trajectory_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterventionDecision]:
        q = self._owned_query(InterventionDecision)
        if ai_system_id:
            q = q.filter(InterventionDecision.ai_system_id == ai_system_id)
        if trajectory_id:
            q = q.filter(InterventionDecision.trajectory_id == trajectory_id)
        if status:
            q = q.filter(InterventionDecision.status == status)
        return (
            q.order_by(InterventionDecision.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    # --- Recommendations --------------------------------------------------

    def create_recommendation(self, **fields: Any) -> PolicyRecommendation:
        row = PolicyRecommendation(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def get_recommendation(
        self,
        rec_id: UUID,
    ) -> PolicyRecommendation | None:
        return (
            self._owned_query(PolicyRecommendation)
            .filter(PolicyRecommendation.id == rec_id)
            .first()
        )

    def find_recommendation_by_pattern(
        self, *, ai_system_id: UUID, pattern_key: str
    ) -> PolicyRecommendation | None:
        return (
            self._db.query(PolicyRecommendation)
            .filter(
                PolicyRecommendation.ai_system_id == ai_system_id,
                PolicyRecommendation.pattern_key == pattern_key,
                PolicyRecommendation.status.in_(["NEW", "UNDER_REVIEW"]),
            )
            .first()
        )

    def list_recommendations(
        self,
        *,
        ai_system_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PolicyRecommendation]:
        q = self._owned_query(PolicyRecommendation)
        if ai_system_id:
            q = q.filter(PolicyRecommendation.ai_system_id == ai_system_id)
        if status:
            q = q.filter(PolicyRecommendation.status == status)
        return (
            q.order_by(PolicyRecommendation.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    # --- Content evaluation cache -----------------------------------------

    def get_cached_evaluation(self, cache_key: str) -> ContentEvaluationResult | None:
        return (
            self._db.query(ContentEvaluationResult)
            .filter(ContentEvaluationResult.cache_key == cache_key)
            .first()
        )

    def create_evaluation_result(self, **fields: Any) -> ContentEvaluationResult:
        row = ContentEvaluationResult(**fields)
        self._db.add(row)
        self._db.flush()
        return row

    def list_evaluations(
        self,
        *,
        trajectory_id: UUID | None = None,
        ai_system_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContentEvaluationResult]:
        q = self._owned_query(ContentEvaluationResult)
        if trajectory_id:
            q = q.filter(ContentEvaluationResult.trajectory_id == trajectory_id)
        if ai_system_id:
            q = q.filter(ContentEvaluationResult.ai_system_id == ai_system_id)
        return (
            q.order_by(ContentEvaluationResult.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
