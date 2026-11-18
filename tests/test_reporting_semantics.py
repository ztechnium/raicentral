"""Reporting semantics, filter contract, assurance, budgets and snapshot RBAC.

Fixtures are small and exact so every asserted number can be derived by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from rai_gateway.core.authorization import require_tenant
from rai_gateway.core.principal import Principal
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.finops.domain import CostState, MeteringSource
from rai_gateway.main import app
from rai_gateway.models.evaluations import AssuranceRecord, DeploymentGateEvaluation
from rai_gateway.models.identity import AgentRuntimeIdentity
from rai_gateway.models.metering import RuntimeMeteringSpan
from rai_gateway.models.registry import Agent, AgentVersion, AISystem
from rai_gateway.models.risk_intelligence import (
    RuntimeBudget,
    RuntimeBudgetWindow,
    RuntimeRiskFinding,
)
from rai_gateway.models.runtime_enforcement import RuntimeEnforcementReceipt
from rai_gateway.reporting import queries, reports
from rai_gateway.reporting.schemas import ReportFilters


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _system(db) -> AISystem:
    suffix = uuid4().hex[:8]
    system = AISystem(
        system_key=f"sem-{suffix}", name=f"Semantics {suffix}", created_by="tester"
    )
    db.add(system)
    db.flush()
    return system


def _agent_version(db, system: AISystem):
    agent = Agent(
        agent_key=f"agent-{uuid4().hex[:8]}",
        ai_system_id=system.id,
        name="Agent",
        created_by="tester",
    )
    db.add(agent)
    db.flush()
    version = AgentVersion(agent_id=agent.id, version_number=1, created_by="tester")
    db.add(version)
    db.flush()
    return agent, version


def _finding(db, system, *, severity="HIGH", status="OPEN", created_at=None):
    row = RuntimeRiskFinding(
        ai_system_id=system.id,
        finding_type="CONTENT_POLICY_VIOLATION",
        title=f"{severity} {status}",
        severity=severity,
        status=status,
        category="PROMPT_INJECTION",
        created_at=created_at or _now(),
    )
    db.add(row)
    db.flush()
    return row


def _scoped(system) -> ReportFilters:
    return ReportFilters(ai_system_id=system.id)


# --------------------------------------------------------------- current vs period


def test_open_is_current_state_but_new_and_resolved_are_period_events(db_session):
    system = _system(db_session)
    now = _now()
    # Old finding, still open: counts as open, NOT as new in the last 30 days.
    _finding(db_session, system, status="OPEN", created_at=now - timedelta(days=90))
    # New in period and still open.
    _finding(db_session, system, status="OPEN", created_at=now - timedelta(days=2))
    # New in period and since closed -> resolved (period cohort).
    _finding(db_session, system, status="RESOLVED", created_at=now - timedelta(days=3))
    # Old and closed: in neither the open nor the period counts.
    _finding(db_session, system, status="RESOLVED", created_at=now - timedelta(days=80))

    report = reports.build_risk_report(
        db_session,
        ReportFilters(
            ai_system_id=system.id,
            period_start=now - timedelta(days=30),
            period_end=now,
        ),
    )

    assert report.open_findings == 2  # the 90-day-old one is included
    assert report.new_findings == 2  # only the two created inside the period
    assert report.resolved_findings == 1  # cohort: created in period, now closed
    assert report.resolved_findings <= report.new_findings
    assert report.mean_time_to_resolution_hours is None  # no resolved_at column
    assert report.findings_by_source["runtime"] == 2
    assert "tool" in report.unavailable_dimensions
    assert report.category_distribution == {"PROMPT_INJECTION": 2}


def test_critical_and_high_counts_follow_open_statuses(db_session):
    system = _system(db_session)
    _finding(db_session, system, severity="CRITICAL", status="INVESTIGATING")
    _finding(db_session, system, severity="HIGH", status="ACKNOWLEDGED")
    _finding(db_session, system, severity="CRITICAL", status="RESOLVED")

    report = reports.build_risk_report(db_session, _scoped(system))

    assert report.critical_findings == 1
    assert report.high_findings == 1
    assert report.open_findings == 2


def test_summary_open_exposure_ignores_period_and_has_no_previous(db_session):
    system = _system(db_session)
    now = _now()
    _finding(
        db_session, system, severity="CRITICAL", created_at=now - timedelta(days=200)
    )

    summary = reports.build_report_summary(
        db_session,
        ReportFilters(
            ai_system_id=system.id,
            period_start=now - timedelta(days=7),
            period_end=now,
        ),
    )

    assert summary.current_risk_exposure.value == 1
    assert summary.current_risk_exposure.previous_value is None
    assert summary.open_critical_findings.value == 1
    assert summary.new_findings is not None and summary.new_findings.value == 0


# --------------------------------------------------------------- assurance


def _assurance(db, system, version, status, *, valid_until=None, superseded=None):
    row = AssuranceRecord(
        ai_system_id=system.id,
        agent_version_id=version.id,
        environment="PRODUCTION",
        status=status,
        valid_until=valid_until,
        superseded_at=superseded,
        created_by="tester",
    )
    db.add(row)
    db.flush()
    return row


def test_assurance_uses_distinct_systems_effective_status_and_denominator(db_session):
    now = _now()
    valid_sys, lapsed_sys, failed_sys, unassessed_sys = (
        _system(db_session) for _ in range(4)
    )
    _, v_valid = _agent_version(db_session, valid_sys)
    _, v_lapsed = _agent_version(db_session, lapsed_sys)
    _, v_failed = _agent_version(db_session, failed_sys)

    _assurance(
        db_session, valid_sys, v_valid, "VALID", valid_until=now + timedelta(days=30)
    )
    # Superseded history must not be counted a second time.
    _assurance(
        db_session, valid_sys, v_valid, "FAILED", superseded=now - timedelta(days=5)
    )
    # Stored VALID but past valid_until: effectively EXPIRED, never "valid".
    _assurance(
        db_session, lapsed_sys, v_lapsed, "VALID", valid_until=now - timedelta(days=1)
    )
    _assurance(db_session, failed_sys, v_failed, "FAILED")

    effective = queries.get_effective_assurance_by_system(db_session, ReportFilters())
    mine = {
        valid_sys.id: "VALID",
        lapsed_sys.id: "EXPIRED",
        failed_sys.id: "FAILED",
    }
    assert {k: v for k, v in effective.items() if k in mine} == mine
    assert unassessed_sys.id not in effective

    one = reports.build_assurance_report(db_session, _scoped(lapsed_sys))
    assert one.total_systems == 1
    assert one.expired_assurance == 1
    assert one.valid_assurance == 0
    assert one.assurance_coverage_pct == 0.0
    assert one.has_assurance_data is True

    none = reports.build_assurance_report(db_session, _scoped(unassessed_sys))
    assert none.unassessed_systems == 1
    assert none.has_assurance_data is False
    assert none.evaluation_pass_rate is None  # no gate evaluations: unknown, not 0%
    assert none.assurance_coverage_pct == 0.0  # denominator exists, nothing valid


def test_gate_pass_rate_is_null_without_evaluations_and_period_scoped(db_session):
    now = _now()
    system = _system(db_session)
    _, version = _agent_version(db_session, system)
    for decision, when in (
        ("PASS", now - timedelta(days=1)),
        ("FAIL", now - timedelta(days=2)),
        ("FAIL", now - timedelta(days=90)),  # outside the 30-day period
    ):
        db_session.add(
            DeploymentGateEvaluation(
                ai_system_id=system.id,
                agent_version_id=version.id,
                environment="PRODUCTION",
                decision=decision,
                evaluated_by="tester",
                evaluated_at=when,
            )
        )
    db_session.flush()

    report = reports.build_assurance_report(
        db_session,
        ReportFilters(
            ai_system_id=system.id,
            period_start=now - timedelta(days=30),
            period_end=now,
        ),
    )
    assert report.gate_evaluations == 2
    assert report.failed_deployment_gates == 1
    assert report.evaluation_pass_rate == 50.0


# --------------------------------------------------------------- budgets


def test_budget_status_uses_current_windows_and_one_row_per_budget(db_session):
    now = _now()
    system = _system(db_session)
    budget = RuntimeBudget(
        ai_system_id=system.id,
        name="Monthly cost",
        metric="COST",
        limit_value=100.0,
        enabled=True,
    )
    db_session.add(budget)
    db_session.flush()
    # An old EXCEEDED window must not make today's budget look exceeded.
    db_session.add(
        RuntimeBudgetWindow(
            budget_id=budget.id,
            ai_system_id=system.id,
            scope_type="AI_SYSTEM",
            scope_id="a",
            window_start=now - timedelta(days=60),
            window_end=now - timedelta(days=30),
            usage_value=500.0,
            status="EXCEEDED",
        )
    )
    # Two active windows (two scopes): reported once, worst case.
    for scope, usage in (("b", 20.0), ("c", 85.0)):
        db_session.add(
            RuntimeBudgetWindow(
                budget_id=budget.id,
                ai_system_id=system.id,
                scope_type="AI_SYSTEM",
                scope_id=scope,
                window_start=now - timedelta(days=1),
                window_end=now + timedelta(days=29),
                usage_value=usage,
                status="WARNING" if usage >= 80 else "OK",
            )
        )
    db_session.flush()

    rows = queries.get_budget_utilization(db_session, _scoped(system))
    assert len(rows) == 1
    assert rows[0]["utilization_pct"] == 85.0
    assert queries.count_budgets_by_status(db_session, _scoped(system), "EXCEEDED") == 0
    assert queries.count_budgets_by_status(db_session, _scoped(system), "WARNING") == 1

    summary = reports.build_report_summary(db_session, _scoped(system))
    assert summary.budget_utilization_pct is not None
    assert summary.budget_utilization_pct.value == 85.0


def test_non_cost_budgets_are_never_averaged_into_money(db_session):
    now = _now()
    system = _system(db_session)
    budget = RuntimeBudget(
        ai_system_id=system.id,
        name="Tokens",
        metric="TOKEN_USAGE",
        limit_value=1000.0,
        enabled=True,
    )
    db_session.add(budget)
    db_session.flush()
    db_session.add(
        RuntimeBudgetWindow(
            budget_id=budget.id,
            ai_system_id=system.id,
            scope_type="AI_SYSTEM",
            scope_id="a",
            window_start=now - timedelta(hours=1),
            window_end=now + timedelta(hours=1),
            usage_value=900.0,
            status="WARNING",
        )
    )
    db_session.flush()

    summary = reports.build_report_summary(db_session, _scoped(system))
    assert summary.budget_utilization_pct is None  # no comparable (COST) budget


# --------------------------------------------------------------- cost semantics


def test_unavailable_cost_contributes_nothing_to_reports_total(db_session):
    now = _now()
    system = _system(db_session)
    db_session.add_all(
        [
            RuntimeMeteringSpan(
                ai_system_id=system.id,
                span_type="MODEL_CALL",
                cost_state=CostState.METERED.value,
                metered_cost=3.0,
                total_tokens=10,
                currency="USD",
                metering_source=MeteringSource.PROVIDER_RESPONSE.value,
                started_at=now,
            ),
            # Unknown cost state with a stray estimate: must NOT be added in.
            RuntimeMeteringSpan(
                ai_system_id=system.id,
                span_type="MODEL_CALL",
                cost_state=CostState.UNAVAILABLE.value,
                estimated_cost=99.0,
                total_tokens=5,
                currency="USD",
                metering_source=MeteringSource.SDK_REPORTED.value,
                started_at=now,
            ),
        ]
    )
    db_session.flush()

    total = queries.get_total_cost(
        db_session,
        ReportFilters(
            ai_system_id=system.id,
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=1),
        ),
    )
    assert total == 3.0


# --------------------------------------------------------------- runtime / policy


def _receipt(db, system, agent, version, *, effect, decision, execution, started):
    identity = AgentRuntimeIdentity(
        identity_key=f"id-{uuid4().hex[:8]}",
        agent_id=agent.id,
        agent_version_id=version.id,
        name="identity",
        created_by="tester",
    )
    db.add(identity)
    db.flush()
    row = RuntimeEnforcementReceipt(
        runtime_identity_id=identity.id,
        ai_system_id=system.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        integration_type="SDK",
        action_digest=uuid4().hex,
        policy_effect=effect,
        enforcement_decision=decision,
        execution_status=execution,
        started_at=started,
    )
    db.add(row)
    db.flush()
    return row


def test_decision_effect_and_execution_outcome_are_reported_separately(db_session):
    now = _now()
    system = _system(db_session)
    agent, version = _agent_version(db_session, system)
    _receipt(
        db_session,
        system,
        agent,
        version,
        effect="ALLOW",
        decision="ALLOW",
        execution="SUCCEEDED",
        started=now,
    )
    _receipt(
        db_session,
        system,
        agent,
        version,
        effect="DENY",
        decision="DENY",
        execution="NOT_ATTEMPTED",
        started=now,
    )
    # A matched MONITOR rule is not a block: decision ALLOW, execution failed.
    _receipt(
        db_session,
        system,
        agent,
        version,
        effect="MONITOR",
        decision="ALLOW",
        execution="FAILED",
        started=now,
    )

    report = reports.build_policy_report(
        db_session,
        ReportFilters(
            ai_system_id=system.id,
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=1),
        ),
    )
    assert report.evaluations == 3
    assert report.effects.monitor == 1 and report.effects.deny == 1
    assert report.decision_distribution == {"ALLOW": 2, "DENY": 1}
    assert report.execution_outcome_distribution["NOT_ATTEMPTED"] == 1
    assert report.execution_outcome_distribution["FAILED"] == 1
    # A DENY decision is an enforcement outcome, not a failed execution.
    assert report.deny_rate == pytest.approx(100 / 3, abs=0.01)

    governed = queries.count_systems_with_governed_activity(
        db_session,
        ReportFilters(
            ai_system_id=system.id,
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=1),
        ),
    )
    assert governed == 1  # distinct systems, not receipts


def test_effect_filter_applies_to_every_policy_figure(db_session):
    now = _now()
    system = _system(db_session)
    agent, version = _agent_version(db_session, system)
    _receipt(
        db_session,
        system,
        agent,
        version,
        effect="ALLOW",
        decision="ALLOW",
        execution="SUCCEEDED",
        started=now,
    )
    _receipt(
        db_session,
        system,
        agent,
        version,
        effect="DENY",
        decision="DENY",
        execution="NOT_ATTEMPTED",
        started=now,
    )

    report = reports.build_policy_report(
        db_session,
        ReportFilters(
            ai_system_id=system.id,
            effect="deny",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=1),
        ),
    )
    assert report.evaluations == 1
    assert report.triggers == 1
    assert report.effects.allow == 0 and report.effects.deny == 1
    assert report.decision_distribution == {"DENY": 1}


# --------------------------------------------------------------- filters & time


def test_filters_normalize_to_naive_utc_and_reject_inverted_period():
    from datetime import timezone

    plus_two = timezone(timedelta(hours=2))
    f = ReportFilters(
        period_start=datetime(2026, 1, 1, 12, 0, tzinfo=plus_two),
        period_end=datetime(2026, 1, 2, 12, 0, tzinfo=plus_two),
    )
    assert f.period_start == datetime(2026, 1, 1, 10, 0)
    assert f.period_start.tzinfo is None

    with pytest.raises(ValueError):
        ReportFilters(
            period_start=datetime(2026, 2, 1), period_end=datetime(2026, 1, 1)
        )


def test_boundary_row_is_counted_in_exactly_one_period(db_session):
    system = _system(db_session)
    boundary = datetime(2026, 3, 10, 0, 0, 0)
    _finding(db_session, system, created_at=boundary)
    start, end = boundary, boundary + timedelta(days=10)

    from rai_gateway.reporting.filters import get_previous_period

    prev_start, prev_end = get_previous_period(start, end)
    current = ReportFilters(ai_system_id=system.id, period_start=start, period_end=end)
    previous = ReportFilters(
        ai_system_id=system.id, period_start=prev_start, period_end=prev_end
    )
    assert queries.count_risk_findings(db_session, current) == 1
    assert queries.count_risk_findings(db_session, previous) == 0


# --------------------------------------------------------------- API: RBAC & contract


@pytest.fixture()
def reports_client(test_client, db_session):
    from rai_gateway.models.database import UserAccount

    # Mutating permissions require a verified email; model a real verified user.
    user = db_session.get(UserAccount, "u1")
    if user is None:
        user = UserAccount(user_id="u1", email="u1@example.test", name="U One")
        db_session.add(user)
    user.email_verified_at = _now()
    db_session.flush()

    holder = {"permissions": frozenset({"reports:read"}), "user": user}

    def fake_tenant():
        return TenantContext(
            principal=Principal(
                user_id="u1",
                email="u1@example.test",
                session_id=None,
                platform_roles=(),
            ),
            membership_ids=(),
            roles=(),
            permissions=holder["permissions"],
        )

    app.dependency_overrides[require_tenant] = fake_tenant
    try:
        yield test_client, holder
    finally:
        app.dependency_overrides.pop(require_tenant, None)


@pytest.mark.parametrize(
    "path,bad",
    [
        ("/v1/reports/summary", "agent_id"),  # accepted by the old API, never applied
        ("/v1/reports/summary", "risk_severity"),
        ("/v1/reports/runtime", "effect"),
        ("/v1/reports/policies", "risk_severity"),
        ("/v1/reports/risk", "effect"),
        ("/v1/reports/assurance", "budget_metric"),
    ],
)
def test_unsupported_filters_are_rejected_not_ignored(reports_client, path, bad):
    client, _ = reports_client
    response = client.get(path, params={bad: str(uuid4())})
    assert response.status_code == 422
    assert bad in response.json()["detail"]["unsupported"]


def test_supported_filters_and_period_are_accepted(reports_client):
    client, _ = reports_client
    now = datetime.now(UTC)
    response = client.get(
        "/v1/reports/risk",
        params={
            "period_start": (now - timedelta(days=7)).isoformat(),
            "period_end": now.isoformat(),
            "risk_severity": ["CRITICAL", "HIGH"],
            "finding_status": "OPEN",
        },
    )
    assert response.status_code == 200
    assert response.json()["open_findings"] >= 0


def test_inverted_or_malformed_period_is_a_client_error(reports_client):
    client, _ = reports_client
    now = datetime.now(UTC)
    inverted = client.get(
        "/v1/reports/summary",
        params={
            "period_start": now.isoformat(),
            "period_end": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert inverted.status_code == 422
    malformed = client.get("/v1/reports/summary", params={"period_start": "yesterday"})
    assert malformed.status_code == 422


def test_report_endpoints_require_reports_read(reports_client):
    client, holder = reports_client
    holder["permissions"] = frozenset({"finops:read"})
    for path in (
        "/v1/reports/summary",
        "/v1/reports/risk",
        "/v1/reports/policies",
        "/v1/reports/budgets",
        "/v1/reports/runtime",
        "/v1/reports/assurance",
        "/v1/reports/snapshots",
    ):
        assert client.get(path).status_code == 403, path


def test_snapshot_create_needs_snapshot_permission_but_read_does_not(
    reports_client, db_session
):
    client, holder = reports_client
    system = _system(db_session)
    _finding(db_session, system)
    body = {
        "report_type": "RISK_REPORT",
        "filters": {"ai_system_id": str(system.id)},
    }

    # Auditor-style read-only caller: cannot write a snapshot.
    denied = client.post("/v1/reports/snapshots", json=body)
    assert denied.status_code == 403

    holder["permissions"] = frozenset({"reports:read", "reports:snapshot"})
    created = client.post("/v1/reports/snapshots", json=body)
    assert created.status_code == 201, created.text
    snapshot = created.json()

    # Effective period and filters are stored, not "now/now".
    assert snapshot["schema_version"] == "1.0"
    assert snapshot["period_start"] < snapshot["period_end"]
    assert snapshot["filters_json"]["ai_system_id"] == str(system.id)
    assert "period_start" in snapshot["filters_json"]
    assert snapshot["generated_by"] == "u1"
    assert len(snapshot["content_digest"]) == 64

    # A read-only caller can list and read, and integrity is verified.
    holder["permissions"] = frozenset({"reports:read"})
    listed = client.get("/v1/reports/snapshots")
    assert listed.status_code == 200
    assert any(item["id"] == snapshot["id"] for item in listed.json())
    fetched = client.get(f"/v1/reports/snapshots/{snapshot['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["integrity_verified"] is True
    assert fetched.json()["data_snapshot"]["open_findings"] == 1


def test_tampered_snapshot_fails_integrity(reports_client, db_session):
    client, holder = reports_client
    holder["permissions"] = frozenset({"reports:read", "reports:snapshot"})
    created = client.post(
        "/v1/reports/snapshots",
        json={"report_type": "RUNTIME_REPORT", "filters": {}},
    )
    assert created.status_code == 201
    snapshot_id = created.json()["id"]

    from rai_gateway.models.reporting import ReportSnapshot as Row

    row = db_session.query(Row).filter(Row.id == snapshot_id).one()
    row.data_snapshot = {**row.data_snapshot, "governed_actions": 999999}
    db_session.flush()

    fetched = client.get(f"/v1/reports/snapshots/{snapshot_id}").json()
    assert fetched["integrity_verified"] is False


def test_unknown_snapshot_and_report_type_are_rejected(reports_client):
    client, holder = reports_client
    holder["permissions"] = frozenset({"reports:read", "reports:snapshot"})
    assert client.get(f"/v1/reports/snapshots/{uuid4()}").status_code == 404
    bad = client.post(
        "/v1/reports/snapshots", json={"report_type": "NOPE", "filters": {}}
    )
    assert bad.status_code == 400


def test_organization_admin_role_gets_snapshot_permission_and_auditor_does_not():
    from rai_gateway.services.admin_console import (
        DEFAULT_RBAC_ROLES as SYSTEM_ROLES,  # noqa: PLC0415
    )

    assert "reports:snapshot" in SYSTEM_ROLES["organization_admin"]["permissions"]
    assert "reports:snapshot" not in SYSTEM_ROLES["auditor"]["permissions"]
    assert "reports:read" in SYSTEM_ROLES["auditor"]["permissions"]


def test_unverified_email_cannot_create_snapshot_even_with_permission(reports_client):
    client, holder = reports_client
    holder["user"].email_verified_at = None
    holder["permissions"] = frozenset({"reports:read", "reports:snapshot"})
    response = client.post(
        "/v1/reports/snapshots", json={"report_type": "RUNTIME_REPORT", "filters": {}}
    )
    assert response.status_code == 403
    # Reading never requires a verified email.
    assert client.get("/v1/reports/snapshots").status_code == 200


def test_attention_comes_from_portfolio_queue_without_invented_fields(db_session):
    system = _system(db_session)
    _finding(db_session, system, severity="CRITICAL", status="OPEN")

    summary = reports.build_report_summary(db_session, _scoped(system))

    items = [i for i in summary.attention if i.ai_system_id == system.id]
    assert items, "open critical finding must surface in the attention queue"
    item = items[0]
    assert item.severity == "CRITICAL"
    assert item.source_type == "RUNTIME_RISK_FINDING"
    assert item.drill_down and item.drill_down.startswith("/risk/findings")
    # Owner/due are passed through only when the source supplies them.
    assert item.due_at is None
    # Scoping to another system must not leak this item.
    other = reports.build_report_summary(db_session, _scoped(_system(db_session)))
    assert all(i.ai_system_id != system.id for i in other.attention)
