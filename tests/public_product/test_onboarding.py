"""Organization onboarding API tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from rai_gateway.auth.passwords import hash_password
from rai_gateway.core.database import get_db
from rai_gateway.governance.domain import GovernanceRole, MembershipStatus
from rai_gateway.governance.service import GovernanceService
from rai_gateway.main import app
from rai_gateway.models.database import AdminCredential, UserAccount
from rai_gateway.models.governance import (
    ControlAssessment,
    ControlFramework,
    ControlObjective,
    ControlRequirement,
    OrganizationGovernanceProfile,
    OrganizationMembership,
)
from rai_gateway.models.identity import AgentRuntimeIdentity
from rai_gateway.models.policy_engine import ContentRule, PolicyCollection
from rai_gateway.models.productization import (
    OrganizationOnboarding,
    SdkDecisionRecord,
    SdkIntegration,
    SdkSession,
)
from rai_gateway.models.registry import Agent, AgentVersion, AISystem
from rai_gateway.public_product.onboarding import OnboardingService
from rai_gateway.services.admin_console import _ensure_default_rbac_seed


@pytest.fixture()
def public_client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def auth_headers(public_client, db_session) -> dict[str, str]:
    _ensure_default_rbac_seed(db_session)
    suffix = uuid.uuid4().hex[:8]
    email = f"onboard-{suffix}@example.com"
    user_id = f"user-{suffix}"
    db_session.add(
        UserAccount(
            user_id=user_id,
            email=email,
            name="Onboard User",
            status="active",
            email_verified_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    db_session.add(
        AdminCredential(
            email=email,
            user_id=user_id,
            password_hash=hash_password("SecurePass123!"),
            is_active=True,
        )
    )
    for role in (
        GovernanceRole.ORGANIZATION_ADMIN.value,
        GovernanceRole.RAI_OFFICER.value,
    ):
        db_session.add(
            OrganizationMembership(
                user_id=user_id,
                role=role,
                status=MembershipStatus.ACTIVE.value,
                created_by=user_id,
            )
        )
    db_session.commit()
    login = public_client.post(
        "/v1/auth/login",
        json={"email": email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_get_onboarding_returns_full_checklist(public_client, auth_headers):
    response = public_client.get("/v1/productization/onboarding", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["steps"]) == 13
    assert "onboarding" in body
    assert body["onboarding"]["welcome_completed"] is False


def test_patch_onboarding_updates_state(public_client, db_session, auth_headers):
    response = public_client.patch(
        "/v1/productization/onboarding",
        json={
            "complete_step": "team_roles",
            "current_step": "first_ai_system",
            "status": "IN_PROGRESS",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["onboarding"]["current_step"] == "first_ai_system"
    assert "team_roles" in body["onboarding"]["completed_steps"]

    record = (
        db_session.query(OrganizationOnboarding)
        .filter(OrganizationOnboarding.singleton_key == "default")
        .first()
    )
    assert record is not None
    assert record.current_step == "first_ai_system"


def test_public_onboarding_routes_removed(public_client):
    assert public_client.get("/v1/public/onboarding").status_code == 404
    assert (
        public_client.patch(
            "/v1/public/onboarding",
            json={"complete_step": "team_roles"},
        ).status_code
        == 404
    )


def test_onboarding_service_merges_computed_steps(db_session):
    profile = (
        db_session.query(OrganizationGovernanceProfile)
        .filter(OrganizationGovernanceProfile.singleton_key == "default")
        .first()
    )
    if profile is None:
        profile = OrganizationGovernanceProfile(
            singleton_key="default",
            name="Organization",
            legal_name="Organization",
        )
        db_session.add(profile)
        db_session.commit()

    gov = GovernanceService(db_session)
    suffix = uuid.uuid4().hex[:8]
    gov.patch_governance_profile(
        updated_by="tester",
        sector="Healthcare",
        jurisdiction="DE",
    )
    gov.create_membership(
        user_id=f"admin-{suffix}",
        role=GovernanceRole.ORGANIZATION_ADMIN.value,
        created_by="tester",
        status=MembershipStatus.ACTIVE.value,
    )
    gov.create_membership(
        user_id=f"officer-{suffix}",
        role=GovernanceRole.RAI_OFFICER.value,
        created_by="tester",
        status=MembershipStatus.ACTIVE.value,
    )

    checklist = OnboardingService(db_session).build_checklist()
    step_map = {step["id"]: step["done"] for step in checklist["steps"]}
    assert step_map["organization_profile"] is True
    assert step_map["team_roles"] is True


# ---------------------------------------------------------------------------
# Step 01: summary, completion provenance, explicit-null PATCH semantics.
# The test schema is shared for the whole session, so each test resets the
# singleton onboarding record and builds its own uniquely keyed records.
# ---------------------------------------------------------------------------

STEP_IDS = [
    "organization_profile",
    "team_roles",
    "first_ai_system",
    "agent_version",
    "resources",
    "first_policies",
    "choose_integration",
    "runtime_identity",
    "connect_application",
    "test_connection",
    "first_governed_action",
    "inspect_trajectory",
    "production_readiness",
]
EVIDENCE_ONLY = (
    "connect_application",
    "test_connection",
    "first_governed_action",
    "production_readiness",
)


def _reset_record(db_session) -> OrganizationOnboarding:
    record = (
        db_session.query(OrganizationOnboarding)
        .filter(OrganizationOnboarding.singleton_key == "default")
        .first()
    )
    if record is None:
        record = OrganizationOnboarding(singleton_key="default")
        db_session.add(record)
    record.status = "NOT_STARTED"
    record.current_step = "organization_profile"
    record.completed_steps = []
    record.first_ai_system_id = None
    record.first_integration_id = None
    record.first_trajectory_id = None
    record.welcome_completed_at = None
    db_session.commit()
    return record


def _make_chain(db_session, *, heartbeat: datetime | None = None):
    suffix = uuid.uuid4().hex[:8]
    system = AISystem(
        system_key=f"sys-{suffix}", name=f"System {suffix}", created_by="tester"
    )
    db_session.add(system)
    db_session.flush()
    agent = Agent(
        agent_key=f"agent-{suffix}",
        ai_system_id=system.id,
        name="Agent",
        created_by="tester",
    )
    db_session.add(agent)
    db_session.flush()
    version = AgentVersion(agent_id=agent.id, version_number=1, created_by="tester")
    db_session.add(version)
    db_session.flush()
    runtime = AgentRuntimeIdentity(
        identity_key=f"rt-{suffix}",
        agent_id=agent.id,
        agent_version_id=version.id,
        name=f"rt-{suffix}",
        created_by="tester",
    )
    db_session.add(runtime)
    db_session.flush()
    integration = SdkIntegration(
        integration_key=f"int-{suffix}",
        name=f"Integration {suffix}",
        runtime_identity_id=runtime.id,
        created_by="tester",
        last_heartbeat_at=heartbeat,
    )
    db_session.add(integration)
    db_session.commit()
    return system, agent, version, runtime, integration


def _add_decision(db_session, integration, runtime) -> None:
    session = SdkSession(
        integration_id=integration.id,
        runtime_identity_id=runtime.id,
        correlation_id=uuid.uuid4().hex,
    )
    db_session.add(session)
    db_session.flush()
    db_session.add(
        SdkDecisionRecord(
            session_id=session.id, integration_id=integration.id, decision="ALLOW"
        )
    )
    db_session.commit()


def _select(db_session, system, integration=None) -> OrganizationOnboarding:
    record = _reset_record(db_session)
    record.first_ai_system_id = system.id
    record.first_integration_id = integration.id if integration else None
    db_session.commit()
    return record


def _steps(body) -> dict[str, dict]:
    return {step["id"]: step for step in body["steps"]}


def _get(client, headers) -> dict:
    response = client.get("/v1/productization/onboarding", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_get_returns_13_ids_and_typed_summary(public_client, db_session, auth_headers):
    _reset_record(db_session)
    body = _get(public_client, auth_headers)
    assert [step["id"] for step in body["steps"]] == STEP_IDS
    assert body["summary"] == {
        "welcome_complete": False,
        "connection": {
            "state": "UNSCOPED",
            "integration_id": None,
            "runtime_identity_id": None,
            "last_observed_at": None,
        },
        "verification": {"state": "NOT_RUN"},
    }
    patched = public_client.patch(
        "/v1/productization/onboarding",
        json={"current_step": "team_roles"},
        headers=auth_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["summary"] == body["summary"]


def test_welcome_complete_comes_only_from_timestamp(
    public_client, db_session, auth_headers
):
    record = _reset_record(db_session)
    record.completed_steps = list(STEP_IDS)
    record.status = "READY"
    db_session.commit()
    assert _get(public_client, auth_headers)["summary"]["welcome_complete"] is False
    record.welcome_completed_at = datetime.now(timezone.utc)
    db_session.commit()
    assert _get(public_client, auth_headers)["summary"]["welcome_complete"] is True


def test_legacy_ready_and_manual_flags_do_not_verify(
    public_client, db_session, auth_headers
):
    record = _reset_record(db_session)
    record.completed_steps = list(STEP_IDS)
    record.status = "READY"
    db_session.commit()
    body = _get(public_client, auth_headers)
    steps = _steps(body)
    for step_id in EVIDENCE_ONLY:
        assert steps[step_id]["done"] is False, step_id
        assert steps[step_id]["completion_source"] == "NONE"
    assert steps["inspect_trajectory"]["completion_source"] == "MANUAL"
    assert body["complete"] is False
    assert body["progress"]["completed"] < 13
    assert body["onboarding"]["status"] != "READY"
    assert body["summary"]["verification"]["state"] == "NOT_RUN"
    # Unavailable verification steps are informational, never the next action.
    assert steps["test_connection"]["actionable"] is False
    assert steps["production_readiness"]["actionable"] is False
    assert body["next_recommended_action"] not in {
        "Test Connection",
        "Production Readiness",
    }


def test_legacy_patch_cannot_promote_evidence(public_client, db_session, auth_headers):
    _reset_record(db_session)
    response = public_client.patch(
        "/v1/productization/onboarding",
        json={"completed_steps": list(EVIDENCE_ONLY), "status": "READY"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert not any(_steps(body)[i]["done"] for i in EVIDENCE_ONLY)
    assert body["summary"]["connection"]["state"] == "UNSCOPED"
    assert body["summary"]["verification"]["state"] == "NOT_RUN"


def test_heartbeat_on_selected_integration_is_observed_not_verified(
    public_client, db_session, auth_headers
):
    beat = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    system, _agent, _version, runtime, integration = _make_chain(
        db_session, heartbeat=beat
    )
    _select(db_session, system, integration)
    body = _get(public_client, auth_headers)
    connection = body["summary"]["connection"]
    assert connection["state"] == "OBSERVED"
    assert connection["integration_id"] == str(integration.id)
    assert connection["runtime_identity_id"] == str(runtime.id)
    assert datetime.fromisoformat(connection["last_observed_at"]) == beat
    steps = _steps(body)
    assert steps["connect_application"]["completion_source"] == "OBSERVATION"
    assert steps["test_connection"]["done"] is False
    assert body["summary"]["verification"]["state"] == "NOT_RUN"


def test_selected_chain_without_heartbeat_is_not_observed(
    public_client, db_session, auth_headers
):
    system, _a, _v, runtime, integration = _make_chain(db_session)
    _select(db_session, system, integration)
    body = _get(public_client, auth_headers)
    assert body["summary"]["connection"] == {
        "state": "NOT_OBSERVED",
        "integration_id": str(integration.id),
        "runtime_identity_id": str(runtime.id),
        "last_observed_at": None,
    }
    assert _steps(body)["connect_application"]["done"] is False


def test_other_integration_or_system_does_not_satisfy_selection(
    public_client, db_session, auth_headers
):
    system_a, _a1, _v1, _r1, integration_a = _make_chain(db_session)
    _sb, _a2, _v2, runtime_b, integration_b = _make_chain(
        db_session, heartbeat=datetime.now(timezone.utc)
    )
    _add_decision(db_session, integration_b, runtime_b)

    _select(db_session, system_a, integration_a)
    body = _get(public_client, auth_headers)
    assert body["summary"]["connection"]["state"] == "NOT_OBSERVED"
    assert _steps(body)["connect_application"]["done"] is False
    assert _steps(body)["first_governed_action"]["done"] is False

    # Integration B belongs to another system: UNSCOPED with no resolved IDs.
    _select(db_session, system_a, integration_b)
    body = _get(public_client, auth_headers)
    assert body["summary"]["connection"] == {
        "state": "UNSCOPED",
        "integration_id": None,
        "runtime_identity_id": None,
        "last_observed_at": None,
    }
    assert _steps(body)["connect_application"]["done"] is False
    assert _steps(body)["first_governed_action"]["done"] is False


def test_missing_selection_is_unscoped_without_crashing(
    public_client, db_session, auth_headers
):
    system, *_rest = _make_chain(db_session)
    record = _reset_record(db_session)
    record.first_ai_system_id = system.id
    record.first_integration_id = uuid.uuid4()
    db_session.commit()
    body = _get(public_client, auth_headers)
    assert body["summary"]["connection"]["state"] == "UNSCOPED"
    record.first_ai_system_id = uuid.uuid4()
    db_session.commit()
    assert _get(public_client, auth_headers)["summary"]["connection"]["state"] == (
        "UNSCOPED"
    )


def test_matching_decision_is_governed_decision_observed_only(
    public_client, db_session, auth_headers
):
    system, _a, _v, runtime, integration = _make_chain(db_session)
    _add_decision(db_session, integration, runtime)
    _select(db_session, system, integration)
    body = _get(public_client, auth_headers)
    steps = _steps(body)
    assert steps["first_governed_action"]["completion_source"] == "OBSERVATION"
    assert steps["production_readiness"]["done"] is False
    assert steps["test_connection"]["done"] is False
    assert body["summary"]["verification"]["state"] == "NOT_RUN"


def test_first_policies_needs_a_rule_in_selected_system(
    public_client, db_session, auth_headers
):
    system, *_ = _make_chain(db_session)
    other, *_ = _make_chain(db_session)
    _select(db_session, system)

    framework = ControlFramework(
        framework_key=f"fw-{uuid.uuid4().hex[:8]}", name="FW", created_by="tester"
    )
    db_session.add(framework)
    db_session.flush()
    objective = ControlObjective(
        framework_id=framework.id, code="OBJ-1", title="O", category="c"
    )
    db_session.add(objective)
    db_session.flush()
    requirement = ControlRequirement(
        objective_id=objective.id, requirement_key="REQ-1", statement="s"
    )
    db_session.add(requirement)
    db_session.flush()
    db_session.add(
        ControlAssessment(ai_system_id=system.id, control_requirement_id=requirement.id)
    )
    db_session.commit()
    # A ControlAssessment alone is not policy configuration.
    assert _steps(_get(public_client, auth_headers))["first_policies"]["done"] is False

    other_collection = PolicyCollection(ai_system_id=other.id)
    db_session.add(other_collection)
    db_session.flush()
    db_session.add(
        ContentRule(
            policy_collection_id=other_collection.id,
            title="Other system rule",
            content="x",
            effect="DENY",
        )
    )
    db_session.commit()
    assert _steps(_get(public_client, auth_headers))["first_policies"]["done"] is False

    collection = PolicyCollection(ai_system_id=system.id)
    db_session.add(collection)
    db_session.flush()
    db_session.add(
        ContentRule(
            policy_collection_id=collection.id,
            title="Archived",
            content="x",
            effect="DENY",
            archived_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()
    assert _steps(_get(public_client, auth_headers))["first_policies"]["done"] is False

    # A disabled but unarchived rule is a recorded configuration object.
    db_session.add(
        ContentRule(
            policy_collection_id=collection.id,
            title="Disabled draft",
            content="x",
            effect="DENY",
            enabled=False,
        )
    )
    db_session.commit()
    step = _steps(_get(public_client, auth_headers))["first_policies"]
    assert step["done"] is True
    assert step["completion_source"] == "RECORD"


def test_manual_configuration_marks_are_labelled_manual(
    public_client, db_session, auth_headers
):
    system, *_ = _make_chain(db_session)
    _select(db_session, system)
    response = public_client.patch(
        "/v1/productization/onboarding",
        json={"complete_step": "first_policies"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    step = _steps(response.json())["first_policies"]
    assert step["done"] is True
    assert step["completion_source"] == "MANUAL"


def test_omitted_ids_preserve_and_explicit_null_clears(
    public_client, db_session, auth_headers
):
    system, _a, _v, _r, integration = _make_chain(db_session)
    trajectory_id = uuid.uuid4()
    record = _select(db_session, system, integration)
    record.first_trajectory_id = trajectory_id
    db_session.commit()

    omitted = public_client.patch(
        "/v1/productization/onboarding",
        json={"current_step": "team_roles"},
        headers=auth_headers,
    ).json()["onboarding"]
    assert omitted["first_ai_system_id"] == str(system.id)
    assert omitted["first_integration_id"] == str(integration.id)
    assert omitted["first_trajectory_id"] == str(trajectory_id)

    cleared = public_client.patch(
        "/v1/productization/onboarding",
        json={"first_integration_id": None, "first_trajectory_id": None},
        headers=auth_headers,
    ).json()["onboarding"]
    assert cleared["first_ai_system_id"] == str(system.id)
    assert cleared["first_integration_id"] is None
    assert cleared["first_trajectory_id"] is None


def test_system_change_and_dependent_clear_are_applied_together(
    public_client, db_session, auth_headers
):
    system_a, _a, _v, _r, integration_a = _make_chain(
        db_session, heartbeat=datetime.now(timezone.utc)
    )
    system_b, *_ = _make_chain(db_session)
    _select(db_session, system_a, integration_a)
    response = public_client.patch(
        "/v1/productization/onboarding",
        json={
            "first_ai_system_id": str(system_b.id),
            "first_integration_id": None,
            "first_trajectory_id": None,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["onboarding"]["first_ai_system_id"] == str(system_b.id)
    assert body["onboarding"]["first_integration_id"] is None
    assert body["summary"]["connection"]["state"] == "UNSCOPED"
    assert _steps(body)["connect_application"]["done"] is False


def test_anonymous_rejected_and_read_only_cannot_patch(public_client, db_session):
    assert public_client.get("/v1/productization/onboarding").status_code in {401, 403}
    assert public_client.patch(
        "/v1/productization/onboarding", json={"complete_step": "team_roles"}
    ).status_code in {401, 403}

    _ensure_default_rbac_seed(db_session)
    suffix = uuid.uuid4().hex[:8]
    email = f"reader-{suffix}@example.com"
    user_id = f"reader-{suffix}"
    db_session.add(
        UserAccount(
            user_id=user_id,
            email=email,
            name="Reader",
            status="active",
            email_verified_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    db_session.add(
        AdminCredential(
            email=email,
            user_id=user_id,
            password_hash=hash_password("SecurePass123!"),
            is_active=True,
        )
    )
    db_session.add(
        OrganizationMembership(
            user_id=user_id,
            role=GovernanceRole.AI_SYSTEM_OWNER.value,
            status=MembershipStatus.ACTIVE.value,
            created_by=user_id,
        )
    )
    db_session.commit()
    login = public_client.post(
        "/v1/auth/login", json={"email": email, "password": "SecurePass123!"}
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    read = public_client.get("/v1/productization/onboarding", headers=headers)
    assert read.status_code == 200, read.text
    denied = public_client.patch(
        "/v1/productization/onboarding",
        json={"complete_step": "team_roles"},
        headers=headers,
    )
    assert denied.status_code in {401, 403}, denied.text
