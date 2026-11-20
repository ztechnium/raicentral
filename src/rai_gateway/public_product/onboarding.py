"""Instance onboarding state and checklist service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.models.governance import (
    OrganizationGovernanceProfile,
    OrganizationMembership,
)
from rai_gateway.models.identity import AgentRuntimeIdentity
from rai_gateway.models.policy_engine import ContentRule, PolicyCollection, ToolRule
from rai_gateway.models.productization import (
    OrganizationOnboarding,
    SdkDecisionRecord,
    SdkIntegration,
)
from rai_gateway.models.registry import (
    Agent,
    AgentVersion,
    AgentVersionDependency,
    AISystem,
)

_SINGLETON_KEY = "default"


class OnboardingStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    INTEGRATED = "INTEGRATED"
    READY = "READY"


class CompletionSource(StrEnum):
    """Why a checklist item is done. Provenance only; never an attestation."""

    NONE = "NONE"
    MANUAL = "MANUAL"
    RECORD = "RECORD"
    OBSERVATION = "OBSERVATION"


class ConnectionState(StrEnum):
    UNSCOPED = "UNSCOPED"
    NOT_OBSERVED = "NOT_OBSERVED"
    OBSERVED = "OBSERVED"


class VerificationState(StrEnum):
    NOT_RUN = "NOT_RUN"


ONBOARDING_STEP_DEFS: list[tuple[str, str]] = [
    ("organization_profile", "Organization Profile"),
    ("team_roles", "Team & Governance Roles"),
    ("first_ai_system", "Register First AI System"),
    ("agent_version", "Add Agent + Version"),
    ("resources", "Add Model / MCP / Tools / Data Sources"),
    ("first_policies", "Configure First Policies"),
    ("choose_integration", "Choose Integration"),
    ("runtime_identity", "Create Runtime Identity"),
    ("connect_application", "Connect Application"),
    ("test_connection", "Test Connection"),
    ("first_governed_action", "Run First Governed Action"),
    ("inspect_trajectory", "Inspect First Trajectory"),
    ("production_readiness", "Production Readiness"),
]

LEGACY_ONBOARDING_STEPS: list[tuple[str, str]] = [
    ("organization", "Create Organization"),
    ("admin", "Invite Organization Admin"),
    ("ai_system", "Register first AI System"),
    ("integration", "Create SDK Integration"),
]

ONBOARDING_STEP_IDS = {step_id for step_id, _ in ONBOARDING_STEP_DEFS}

# Steps whose completion can only come from server-side evidence. A stored
# manual flag (including a historical one) never completes them.
EVIDENCE_ONLY_STEP_IDS = frozenset(
    {
        "connect_application",
        "test_connection",
        "first_governed_action",
        "production_readiness",
    }
)

# Steps the product cannot establish yet (a later verification phase). They
# stay incomplete, are informational, and are never a "next action".
UNSUPPORTED_STEP_IDS = frozenset({"test_connection", "production_readiness"})


class _Unset:
    """Sentinel type: argument omitted (preserve) as opposed to None (clear)."""

    _instance: "_Unset | None" = None

    def __new__(cls) -> "_Unset":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()


@dataclass(frozen=True)
class _Selection:
    system_id: UUID
    integration: SdkIntegration
    runtime: AgentRuntimeIdentity


class OnboardingError(Exception):
    def __init__(self, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status


class OnboardingService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_record(self) -> OrganizationOnboarding | None:
        return (
            self._db.query(OrganizationOnboarding)
            .filter(OrganizationOnboarding.singleton_key == _SINGLETON_KEY)
            .first()
        )

    def get_or_create(
        self,
        *,
        status: str = OnboardingStatus.NOT_STARTED.value,
        current_step: str = "organization_profile",
        completed_steps: list[str] | None = None,
    ) -> OrganizationOnboarding:
        record = self.get_record()
        if record:
            return record
        record = OrganizationOnboarding(
            singleton_key=_SINGLETON_KEY,
            status=status,
            current_step=current_step,
            completed_steps=completed_steps or [],
        )
        self._db.add(record)
        self._db.flush()
        return record

    def serialize(self, record: OrganizationOnboarding) -> dict[str, Any]:
        return {
            "current_step": record.current_step,
            "completed_steps": list(record.completed_steps or []),
            "first_ai_system_id": str(record.first_ai_system_id)
            if record.first_ai_system_id
            else None,
            "first_integration_id": str(record.first_integration_id)
            if record.first_integration_id
            else None,
            "first_trajectory_id": str(record.first_trajectory_id)
            if record.first_trajectory_id
            else None,
            "status": record.status,
            "welcome_completed": record.welcome_completed_at is not None,
            "welcome_completed_at": record.welcome_completed_at.isoformat()
            if record.welcome_completed_at
            else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def update(
        self,
        *,
        current_step: str | None = None,
        complete_step: str | None = None,
        completed_steps: list[str] | None = None,
        first_ai_system_id: UUID | None | _Unset = UNSET,
        first_integration_id: UUID | None | _Unset = UNSET,
        first_trajectory_id: UUID | None | _Unset = UNSET,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Apply a setup patch.

        Selected-ID arguments: ``UNSET`` (omitted) preserves the stored value,
        ``None`` clears it, and a UUID sets it.
        """
        record = self.get_or_create()

        if current_step is not None:
            if current_step not in ONBOARDING_STEP_IDS:
                raise OnboardingError(f"Unknown onboarding step: {current_step}")
            record.current_step = current_step
            if record.status == OnboardingStatus.NOT_STARTED.value:
                record.status = OnboardingStatus.IN_PROGRESS.value

        if completed_steps is not None:
            record.completed_steps = list(completed_steps)

        if complete_step is not None:
            if complete_step not in ONBOARDING_STEP_IDS:
                raise OnboardingError(f"Unknown onboarding step: {complete_step}")
            steps = set(record.completed_steps or [])
            steps.add(complete_step)
            record.completed_steps = sorted(steps)
            if record.status == OnboardingStatus.NOT_STARTED.value:
                record.status = OnboardingStatus.IN_PROGRESS.value

        if not isinstance(first_ai_system_id, _Unset):
            record.first_ai_system_id = first_ai_system_id
        if not isinstance(first_integration_id, _Unset):
            record.first_integration_id = first_integration_id
        if not isinstance(first_trajectory_id, _Unset):
            record.first_trajectory_id = first_trajectory_id

        if status is not None:
            if status not in {item.value for item in OnboardingStatus}:
                raise OnboardingError(f"Invalid onboarding status: {status}")
            record.status = status

        self._db.commit()
        self._db.refresh(record)
        return self.build_checklist(record)

    def build_checklist(
        self,
        record: OrganizationOnboarding | None = None,
    ) -> dict[str, Any]:
        if record is None:
            record = self.get_record()

        selection = self._resolve_selection(record)
        summary = self._build_summary(record, selection)
        sources = self._completion_sources(record, selection)

        steps: list[dict[str, Any]] = []
        for step_id, label in ONBOARDING_STEP_DEFS:
            source = sources[step_id]
            steps.append(
                {
                    "id": step_id,
                    "label": label,
                    "done": source != CompletionSource.NONE,
                    "blocked": False,
                    "blocked_reason": None,
                    "completion_source": source.value,
                    "actionable": step_id not in UNSUPPORTED_STEP_IDS,
                }
            )

        legacy_steps = self._legacy_steps()

        completed_count = sum(1 for step in steps if step["done"])
        next_step = next(
            (step for step in steps if not step["done"] and step["actionable"]),
            None,
        )
        onboarding_payload = (
            self.serialize(record)
            if record
            else {
                "current_step": "organization_profile",
                "completed_steps": [],
                "first_ai_system_id": None,
                "first_integration_id": None,
                "first_trajectory_id": None,
                "status": OnboardingStatus.NOT_STARTED.value,
                "welcome_completed": False,
                "welcome_completed_at": None,
                "created_at": None,
                "updated_at": None,
            }
        )

        # A stored legacy READY value is workflow history only. The returned
        # status is never READY: it is neither derived from nor proof of
        # runtime verification.
        status = onboarding_payload["status"]
        if (
            summary["connection"]["state"] == ConnectionState.OBSERVED.value
            and sources["first_governed_action"] == CompletionSource.OBSERVATION
        ):
            status = OnboardingStatus.INTEGRATED.value
        elif completed_count > 0 or record:
            status = OnboardingStatus.IN_PROGRESS.value

        return {
            "onboarding": {**onboarding_payload, "status": status},
            "summary": summary,
            "steps": steps,
            "legacy_steps": legacy_steps,
            "complete": all(step["done"] for step in steps),
            "legacy_complete": all(step["done"] for step in legacy_steps),
            "progress": {
                "completed": completed_count,
                "total": len(steps),
            },
            "next_recommended_action": next_step["label"] if next_step else None,
            "blocked_steps": [],
        }

    # ------------------------------------------------------------------
    # Selected-context resolution and read-only summary
    # ------------------------------------------------------------------

    def _resolve_selection(
        self, record: OrganizationOnboarding | None
    ) -> _Selection | None:
        """Resolve the saved system + integration into one consistent chain.

        Returns None when either is unset, missing, or they do not agree. Never
        falls back to the first available integration or trajectory.
        """
        if (
            record is None
            or record.first_ai_system_id is None
            or record.first_integration_id is None
        ):
            return None
        integration = (
            self._db.query(SdkIntegration)
            .filter(SdkIntegration.id == record.first_integration_id)
            .first()
        )
        if integration is None or integration.runtime_identity_id is None:
            return None
        runtime = (
            self._db.query(AgentRuntimeIdentity)
            .filter(AgentRuntimeIdentity.id == integration.runtime_identity_id)
            .first()
        )
        if runtime is None:
            return None
        agent = self._db.query(Agent).filter(Agent.id == runtime.agent_id).first()
        if agent is None or agent.ai_system_id != record.first_ai_system_id:
            return None
        version = (
            self._db.query(AgentVersion)
            .filter(AgentVersion.id == runtime.agent_version_id)
            .first()
        )
        if version is None or version.agent_id != agent.id:
            return None
        system = (
            self._db.query(AISystem.id)
            .filter(AISystem.id == agent.ai_system_id)
            .first()
        )
        if system is None:
            return None
        return _Selection(
            system_id=agent.ai_system_id, integration=integration, runtime=runtime
        )

    def _build_summary(
        self,
        record: OrganizationOnboarding | None,
        selection: _Selection | None,
    ) -> dict[str, Any]:
        connection: dict[str, Any] = {
            "state": ConnectionState.UNSCOPED.value,
            "integration_id": None,
            "runtime_identity_id": None,
            "last_observed_at": None,
        }
        if selection is not None:
            observed_at = selection.integration.last_heartbeat_at
            connection = {
                "state": (
                    ConnectionState.OBSERVED.value
                    if observed_at is not None
                    else ConnectionState.NOT_OBSERVED.value
                ),
                "integration_id": str(selection.integration.id),
                "runtime_identity_id": str(selection.runtime.id),
                "last_observed_at": observed_at.isoformat() if observed_at else None,
            }
        return {
            "welcome_complete": bool(record and record.welcome_completed_at),
            "connection": connection,
            # Step 01 never establishes verification; a later phase extends this.
            "verification": {"state": VerificationState.NOT_RUN.value},
        }

    def _legacy_steps(self) -> list[dict[str, Any]]:
        systems = self._db.query(AISystem).count()
        integrations = self._db.query(SdkIntegration).count()
        admins = (
            self._db.query(OrganizationMembership)
            .filter(
                OrganizationMembership.role == "ORGANIZATION_ADMIN",
                OrganizationMembership.status == "ACTIVE",
            )
            .count()
        )
        profile = (
            self._db.query(OrganizationGovernanceProfile)
            .filter(OrganizationGovernanceProfile.singleton_key == _SINGLETON_KEY)
            .first()
        )
        legacy_done = {
            "organization": profile is not None,
            "admin": admins > 0,
            "ai_system": systems > 0,
            "integration": integrations > 0,
        }
        return [
            {"id": step_id, "label": label, "done": legacy_done.get(step_id, False)}
            for step_id, label in LEGACY_ONBOARDING_STEPS
        ]

    # ------------------------------------------------------------------
    # Checklist provenance
    # ------------------------------------------------------------------

    def _scope_system_ids(self, record: OrganizationOnboarding | None) -> list[UUID]:
        """The selected system when it exists, otherwise every system."""
        if record is not None and record.first_ai_system_id is not None:
            exists = (
                self._db.query(AISystem.id)
                .filter(AISystem.id == record.first_ai_system_id)
                .first()
            )
            if exists is not None:
                return [record.first_ai_system_id]
        return [row[0] for row in self._db.query(AISystem.id).all()]

    def _completion_sources(
        self,
        record: OrganizationOnboarding | None,
        selection: _Selection | None,
    ) -> dict[str, CompletionSource]:
        persisted = set(record.completed_steps or []) if record else set()
        recorded = self._recorded_steps(record)

        observed = {"connect_application": False, "first_governed_action": False}
        if selection is not None:
            observed["connect_application"] = (
                selection.integration.last_heartbeat_at is not None
            )
            observed["first_governed_action"] = (
                self._db.query(SdkDecisionRecord.id)
                .filter(SdkDecisionRecord.integration_id == selection.integration.id)
                .first()
                is not None
            )

        sources: dict[str, CompletionSource] = {}
        for step_id, _label in ONBOARDING_STEP_DEFS:
            if step_id in UNSUPPORTED_STEP_IDS:
                source = CompletionSource.NONE
            elif step_id in EVIDENCE_ONLY_STEP_IDS:
                source = (
                    CompletionSource.OBSERVATION
                    if observed.get(step_id)
                    else CompletionSource.NONE
                )
            elif step_id == "inspect_trajectory":
                # Finding a trajectory is not review; only an explicit mark counts.
                source = (
                    CompletionSource.MANUAL
                    if step_id in persisted
                    else CompletionSource.NONE
                )
            elif recorded.get(step_id):
                source = CompletionSource.RECORD
            elif step_id in persisted:
                source = CompletionSource.MANUAL
            else:
                source = CompletionSource.NONE
            sources[step_id] = source
        return sources

    def _recorded_steps(self, record: OrganizationOnboarding | None) -> dict[str, bool]:
        """Steps established by actual records (never by a saved UUID alone)."""
        db = self._db
        profile = (
            db.query(OrganizationGovernanceProfile)
            .filter(OrganizationGovernanceProfile.singleton_key == _SINGLETON_KEY)
            .first()
        )
        membership_roles = {
            row[0]
            for row in db.query(OrganizationMembership.role)
            .filter(OrganizationMembership.status == "ACTIVE")
            .all()
        }
        scope = self._scope_system_ids(record)

        def has_agent_versions() -> bool:
            return (
                db.query(AgentVersion.id)
                .join(Agent, Agent.id == AgentVersion.agent_id)
                .filter(Agent.ai_system_id.in_(scope))
                .first()
                is not None
            )

        def has_resources() -> bool:
            return (
                db.query(AgentVersionDependency.id)
                .join(
                    AgentVersion,
                    AgentVersion.id == AgentVersionDependency.agent_version_id,
                )
                .join(Agent, Agent.id == AgentVersion.agent_id)
                .filter(Agent.ai_system_id.in_(scope))
                .first()
                is not None
            )

        def has_rules() -> bool:
            # A disabled rule is still a recorded configuration object; it is
            # never reported as active or enforced. ControlAssessment is a
            # different concept and does not count.
            content = (
                db.query(ContentRule.id)
                .join(
                    PolicyCollection,
                    PolicyCollection.id == ContentRule.policy_collection_id,
                )
                .filter(
                    PolicyCollection.ai_system_id.in_(scope),
                    ContentRule.archived_at.is_(None),
                )
                .first()
            )
            if content is not None:
                return True
            return (
                db.query(ToolRule.id)
                .join(
                    PolicyCollection,
                    PolicyCollection.id == ToolRule.policy_collection_id,
                )
                .filter(
                    PolicyCollection.ai_system_id.in_(scope),
                    ToolRule.archived_at.is_(None),
                )
                .first()
                is not None
            )

        def has_integrations() -> bool:
            return (
                db.query(SdkIntegration.id)
                .join(
                    AgentRuntimeIdentity,
                    AgentRuntimeIdentity.id == SdkIntegration.runtime_identity_id,
                )
                .join(Agent, Agent.id == AgentRuntimeIdentity.agent_id)
                .filter(Agent.ai_system_id.in_(scope))
                .first()
                is not None
            )

        def has_runtime_identities() -> bool:
            return (
                db.query(AgentRuntimeIdentity.id)
                .join(Agent, Agent.id == AgentRuntimeIdentity.agent_id)
                .filter(Agent.ai_system_id.in_(scope))
                .first()
                is not None
            )

        return {
            "organization_profile": bool(
                profile
                and (profile.sector or profile.jurisdiction or profile.legal_name)
            ),
            "team_roles": "RAI_OFFICER" in membership_roles
            and "ORGANIZATION_ADMIN" in membership_roles,
            "first_ai_system": len(scope) > 0,
            "agent_version": has_agent_versions(),
            "resources": has_resources(),
            "first_policies": has_rules(),
            "choose_integration": has_integrations(),
            "runtime_identity": has_runtime_identities(),
        }
