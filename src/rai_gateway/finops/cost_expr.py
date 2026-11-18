"""Single definition of "known cost" for span-shaped rows.

FinOps and Reports both aggregate these expressions so their figures reconcile.
A row only contributes money when its ``cost_state`` says the amount is real:

* BILLED     -> billed_cost (legacy fallback: actual_cost)
* METERED    -> metered_cost (legacy fallback: actual_cost)
* ACTUAL     -> actual_cost (legacy state, treated as metered)
* ESTIMATED  -> estimated_cost
* anything else (UNAVAILABLE, MIXED, NULL) contributes NULL, never 0.

``SUM`` ignores NULL, so an all-unavailable group sums to NULL. Callers must
distinguish that from a genuine 0.0 by counting rows per state (see
``state_count``) instead of testing the amount for truthiness.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func

from rai_gateway.finops.domain import CostState

BILLED_STATES = (CostState.BILLED.value,)
METERED_STATES = (CostState.METERED.value, CostState.ACTUAL.value)
ESTIMATED_STATES = (CostState.ESTIMATED.value,)
KNOWN_STATES = BILLED_STATES + METERED_STATES + ESTIMATED_STATES


def billed_cost(model: Any):
    return case(
        (
            model.cost_state.in_(BILLED_STATES),
            func.coalesce(model.billed_cost, model.actual_cost),
        ),
        else_=None,
    )


def metered_cost(model: Any):
    return case(
        (
            model.cost_state == CostState.METERED.value,
            func.coalesce(model.metered_cost, model.actual_cost),
        ),
        (model.cost_state == CostState.ACTUAL.value, model.actual_cost),
        else_=None,
    )


def estimated_cost(model: Any):
    return case(
        (model.cost_state.in_(ESTIMATED_STATES), model.estimated_cost),
        else_=None,
    )


def known_cost(model: Any):
    """Billed + metered + estimated amount for one row, NULL when unknown."""
    return case(
        (
            model.cost_state.in_(BILLED_STATES),
            func.coalesce(model.billed_cost, model.actual_cost),
        ),
        (
            model.cost_state == CostState.METERED.value,
            func.coalesce(model.metered_cost, model.actual_cost),
        ),
        (model.cost_state == CostState.ACTUAL.value, model.actual_cost),
        (model.cost_state.in_(ESTIMATED_STATES), model.estimated_cost),
        else_=None,
    )


def state_count(model: Any, states: tuple[str, ...]):
    """Number of rows whose cost_state is one of ``states`` (never NULL)."""
    return func.coalesce(func.sum(case((model.cost_state.in_(states), 1), else_=0)), 0)


def sum_or_none(expr: Any, rows_with_state: Any):
    """SUM(expr) when at least one contributing row exists, else NULL.

    ``rows_with_state`` is the matching ``state_count`` expression. This is what
    keeps a valid zero (rows exist, amount 0) apart from unknown (no rows).
    """
    return case((rows_with_state > 0, func.coalesce(func.sum(expr), 0.0)), else_=None)


def derive_cost_state(
    *, billed: int, metered: int, estimated: int, unavailable: int, total: int
) -> str:
    """Cost state from per-state row counts. Never inspects amounts."""
    if total == 0:
        return "NO_DATA"
    known_kinds = sum(1 for n in (billed, metered, estimated) if n > 0)
    if known_kinds == 0:
        return CostState.UNAVAILABLE.value
    if known_kinds > 1:
        return CostState.MIXED.value
    if billed:
        return CostState.BILLED.value
    if metered:
        return CostState.METERED.value
    return CostState.ESTIMATED.value
