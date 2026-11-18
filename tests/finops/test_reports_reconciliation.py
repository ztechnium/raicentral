"""Reports financial figures must reconcile with FinOps, or expose why they differ."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from rai_gateway.finops.reporting import FinOpsFilters, FinOpsReportingService
from rai_gateway.models.finops import UsageSettlement
from rai_gateway.models.metering import RuntimeMeteringSpan
from rai_gateway.models.registry import AISystem
from rai_gateway.reporting import queries, reports
from rai_gateway.reporting.schemas import ReportFilters


def _system(db) -> AISystem:
    suffix = uuid4().hex[:8]
    system = AISystem(system_key=f"rec-{suffix}", name=f"Rec {suffix}", created_by="t")
    db.add(system)
    db.flush()
    return system


def _span(db, system, *, state, cost=None, currency="USD", **kw):
    fields = {
        "ai_system_id": system.id,
        "span_type": "MODEL_CALL",
        "cost_state": state,
        "currency": currency,
        "metering_source": "PROVIDER_RESPONSE",
        "started_at": datetime.now(UTC) - timedelta(hours=1),
        "total_tokens": 10,
    }
    fields.update(kw)
    if state == "BILLED":
        fields["billed_cost"] = cost
    elif state == "METERED":
        fields["metered_cost"] = cost
    elif state == "ESTIMATED":
        fields["estimated_cost"] = cost
    db.add(RuntimeMeteringSpan(**fields))
    db.flush()


def _both_filters(system):
    now = datetime.now(UTC)
    start, end = now - timedelta(days=2), now + timedelta(days=1)
    return (
        FinOpsFilters(period_start=start, period_end=end, ai_system_id=system.id),
        ReportFilters(
            ai_system_id=system.id,
            period_start=start.replace(tzinfo=None),
            period_end=end.replace(tzinfo=None),
        ),
    )


def test_reports_span_cost_equals_finops_total_for_matching_filters(db_session):
    system = _system(db_session)
    _span(db_session, system, state="BILLED", cost=1.5)
    _span(db_session, system, state="METERED", cost=0.25)
    _span(db_session, system, state="ESTIMATED", cost=0.125)
    _span(
        db_session, system, state="UNAVAILABLE", estimated_cost=99.0
    )  # must not count
    finops_filters, report_filters = _both_filters(system)

    finops_total = FinOpsReportingService(db_session).dashboard(finops_filters)[
        "totals"
    ]["total_cost"]
    summary = queries.get_cost_summary(db_session, report_filters)

    assert finops_total == 1.875
    assert summary["span_cost"] == finops_total
    assert summary["ledger_only_cost"] is None
    assert summary["total_cost"] == finops_total


def test_ledger_only_settlements_are_the_explained_difference(db_session):
    system = _system(db_session)
    _span(db_session, system, state="METERED", cost=2.0)
    linked_span_id = uuid4()
    db_session.add_all(
        [
            # Standalone settlement no span represents: counted by Reports only.
            UsageSettlement(
                ai_system_id=system.id,
                cost_state="METERED",
                metered_cost=0.5,
                currency="USD",
                metering_source="PROVIDER_RESPONSE",
            ),
            # Settlement already represented by a span: must not be double counted.
            UsageSettlement(
                ai_system_id=system.id,
                metering_span_id=linked_span_id,
                cost_state="METERED",
                metered_cost=2.0,
                currency="USD",
                metering_source="PROVIDER_RESPONSE",
            ),
        ]
    )
    db_session.flush()
    finops_filters, report_filters = _both_filters(system)

    finops_total = FinOpsReportingService(db_session).dashboard(finops_filters)[
        "totals"
    ]["total_cost"]
    summary = queries.get_cost_summary(db_session, report_filters)

    assert finops_total == 2.0
    assert summary["span_cost"] == 2.0
    assert summary["ledger_only_cost"] == 0.5
    assert summary["total_cost"] == 2.5
    assert summary["total_cost"] - summary["span_cost"] == summary["ledger_only_cost"]


def test_reports_never_sum_across_currencies(db_session):
    system = _system(db_session)
    _span(db_session, system, state="METERED", cost=1.0, currency="USD")
    _span(db_session, system, state="METERED", cost=5.0, currency="EUR")
    _, report_filters = _both_filters(system)
    summary = queries.get_cost_summary(db_session, report_filters)
    assert summary["currency"] == "MIXED"
    assert summary["currencies"] == ["EUR", "USD"]
    assert summary["total_cost"] is None
    assert summary["span_cost"] is None


def test_reports_report_unknown_cost_as_none_not_zero(db_session):
    system = _system(db_session)
    _, report_filters = _both_filters(system)
    assert queries.get_cost_summary(db_session, report_filters)["total_cost"] is None

    _span(db_session, system, state="UNAVAILABLE")
    summary = queries.get_cost_summary(db_session, report_filters)
    assert summary["total_cost"] is None  # usage exists but nothing is priced
    assert summary["currency"] == "USD"


def test_a_valid_zero_is_zero_not_unknown(db_session):
    system = _system(db_session)
    _span(db_session, system, state="BILLED", cost=0.0)
    _, report_filters = _both_filters(system)
    assert queries.get_cost_summary(db_session, report_filters)["total_cost"] == 0.0


def test_budget_report_exposes_the_decomposition_and_keeps_sub_cent_precision(
    db_session,
):
    system = _system(db_session)
    _span(db_session, system, state="METERED", cost=0.0000465)
    _, report_filters = _both_filters(system)
    report = reports.build_budget_report(db_session, report_filters)
    assert report.span_cost == 0.0000465  # not rounded to 0.00
    assert report.total_cost == 0.0000465
    assert report.ledger_only_cost is None
    assert report.currency == "USD"
    assert "same definition" in report.cost_basis
    assert "not summed" in report.cost_basis


def test_budget_report_total_is_null_for_mixed_currencies(db_session):
    system = _system(db_session)
    _span(db_session, system, state="METERED", cost=1.0, currency="USD")
    _span(db_session, system, state="METERED", cost=1.0, currency="GBP")
    _, report_filters = _both_filters(system)
    report = reports.build_budget_report(db_session, report_filters)
    assert report.total_cost is None
    assert report.currency == "MIXED"
