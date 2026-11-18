"""Server-side FinOps aggregates. Spans are the display source.

Accounting rules enforced here (see docs/finops-metric-dictionary.md):

* Cost state comes from per-state *row counts*, never from the truthiness of an
  amount. A valid $0.00 billed span is BILLED; no spans is NO_DATA; spans with
  no price are UNAVAILABLE.
* Money is never summed across currencies. When the filtered spans carry more
  than one currency and no ``currency`` filter is applied, cost fields are
  ``None`` and ``currencies`` lists what is present.
* Averages divide by the denominator named next to them, never by all spans.
* Reservations, settlements and receipts are not summed here: a settled
  request is represented by exactly one metering span.

This edition is single-organization: there is no ``organization_id`` on these
tables, so isolation is by RBAC (``finops:read``) and by the delegation /
AI-system filters, not by an org predicate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import case, cast, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from rai_gateway.finops import cost_expr
from rai_gateway.finops.budget_health import budget_health
from rai_gateway.models.evidence import EvidenceFinding, RuntimeTrajectory
from rai_gateway.models.finops import UsageReservation
from rai_gateway.models.identity import DelegationGrant
from rai_gateway.models.metering import RuntimeMeteringSpan
from rai_gateway.models.registry import Agent, AISystem
from rai_gateway.models.runtime_enforcement import RuntimeEnforcementReceipt

_INTERVENTION = ("DENY", "REQUIRE_APPROVAL", "SUSPEND")
_HIGH_RISK = ("HIGH", "CRITICAL")
_EXPORT_CAP = 5000
MAX_PERIOD_DAYS = 400

_M = RuntimeMeteringSpan

# Dimensions whose key is a plain string column (searchable server-side).
STRING_DIMENSIONS = frozenset({"provider", "model", "tool", "environment"})
VALID_COST_STATES = frozenset(
    {"BILLED", "METERED", "ACTUAL", "ESTIMATED", "UNAVAILABLE", "MIXED"}
)
SORT_KEYS = frozenset({"cost", "calls", "tokens", "trajectories"})
DIMENSIONS = (
    "ai_system",
    "agent",
    "agent_version",
    "runtime_identity",
    "delegation",
    "provider",
    "model",
    "tool",
    "environment",
    "integration",
)

DEFINITIONS = {
    "known_cost": (
        "Billed + metered + estimated amount over spans whose cost state carries a "
        "real amount. Spans with UNAVAILABLE cost contribute nothing and are counted "
        "separately; they are never treated as zero."
    ),
    "estimated_cost": (
        "Cost derived from a pre-execution estimate. It is not confirmed incurred "
        "spend and is excluded from billed and metered figures."
    ),
    "risk_associated_spend": (
        "Known cost of trajectories that have a HIGH or CRITICAL finding, or an "
        "enforcement decision of DENY, REQUIRE_APPROVAL, or SUSPEND. It is correlated "
        "spend, not monetary loss caused by risk."
    ),
    "prevented_estimated_spend": (
        "Estimated reservation cost for requests denied before execution, plus "
        "reservations released because a budget denied them. This is a counterfactual "
        "estimate: it is not realized savings and does not mean any invoice was reduced."
    ),
}


@dataclass
class FinOpsFilters:
    period_start: datetime
    period_end: datetime
    ai_system_id: UUID | None = None
    agent_id: UUID | None = None
    agent_version_id: UUID | None = None
    runtime_identity_id: UUID | None = None
    delegation_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    environment: str | None = None
    integration_id: UUID | None = None
    cost_state: str | None = None
    metering_source: str | None = None
    currency: str | None = None


def default_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    end = now or datetime.now(timezone.utc)
    return end - timedelta(days=30), end


class FinOpsReportingService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------ dashboard
    def dashboard(self, filters: FinOpsFilters) -> dict[str, Any]:
        scope = self.currency_scope(filters)
        previous_filters = self._previous(filters)
        current = self._totals(filters, scope)
        previous = self._totals(previous_filters, self.currency_scope(previous_filters))
        risk = self._risk_spend(filters, scope)
        prevented = self._prevented(filters)
        pressure = budget_health(self._db, ai_system_id=filters.ai_system_id)["summary"]
        return {
            "period": _period(filters),
            "previous_period": _period(previous_filters),
            "totals": current,
            "previous_totals": {
                "total_cost": previous["total_cost"],
                "cost_state": previous["cost_state"],
                "currency": previous["currency"],
                "total_tokens": previous["total_tokens"],
            },
            "comparison": {
                "total_cost_change_pct": _change(
                    current["total_cost"], previous["total_cost"]
                ),
                "total_tokens_change_pct": _change(
                    current["total_tokens"], previous["total_tokens"]
                ),
                # Cost growth is a neutral fact, not a defect. The comparison is
                # only like-for-like when both periods were priced to a similar degree.
                "like_for_like": _like_for_like(current, previous),
                "basis": (
                    "Known cost in this period vs the immediately preceding period of "
                    "equal length, same filters."
                ),
            },
            "coverage": self._coverage(filters),
            "budget_pressure": {
                "healthy": pressure["healthy"],
                "warning": pressure["warning"],
                "exceeded": pressure["exceeded"],
                "unavailable": pressure["unavailable"],
                "no_active_window": pressure["no_active_window"],
                "total": pressure["total"],
                "budget_warning_count": pressure["warning"],
                "budget_exceeded_count": pressure["exceeded"],
            },
            "risk_associated_spend": risk["risk_associated_spend"],
            "risk_breakdown": risk["breakdown"],
            "prevented_estimated_spend": prevented["amount"],
            "prevented_estimated_currency": prevented["currency"],
            "definition": DEFINITIONS,
        }

    # ----------------------------------------------------------------- timeseries
    def timeseries(self, filters: FinOpsFilters) -> dict[str, Any]:
        scope = self.currency_scope(filters)
        bucket = func.date_trunc("day", _M.started_at)
        rows = (
            self._spans(filters)
            .with_entities(
                bucket.label("bucket"),
                *self._cost_entities(),
                func.sum(_M.total_tokens),
                func.count(func.distinct(_M.trajectory_id)),
                func.sum(case((_M.span_type == "MODEL_CALL", 1), else_=0)),
            )
            .group_by(bucket)
            .order_by(bucket)
            .all()
        )
        points = []
        known_total: float | None = None
        for row in rows:
            block = _cost_block(row[1:8], scope["mixed"])
            if block["cost"] is not None:
                known_total = (known_total or 0.0) + block["cost"]
            points.append(
                {
                    "timestamp": row[0].isoformat() if row[0] else None,
                    "cost": block["cost"],
                    "billed_cost": block["billed_cost"],
                    "metered_cost": block["metered_cost"],
                    "estimated_cost": block["estimated_cost"],
                    "cost_state": block["cost_state"],
                    "priced_spans": block["priced_spans"],
                    "unavailable_spans": block["unavailable_spans"],
                    "tokens": _opt_int(row[8]),
                    "trajectories": int(row[9] or 0),
                    "model_calls": int(row[10] or 0),
                }
            )
        return {
            "period": _period(filters),
            "bucket": "day",
            "currency": scope["currency"],
            "currencies": scope["currencies"],
            "total_cost": round(known_total, 8) if known_total is not None else None,
            "points": points,
        }

    # ------------------------------------------------------------------ breakdown
    def breakdown(
        self,
        filters: FinOpsFilters,
        *,
        dimension: str,
        limit: int = 20,
        offset: int = 0,
        sort: str = "cost",
        descending: bool = True,
        search: str | None = None,
    ) -> dict[str, Any]:
        column = _dimension_column(dimension)
        if column is None:
            raise ValueError(f"Unsupported breakdown dimension: {dimension}")
        if sort not in SORT_KEYS:
            raise ValueError(f"Unsupported sort: {sort}")
        scope = self.currency_scope(filters)
        query = self._spans(filters)
        if search:
            if dimension not in STRING_DIMENSIONS:
                raise ValueError(
                    f"Search is only supported for {', '.join(sorted(STRING_DIMENSIONS))}"
                )
            escaped = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query = query.filter(column.ilike(f"%{escaped}%", escape="\\"))

        order_expr = {
            "cost": func.sum(cost_expr.known_cost(_M)),
            "calls": func.count(_M.id),
            "tokens": func.sum(_M.total_tokens),
            "trajectories": func.count(func.distinct(_M.trajectory_id)),
        }[sort]
        order = (
            order_expr.desc().nullslast() if descending else order_expr.asc().nullslast()
        )

        rows = (
            query.with_entities(
                column,
                *self._cost_entities(),
                func.count(func.distinct(_M.trajectory_id)),
                func.count(
                    func.distinct(
                        case((_M.cost_state.in_(cost_expr.KNOWN_STATES), _M.trajectory_id))
                    )
                ),
                func.sum(case((_M.span_type == "MODEL_CALL", 1), else_=0)),
                func.sum(case((_M.span_type == "TOOL_CALL", 1), else_=0)),
                func.sum(_M.total_tokens),
                func.sum(_M.input_tokens),
                func.sum(_M.output_tokens),
                func.sum(_M.cached_input_tokens),
                func.sum(_M.reasoning_tokens),
                func.count(_M.total_tokens),
            )
            .group_by(column)
            .order_by(order, func.count(_M.id).desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        total_groups = query.with_entities(column).group_by(column).count()

        items = []
        for row in rows:
            key = row[0]
            block = _cost_block(row[1:8], scope["mixed"])
            calls = int(row[7] or 0)
            known_trajectories = int(row[9] or 0)
            priced = block["priced_spans"]
            cost = block["cost"]
            items.append(
                {
                    "key": str(key) if key is not None else None,
                    "label": self._label(dimension, key),
                    "cost": cost,
                    "billed_cost": block["billed_cost"],
                    "metered_cost": block["metered_cost"],
                    "estimated_cost": block["estimated_cost"],
                    "cost_state": block["cost_state"],
                    "currency": scope["currency"],
                    "calls": calls,
                    "model_calls": int(row[10] or 0),
                    "tool_calls": int(row[11] or 0),
                    "trajectories": int(row[8] or 0),
                    "priced_calls": priced,
                    "unpriced_calls": block["unavailable_spans"],
                    "unpriced_share_pct": round(
                        block["unavailable_spans"] / calls * 100, 1
                    )
                    if calls
                    else None,
                    "tokens": _opt_int(row[12]),
                    "input_tokens": _opt_int(row[13]),
                    "output_tokens": _opt_int(row[14]),
                    "cached_tokens": _opt_int(row[15]),
                    "reasoning_tokens": _opt_int(row[16]),
                    "token_reported_calls": int(row[17] or 0),
                    # Denominator is priced calls only (see avg_cost_denominator).
                    "avg_cost_per_call": round(cost / priced, 8)
                    if cost is not None and priced
                    else None,
                    "avg_cost_per_trajectory": round(cost / known_trajectories, 8)
                    if cost is not None and known_trajectories
                    else None,
                    "avg_cost_denominator": {
                        "per_call": "calls with a known cost",
                        "per_trajectory": "trajectories with at least one known-cost call",
                    },
                }
            )
        return {
            "dimension": dimension,
            "items": items,
            "total": total_groups,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "order": "desc" if descending else "asc",
            "currency": scope["currency"],
            "currencies": scope["currencies"],
        }

    # --------------------------------------------------------------- trajectories
    def trajectories(
        self, filters: FinOpsFilters, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        cost = cost_expr.known_cost(_M)
        grouped = (
            self._spans(filters)
            .filter(_M.trajectory_id.isnot(None))
            .with_entities(
                _M.trajectory_id,
                func.sum(cost).label("cost"),
                func.sum(_M.total_tokens).label("tokens"),
                func.max(_M.started_at).label("started_at"),
            )
            .group_by(_M.trajectory_id)
            .order_by(func.sum(cost).desc().nullslast())
        )
        total = grouped.count()
        rows = grouped.offset(offset).limit(limit).all()
        return {
            "items": [
                {
                    "trajectory_id": str(row.trajectory_id),
                    "cost": round(float(row.cost), 8) if row.cost is not None else None,
                    "tokens": _opt_int(row.tokens),
                    "started_at": row.started_at.isoformat()
                    if row.started_at
                    else None,
                }
                for row in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def export_rows(self, filters: FinOpsFilters, view: str) -> list[dict[str, Any]]:
        if view == "summary":
            return [self.dashboard(filters)["totals"]]
        if view == "trajectories":
            return self.trajectories(filters, limit=_EXPORT_CAP)["items"]
        if view == "spans":
            spans = self._spans(filters).limit(_EXPORT_CAP).all()
            from rai_gateway.finops.metering import RuntimeMeteringService

            svc = RuntimeMeteringService(self._db)
            return [svc.serialize_span(span) for span in spans]
        dimension = {
            "systems": "ai_system",
            "models": "model",
            "tenants": "delegation",
        }.get(view, view)
        return self.breakdown(filters, dimension=dimension, limit=_EXPORT_CAP)["items"]

    # ------------------------------------------------------------------ internals
    def _spans(self, filters: FinOpsFilters):
        query = self._db.query(_M).filter(
            _M.started_at >= filters.period_start,
            _M.started_at < filters.period_end,
        )
        mapping = {
            "ai_system_id": _M.ai_system_id,
            "agent_id": _M.agent_id,
            "agent_version_id": _M.agent_version_id,
            "runtime_identity_id": _M.runtime_identity_id,
            "delegation_id": _M.delegation_id,
            "provider": _M.provider,
            "model": _M.model,
            "environment": _M.environment,
            "integration_id": _M.integration_id,
            "cost_state": _M.cost_state,
            "metering_source": _M.metering_source,
            "currency": _M.currency,
        }
        for name, column in mapping.items():
            value = getattr(filters, name)
            if value:
                query = query.filter(column == value)
        return query

    def _known_cost(self):
        return cost_expr.known_cost(_M)

    def _cost_entities(self) -> list[Any]:
        """Seven aggregate columns consumed by ``_cost_block``."""
        return [
            func.sum(cost_expr.billed_cost(_M)),
            func.sum(cost_expr.metered_cost(_M)),
            func.sum(cost_expr.estimated_cost(_M)),
            cost_expr.state_count(_M, cost_expr.BILLED_STATES),
            cost_expr.state_count(_M, cost_expr.METERED_STATES),
            cost_expr.state_count(_M, cost_expr.ESTIMATED_STATES),
            func.count(_M.id),
        ]

    def currency_scope(self, filters: FinOpsFilters) -> dict[str, Any]:
        """Which currencies the filtered spans carry, and whether summing is safe."""
        base = replace(filters, currency=None)
        present = sorted(
            {
                row[0]
                for row in self._spans(base).with_entities(_M.currency).distinct().all()
                if row[0]
            }
        )
        if filters.currency:
            return {
                "currency": filters.currency,
                "currencies": present,
                "mixed": False,
            }
        if len(present) > 1:
            return {"currency": "MIXED", "currencies": present, "mixed": True}
        return {
            "currency": present[0] if present else None,
            "currencies": present,
            "mixed": False,
        }

    def _totals(self, filters: FinOpsFilters, scope: dict[str, Any]) -> dict[str, Any]:
        known_state = _M.cost_state.in_(cost_expr.KNOWN_STATES)
        row = (
            self._spans(filters)
            .with_entities(
                *self._cost_entities(),
                func.sum(_M.total_tokens),
                func.sum(_M.input_tokens),
                func.sum(_M.output_tokens),
                func.sum(_M.cached_input_tokens),
                func.sum(_M.reasoning_tokens),
                func.sum(case((_M.span_type == "MODEL_CALL", 1), else_=0)),
                func.sum(case((_M.span_type == "TOOL_CALL", 1), else_=0)),
                func.count(func.distinct(_M.trajectory_id)),
                func.count(func.distinct(_M.runtime_conversation_id)),
                func.count(func.distinct(case((known_state, _M.trajectory_id)))),
                func.count(
                    func.distinct(case((known_state, _M.runtime_conversation_id)))
                ),
                func.count(_M.total_tokens),
            )
            .one()
        )
        block = _cost_block(row[0:7], scope["mixed"])
        trajectories, conversations = int(row[14] or 0), int(row[15] or 0)
        known_trajectories, known_conversations = int(row[16] or 0), int(row[17] or 0)
        cost = block["cost"]
        settled = [
            v for v in (block["billed_cost"], block["metered_cost"]) if v is not None
        ]
        return {
            "total_cost": cost,
            "billed_cost": block["billed_cost"],
            "metered_cost": block["metered_cost"],
            # "actual" = billed + metered (legacy name). None when neither exists.
            "actual_cost": round(sum(settled), 8) if settled else None,
            "estimated_cost": block["estimated_cost"],
            "unavailable_cost_count": block["unavailable_spans"],
            "span_counts": {
                "total": block["total_spans"],
                "billed": block["billed_spans"],
                "metered": block["metered_spans"],
                "estimated": block["estimated_spans"],
                "unavailable": block["unavailable_spans"],
            },
            # False when some spans have no price: total_cost is then a floor.
            "cost_complete": block["total_spans"] > 0
            and block["unavailable_spans"] == 0,
            "currency": scope["currency"],
            "currencies": scope["currencies"],
            "by_currency": self._by_currency(filters) if scope["mixed"] else None,
            "cost_state": block["cost_state"],
            "total_tokens": _opt_int(row[7]),
            "input_tokens": _opt_int(row[8]),
            "output_tokens": _opt_int(row[9]),
            "cached_tokens": _opt_int(row[10]),
            "reasoning_tokens": _opt_int(row[11]),
            "token_reported_spans": int(row[18] or 0),
            "model_calls": int(row[12] or 0),
            "tool_calls": int(row[13] or 0),
            "trajectories": trajectories,
            "conversations": conversations,
            "known_cost_trajectories": known_trajectories,
            "known_cost_conversations": known_conversations,
            # Denominator = trajectories/conversations that have a known cost, so
            # unpriced activity cannot silently dilute the average.
            "avg_cost_per_trajectory": round(cost / known_trajectories, 8)
            if cost is not None and known_trajectories
            else None,
            "avg_cost_per_conversation": round(cost / known_conversations, 8)
            if cost is not None and known_conversations
            else None,
            "avg_cost_denominator": "trajectories_and_conversations_with_known_cost",
            "avg_tokens_per_trajectory": round(int(row[7]) / trajectories, 2)
            if row[7] is not None and trajectories
            else None,
        }

    def _by_currency(self, filters: FinOpsFilters) -> list[dict[str, Any]]:
        rows = (
            self._spans(filters)
            .with_entities(_M.currency, *self._cost_entities())
            .group_by(_M.currency)
            .order_by(_M.currency)
            .all()
        )
        out = []
        for row in rows:
            block = _cost_block(row[1:8], False)
            out.append(
                {
                    "currency": row[0],
                    "total_cost": block["cost"],
                    "billed_cost": block["billed_cost"],
                    "metered_cost": block["metered_cost"],
                    "estimated_cost": block["estimated_cost"],
                    "cost_state": block["cost_state"],
                    "spans": block["total_spans"],
                }
            )
        return out

    def _coverage(self, filters: FinOpsFilters) -> dict[str, Any]:
        """Measurement coverage over spans. This is NOT governance control coverage."""
        row = (
            self._spans(filters)
            .with_entities(
                *self._cost_entities()[3:],
                func.count(_M.total_tokens),
                func.count(_M.model),
                func.count(_M.delegation_id),
                func.count(_M.ai_system_id),
            )
            .one()
        )
        billed, metered, estimated, total = (int(row[i] or 0) for i in range(4))
        known = billed + metered + estimated
        unavailable = total - known

        def pct(part: int) -> float | None:
            return round(part / total * 100, 1) if total else None

        return {
            "span_count": total,
            "cost_coverage_pct": pct(known),
            "billed_pct": pct(billed),
            "metered_pct": pct(metered),
            "actual_pct": pct(billed + metered),
            "estimated_pct": pct(estimated),
            "unavailable_pct": pct(unavailable),
            "token_coverage_pct": pct(int(row[4] or 0)),
            "model_attribution_pct": pct(int(row[5] or 0)),
            # Delegation (SaaS tenant) attribution; unrelated to cost coverage.
            "tenant_attribution_pct": pct(int(row[6] or 0)),
            # Usage not tied to any registered AI system.
            "system_attribution_pct": pct(int(row[7] or 0)),
            "unattributed_pct": pct(total - int(row[7] or 0)),
        }

    def _risk_spend(
        self, filters: FinOpsFilters, scope: dict[str, Any]
    ) -> dict[str, Any]:
        finding_ids = self._db.query(EvidenceFinding.trajectory_id).filter(
            EvidenceFinding.severity.in_(_HIGH_RISK),
            EvidenceFinding.trajectory_id.isnot(None),
        )
        denied_ids = (
            self._db.query(RuntimeTrajectory.id)
            .join(
                RuntimeEnforcementReceipt,
                RuntimeEnforcementReceipt.correlation_id
                == RuntimeTrajectory.correlation_id,
            )
            .filter(RuntimeEnforcementReceipt.enforcement_decision.in_(_INTERVENTION))
        )
        risk_ids = finding_ids.union(denied_ids)
        amount = None
        if not scope["mixed"]:
            amount = (
                self._spans(filters)
                .filter(_M.trajectory_id.in_(risk_ids))
                .with_entities(func.sum(cost_expr.known_cost(_M)))
                .scalar()
            )
        value = round(float(amount), 8) if amount is not None else None
        return {
            "risk_associated_spend": value,
            "breakdown": {"spend_with_findings_or_intervention": value},
        }

    def _prevented(self, filters: FinOpsFilters) -> dict[str, Any]:
        """Counterfactual only. Honors the same system/provider/model filters."""

        def scoped(query):
            for name, column in (
                ("ai_system_id", UsageReservation.ai_system_id),
                ("agent_id", UsageReservation.agent_id),
                ("agent_version_id", UsageReservation.agent_version_id),
                ("runtime_identity_id", UsageReservation.runtime_identity_id),
                ("delegation_id", UsageReservation.delegation_id),
                ("provider", UsageReservation.provider),
                ("model", UsageReservation.model),
                ("currency", UsageReservation.currency),
            ):
                value = getattr(filters, name)
                if value:
                    query = query.filter(column == value)
            return query.filter(
                UsageReservation.created_at >= filters.period_start,
                UsageReservation.created_at < filters.period_end,
            )

        denied = scoped(
            self._db.query(
                UsageReservation.currency, func.sum(UsageReservation.estimated_cost)
            ).join(
                RuntimeEnforcementReceipt,
                RuntimeEnforcementReceipt.id == UsageReservation.enforcement_receipt_id,
            )
        ).filter(
            RuntimeEnforcementReceipt.enforcement_decision.in_(_INTERVENTION),
            RuntimeEnforcementReceipt.execution_attempted.is_(False),
        )
        released = scoped(
            self._db.query(
                UsageReservation.currency, func.sum(UsageReservation.estimated_cost)
            )
        ).filter(
            UsageReservation.status == "RELEASED",
            cast(UsageReservation.allocation, JSONB).contains(
                {"release_reason": "budget_denied"}
            ),
        )
        totals: dict[str, float] = {}
        for query in (denied, released):
            for currency, amount in query.group_by(UsageReservation.currency).all():
                code = currency or "USD"
                totals[code] = totals.get(code, 0.0) + float(amount or 0)
        if len(totals) > 1:
            return {"amount": None, "currency": "MIXED"}
        if not totals:
            return {"amount": 0.0, "currency": filters.currency}
        currency, amount = next(iter(totals.items()))
        return {"amount": round(amount, 8), "currency": currency}

    def _label(self, dimension: str, key: Any) -> str | None:
        if key is None:
            return "Not reported" if dimension in STRING_DIMENSIONS else "Unattributed"
        if dimension == "ai_system":
            row = self._db.query(AISystem).filter(AISystem.id == key).first()
            return row.name if row else str(key)
        if dimension == "agent":
            row = self._db.query(Agent).filter(Agent.id == key).first()
            return row.name if row else str(key)
        if dimension == "delegation":
            row = (
                self._db.query(DelegationGrant)
                .filter(DelegationGrant.id == key)
                .first()
            )
            if row and isinstance(row.scope, dict):
                label = row.scope.get("tenant_label") or row.scope.get("name")
                if isinstance(label, str) and label.strip():
                    return label
            return str(key)
        return str(key)

    def _previous(self, filters: FinOpsFilters) -> FinOpsFilters:
        delta = filters.period_end - filters.period_start
        return replace(
            filters,
            period_start=filters.period_start - delta,
            period_end=filters.period_start,
        )


def _cost_block(values: Any, mixed: bool) -> dict[str, Any]:
    """Turn the seven ``_cost_entities`` columns into a state-aware cost dict."""
    billed_sum, metered_sum, estimated_sum, billed_n, metered_n, estimated_n, total = (
        values
    )
    billed_n, metered_n, estimated_n, total = (
        int(billed_n or 0),
        int(metered_n or 0),
        int(estimated_n or 0),
        int(total or 0),
    )
    known = billed_n + metered_n + estimated_n
    unavailable = total - known
    state = cost_expr.derive_cost_state(
        billed=billed_n,
        metered=metered_n,
        estimated=estimated_n,
        unavailable=unavailable,
        total=total,
    )

    def part(amount: Any, count: int) -> float | None:
        if mixed or count == 0:
            return None
        return round(float(amount or 0.0), 8)

    billed, metered, estimated = (
        part(billed_sum, billed_n),
        part(metered_sum, metered_n),
        part(estimated_sum, estimated_n),
    )
    parts = [p for p in (billed, metered, estimated) if p is not None]
    return {
        "cost": round(sum(parts), 8) if parts else None,
        "billed_cost": billed,
        "metered_cost": metered,
        "estimated_cost": estimated,
        "cost_state": state,
        "priced_spans": known,
        "billed_spans": billed_n,
        "metered_spans": metered_n,
        "estimated_spans": estimated_n,
        "unavailable_spans": unavailable,
        "total_spans": total,
    }


def _like_for_like(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    """Both periods priced to a similar degree (within 10 percentage points)."""

    def share(totals: dict[str, Any]) -> float | None:
        counts = totals["span_counts"]
        if not counts["total"]:
            return None
        return (counts["total"] - counts["unavailable"]) / counts["total"]

    a, b = share(current), share(previous)
    return a is not None and b is not None and abs(a - b) <= 0.10


def _opt_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _dimension_column(dimension: str):
    return {
        "ai_system": _M.ai_system_id,
        "agent": _M.agent_id,
        "agent_version": _M.agent_version_id,
        "runtime_identity": _M.runtime_identity_id,
        "delegation": _M.delegation_id,
        "provider": _M.provider,
        "model": _M.model,
        "tool": _M.operation,
        "environment": _M.environment,
        "integration": _M.integration_id,
    }.get(dimension)


def _period(filters: FinOpsFilters) -> dict[str, str]:
    return {
        "start": filters.period_start.isoformat(),
        "end": filters.period_end.isoformat(),
    }


def _change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100, 1)
