"""FinOps reporting: cost-state correctness, reconciliation, isolation, budgets."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rai_gateway.core.authorization import require_tenant
from rai_gateway.core.principal import Principal
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.finops.budget_health import budget_health, project_window
from rai_gateway.finops.reporting import FinOpsFilters, FinOpsReportingService
from rai_gateway.main import app
from rai_gateway.models.metering import RuntimeMeteringSpan
from rai_gateway.risk_intelligence.budgets import BudgetService
from rai_gateway.risk_intelligence.domain import BudgetEffect, BudgetMetric

NOW = datetime.now(timezone.utc)


def _filters(**kw) -> FinOpsFilters:
    return FinOpsFilters(
        period_start=kw.pop("period_start", NOW - timedelta(days=7)),
        period_end=kw.pop("period_end", NOW + timedelta(hours=1)),
        **kw,
    )


def _span(db, *, state="METERED", cost=None, currency="USD", **kw):
    fields = {
        "span_type": "MODEL_CALL",
        "provider": "openai",
        "model": "gpt-x",
        "currency": currency,
        "cost_state": state,
        "started_at": NOW - timedelta(hours=1),
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "trajectory_id": uuid.uuid4(),
    }
    fields.update(kw)
    if state == "BILLED":
        fields["billed_cost"] = cost
        fields["actual_cost"] = cost
    elif state == "METERED":
        fields["metered_cost"] = cost
        fields["actual_cost"] = cost
    elif state == "ESTIMATED":
        fields["estimated_cost"] = cost
    row = RuntimeMeteringSpan(**fields)
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- cost state


def test_valid_zero_billed_cost_is_billed_not_unavailable(db_session):
    _span(db_session, state="BILLED", cost=0.0)
    totals = FinOpsReportingService(db_session).dashboard(_filters())["totals"]
    assert totals["cost_state"] == "BILLED"
    assert totals["billed_cost"] == 0.0
    assert totals["total_cost"] == 0.0
    assert totals["metered_cost"] is None  # no metered spans: unknown, not 0
    assert totals["estimated_cost"] is None


def test_no_spans_is_no_data_not_zero(db_session):
    totals = FinOpsReportingService(db_session).dashboard(_filters())["totals"]
    assert totals["cost_state"] == "NO_DATA"
    assert totals["total_cost"] is None
    assert totals["total_tokens"] is None
    assert totals["cost_complete"] is False


def test_unpriced_spans_are_unavailable_and_never_zero(db_session):
    _span(db_session, state="UNAVAILABLE", cost=None)
    totals = FinOpsReportingService(db_session).dashboard(_filters())["totals"]
    assert totals["cost_state"] == "UNAVAILABLE"
    assert totals["total_cost"] is None
    assert totals["unavailable_cost_count"] == 1


def test_billed_metered_estimated_stay_separate_and_total_is_their_sum(db_session):
    _span(db_session, state="BILLED", cost=1.0)
    _span(db_session, state="METERED", cost=0.5)
    _span(db_session, state="ESTIMATED", cost=0.25)
    _span(db_session, state="UNAVAILABLE")
    totals = FinOpsReportingService(db_session).dashboard(_filters())["totals"]
    assert totals["billed_cost"] == 1.0
    assert totals["metered_cost"] == 0.5
    assert totals["estimated_cost"] == 0.25
    assert totals["actual_cost"] == 1.5  # billed + metered, never estimated
    assert totals["total_cost"] == 1.75
    assert totals["cost_state"] == "MIXED"
    assert totals["cost_complete"] is False  # one span has no price
    assert totals["span_counts"] == {
        "total": 4,
        "billed": 1,
        "metered": 1,
        "estimated": 1,
        "unavailable": 1,
    }


def test_average_uses_known_cost_trajectories_as_denominator(db_session):
    priced, unpriced = uuid.uuid4(), uuid.uuid4()
    _span(db_session, state="METERED", cost=2.0, trajectory_id=priced)
    _span(db_session, state="UNAVAILABLE", trajectory_id=unpriced)
    totals = FinOpsReportingService(db_session).dashboard(_filters())["totals"]
    assert totals["trajectories"] == 2
    assert totals["known_cost_trajectories"] == 1
    # 2.0 / 1 priced trajectory, not 2.0 / 2 all trajectories.
    assert totals["avg_cost_per_trajectory"] == 2.0


# ----------------------------------------------------------------- currency


def test_mixed_currencies_are_never_summed(db_session):
    _span(db_session, state="METERED", cost=1.0, currency="USD")
    _span(db_session, state="METERED", cost=3.0, currency="EUR")
    svc = FinOpsReportingService(db_session)
    totals = svc.dashboard(_filters())["totals"]
    assert totals["currency"] == "MIXED"
    assert totals["total_cost"] is None
    assert {c["currency"]: c["total_cost"] for c in totals["by_currency"]} == {
        "EUR": 3.0,
        "USD": 1.0,
    }
    breakdown = svc.breakdown(_filters(), dimension="model")
    assert breakdown["currency"] == "MIXED"
    assert all(item["cost"] is None for item in breakdown["items"])
    assert svc.timeseries(_filters())["total_cost"] is None


def test_currency_filter_makes_totals_summable(db_session):
    _span(db_session, state="METERED", cost=1.0, currency="USD")
    _span(db_session, state="METERED", cost=3.0, currency="EUR")
    svc = FinOpsReportingService(db_session)
    totals = svc.dashboard(_filters(currency="EUR"))["totals"]
    assert totals["currency"] == "EUR"
    assert totals["total_cost"] == 3.0


# ------------------------------------------------------------ reconciliation


def test_breakdown_timeseries_and_dashboard_totals_reconcile(db_session):
    for i in range(5):
        _span(
            db_session,
            state="METERED" if i % 2 else "ESTIMATED",
            cost=0.1 * (i + 1),
            model=f"model-{i % 3}",
            started_at=NOW - timedelta(days=i),
        )
    _span(db_session, state="UNAVAILABLE", model="model-0")
    svc = FinOpsReportingService(db_session)
    f = _filters()
    dash = svc.dashboard(f)["totals"]["total_cost"]
    series = svc.timeseries(f)["total_cost"]
    groups = svc.breakdown(f, dimension="model", limit=200)["items"]
    grouped = round(sum(i["cost"] for i in groups if i["cost"] is not None), 8)
    assert dash == series == grouped


def test_unattributed_rows_are_visible_with_their_own_label(db_session):
    _span(db_session, state="METERED", cost=1.0, ai_system_id=None)
    _span(db_session, state="UNAVAILABLE", provider=None, model=None)
    svc = FinOpsReportingService(db_session)
    systems = svc.breakdown(_filters(), dimension="ai_system")["items"]
    assert [i["label"] for i in systems] == ["Unattributed"]
    assert systems[0]["calls"] == 2
    assert systems[0]["unpriced_calls"] == 1
    assert systems[0]["unpriced_share_pct"] == 50.0
    models = svc.breakdown(_filters(), dimension="model")["items"]
    assert any(i["label"] == "Not reported" for i in models)


def test_breakdown_pagination_sort_and_total(db_session):
    for i in range(5):
        _span(db_session, state="METERED", cost=float(i + 1), model=f"m{i}")
    svc = FinOpsReportingService(db_session)
    page1 = svc.breakdown(_filters(), dimension="model", limit=2, offset=0)
    page2 = svc.breakdown(_filters(), dimension="model", limit=2, offset=2)
    assert page1["total"] == page2["total"] == 5
    assert [i["label"] for i in page1["items"]] == ["m4", "m3"]
    assert [i["label"] for i in page2["items"]] == ["m2", "m1"]
    asc = svc.breakdown(_filters(), dimension="model", descending=False, limit=1)
    assert asc["items"][0]["label"] == "m0"


def test_search_is_escaped_and_limited_to_string_dimensions(db_session):
    _span(db_session, state="METERED", cost=1.0, model="gpt_4")
    _span(db_session, state="METERED", cost=1.0, model="gptx4")
    svc = FinOpsReportingService(db_session)
    hits = svc.breakdown(_filters(), dimension="model", search="gpt_4")["items"]
    assert [i["label"] for i in hits] == ["gpt_4"]  # "_" is literal, not a wildcard
    with pytest.raises(ValueError):
        svc.breakdown(_filters(), dimension="ai_system", search="x")
    with pytest.raises(ValueError):
        svc.breakdown(_filters(), dimension="model", sort="bogus")


def test_tokens_are_null_when_no_span_reported_them(db_session):
    _span(
        db_session,
        state="METERED",
        cost=1.0,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
    )
    item = FinOpsReportingService(db_session).breakdown(
        _filters(), dimension="model"
    )["items"][0]
    assert item["tokens"] is None
    assert item["token_reported_calls"] == 0


# ----------------------------------------------------- delegation isolation


def test_delegation_filter_isolates_tenants_across_every_view(db_session):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    _span(db_session, state="METERED", cost=1.0, delegation_id=tenant_a)
    _span(db_session, state="METERED", cost=10.0, delegation_id=tenant_b)
    svc = FinOpsReportingService(db_session)
    f = _filters(delegation_id=tenant_a)
    assert svc.dashboard(f)["totals"]["total_cost"] == 1.0
    assert svc.timeseries(f)["total_cost"] == 1.0
    items = svc.breakdown(f, dimension="delegation")["items"]
    assert [i["key"] for i in items] == [str(tenant_a)]
    assert svc.trajectories(f)["total"] == 1
    assert svc.trajectories(f)["items"][0]["cost"] == 1.0


# ------------------------------------------------------------- budget health


def _budget(db, stack, **kw):
    params = {
        "ai_system_id": stack["system"].id,
        "name": "daily",
        "scope_type": "RUNTIME_IDENTITY",
        "metric": BudgetMetric.ACTION_COUNT.value,
        "tool_name": "create_order",
        "limit_value": 4,
        "window_seconds": 86400,
        "warning_threshold": 0.5,
        "effect_on_exceed": BudgetEffect.DENY.value,
        "enabled": True,
    }
    params.update(kw)
    svc = BudgetService(db)
    budget = svc.create_budget(**params)
    db.commit()
    return svc, budget


def test_budget_health_reports_current_window_headroom_and_enforcement(
    db_session, runtime_stack
):
    svc, budget = _budget(db_session, runtime_stack)
    for _ in range(3):
        svc.check_and_consume(
            ai_system_id=runtime_stack["system"].id,
            runtime_identity_id=runtime_stack["identity"].id,
            tool_name="create_order",
        )
    db_session.commit()
    health = budget_health(db_session)
    (item,) = [i for i in health["items"] if i["budget_id"] == str(budget.id)]
    assert item["status"] == "WARNING"
    assert item["window"]["usage"] == 3
    assert item["window"]["headroom"] == 1
    assert item["blocking"] is True
    assert item["metric"] == "ACTION_COUNT" and item["unit"] == "actions"
    assert item["evidence"]["checks"] >= 3
    assert "DENY" in item["enforcement_summary"]
    assert health["summary"]["warning"] == 1


def test_historical_windows_do_not_count_as_budget_pressure(
    db_session, runtime_stack
):
    from rai_gateway.models.risk_intelligence import RuntimeBudgetWindow

    svc, budget = _budget(db_session, runtime_stack)
    db_session.add(
        RuntimeBudgetWindow(
            budget_id=budget.id,
            ai_system_id=runtime_stack["system"].id,
            scope_type="RUNTIME_IDENTITY",
            scope_id=str(runtime_stack["identity"].id),
            window_start=NOW - timedelta(days=3),
            window_end=NOW - timedelta(days=2),
            usage_value=99,
            status="EXCEEDED",
        )
    )
    db_session.commit()
    health = budget_health(db_session)
    assert health["summary"]["exceeded"] == 0
    (item,) = [i for i in health["items"] if i["budget_id"] == str(budget.id)]
    assert item["status"] == "NO_ACTIVE_WINDOW"  # not assumed healthy either
    assert item["window"] is None


def test_monitor_only_budget_is_not_reported_as_enforced(db_session, runtime_stack):
    _, budget = _budget(
        db_session, runtime_stack, effect_on_exceed=BudgetEffect.MONITOR.value
    )
    (item,) = [
        i
        for i in budget_health(db_session)["items"]
        if i["budget_id"] == str(budget.id)
    ]
    assert item["blocking"] is False
    assert "Monitor only" in item["enforcement_summary"]


def test_budget_without_checks_says_enforcement_is_unproven(db_session, runtime_stack):
    _, budget = _budget(db_session, runtime_stack)
    (item,) = [
        i
        for i in budget_health(db_session)["items"]
        if i["budget_id"] == str(budget.id)
    ]
    assert item["evidence"]["checks"] == 0
    assert "no enforcement check" in item["enforcement_summary"]


def test_budgets_of_different_metrics_are_not_combined(db_session, runtime_stack):
    _budget(db_session, runtime_stack, name="actions")
    _budget(
        db_session,
        runtime_stack,
        name="tokens",
        metric=BudgetMetric.TOKEN_USAGE.value,
        tool_name=None,
    )
    items = budget_health(db_session)["items"]
    assert {i["unit"] for i in items} == {"actions", "tokens"}
    assert "total_usage" not in budget_health(db_session)["summary"]


# ----------------------------------------------------------- projection maths

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_END = _START + timedelta(hours=24)


def test_projection_requires_enough_elapsed_window():
    out = project_window(
        usage=5, limit=10, window_start=_START, window_end=_END,
        now=_START + timedelta(minutes=5),
    )
    assert out["status"] == "insufficient_data"
    assert out["projected_window_usage"] is None


def test_projection_without_usage_is_insufficient_not_zero():
    out = project_window(
        usage=0, limit=10, window_start=_START, window_end=_END,
        now=_START + timedelta(hours=12),
    )
    assert out["status"] == "insufficient_data"


def test_projection_flags_exhaustion_before_reset():
    out = project_window(
        usage=8, limit=10, window_start=_START, window_end=_END,
        now=_START + timedelta(hours=12),
    )
    assert out["status"] == "projected"
    assert out["projected_window_usage"] == pytest.approx(16)
    assert out["will_exceed_before_reset"] is True
    # 8 per 12h -> limit of 10 reached at 15h, inside the window.
    assert out["projected_exhaustion_at"].startswith("2026-01-01T15:00")
    assert out["confidence"] == "high"


def test_projection_under_limit_has_no_exhaustion_and_never_crosses_reset():
    out = project_window(
        usage=2, limit=10, window_start=_START, window_end=_END,
        now=_START + timedelta(hours=12),
    )
    assert out["will_exceed_before_reset"] is False
    assert out["projected_exhaustion_at"] is None
    assert out["projected_window_usage"] == pytest.approx(4)  # window total only


def test_projection_for_invalid_limit_is_insufficient():
    out = project_window(
        usage=2, limit=0, window_start=_START, window_end=_END,
        now=_START + timedelta(hours=12),
    )
    assert out["status"] == "insufficient_data"


# ------------------------------------------------------------------- API


@pytest.fixture()
def finops_client(test_client):
    holder = {"permissions": frozenset({"finops:read"})}

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


def test_api_rejects_inverted_oversized_and_unknown_filters(finops_client):
    client, _ = finops_client
    now = datetime.now(timezone.utc)
    inverted = client.get(
        "/v1/finops/dashboard",
        params={
            "period_start": now.isoformat(),
            "period_end": (now - timedelta(days=1)).isoformat(),
        },
    )
    assert inverted.status_code == 400
    too_long = client.get(
        "/v1/finops/breakdown",
        params={
            "period_start": (now - timedelta(days=900)).isoformat(),
            "period_end": now.isoformat(),
        },
    )
    assert too_long.status_code == 400
    assert (
        client.get("/v1/finops/dashboard", params={"cost_state": "NOPE"}).status_code
        == 400
    )
    assert (
        client.get("/v1/finops/breakdown", params={"dimension": "nope"}).status_code
        == 400
    )
    assert (
        client.get("/v1/finops/breakdown", params={"agent_id": "not-a-uuid"}).status_code
        == 422
    )


def test_api_requires_finops_read_permission(finops_client):
    client, holder = finops_client
    holder["permissions"] = frozenset({"evidence:read"})
    for path in ("dashboard", "timeseries", "breakdown", "budgets", "trajectories"):
        response = client.get(f"/v1/finops/{path}")
        assert response.status_code in (401, 403), path


def test_api_accepts_every_supported_filter_on_every_read_view(db_session, finops_client):
    client, _ = finops_client
    _span(db_session, state="METERED", cost=1.0, model="gpt-x")
    db_session.commit()
    params = {
        "provider": "openai",
        "model": "gpt-x",
        "environment": "prod",
        "metering_source": "UNKNOWN",
        "cost_state": "metered",
        "currency": "usd",
    }
    for path in ("dashboard", "timeseries", "breakdown", "trajectories", "models"):
        assert client.get(f"/v1/finops/{path}", params=params).status_code == 200, path


def test_api_export_requires_permission_and_allowlists_views(finops_client):
    client, holder = finops_client
    assert client.get("/v1/finops/export", params={"view": "summary"}).status_code == 200
    assert client.get("/v1/finops/export", params={"view": "spans"}).status_code == 403
    assert client.get("/v1/finops/export", params={"view": "../x"}).status_code == 400
    holder["permissions"] = frozenset({"finops:read", "finops:export"})
    assert client.get("/v1/finops/export", params={"view": "models"}).status_code == 200


def test_csv_export_neutralises_formula_injection(db_session, finops_client):
    client, holder = finops_client
    holder["permissions"] = frozenset({"finops:read", "finops:export"})
    _span(db_session, state="METERED", cost=1.0, model="=HYPERLINK(\"http://x\")")
    db_session.commit()
    response = client.get(
        "/v1/finops/export", params={"view": "models", "format": "csv"}
    )
    assert response.status_code == 200
    assert "'=HYPERLINK" in response.text
    assert ",=HYPERLINK" not in response.text
