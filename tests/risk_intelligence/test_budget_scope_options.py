"""Budget form scope options and model-scope matching."""

from __future__ import annotations

from uuid import uuid4

from rai_gateway.risk_intelligence.budget_scope import (
    BudgetEvaluationContext,
    budget_matches,
)
from rai_gateway.risk_intelligence.budget_scope_options import list_scope_options
from rai_gateway.risk_intelligence.budgets import BudgetService


def _values(options):
    return {o["value"] for o in options}


def test_scope_options_are_limited_to_the_ai_system(db_session, runtime_stack):
    system = runtime_stack["system"].id
    other = uuid4()

    agents = list_scope_options(db_session, scope_type="AGENT", ai_system_id=system)
    assert str(runtime_stack["agent"].id) in _values(agents)
    assert list_scope_options(db_session, scope_type="AGENT", ai_system_id=other) == []

    versions = list_scope_options(db_session, scope_type="AGENT_VERSION", ai_system_id=system)
    assert str(runtime_stack["version"].id) in _values(versions)
    assert versions[0]["label"].endswith(f"v{runtime_stack['version'].version_number}")

    idents = list_scope_options(db_session, scope_type="RUNTIME_IDENTITY", ai_system_id=system)
    assert str(runtime_stack["identity"].id) in _values(idents)

    deleg = list_scope_options(db_session, scope_type="DELEGATION", ai_system_id=system)
    assert str(runtime_stack["delegation"].id) in _values(deleg)


def test_tool_options_carry_tool_name_and_unfiltered_lists_all(db_session, runtime_stack):
    everything = list_scope_options(db_session, scope_type="TOOL")
    assert str(runtime_stack["submit_tool"].id) in _values(everything)
    row = next(o for o in everything if o["value"] == str(runtime_stack["submit_tool"].id))
    assert row["tool_name"] == runtime_stack["submit_tool"].name


def test_unknown_scope_returns_nothing(db_session):
    assert list_scope_options(db_session, scope_type="NOPE") == []


def test_model_budget_matches_by_resource_id_or_name(db_session, runtime_stack):
    svc = BudgetService(db_session)
    model_id = uuid4()
    by_id = svc.create_budget(
        ai_system_id=runtime_stack["system"].id,
        name="m-id",
        scope_type="MODEL",
        scope_id=str(model_id),
        metric="TOKEN_USAGE",
        limit_value=10,
        window_seconds=3600,
        effect_on_exceed="DENY",
        enabled=True,
    )
    by_name = svc.create_budget(
        ai_system_id=runtime_stack["system"].id,
        name="m-name",
        scope_type="MODEL",
        scope_id="gemini-3.1-flash-lite",
        metric="TOKEN_USAGE",
        limit_value=10,
        window_seconds=3600,
        effect_on_exceed="DENY",
        enabled=True,
    )
    db_session.commit()
    ctx = BudgetEvaluationContext(
        ai_system_id=runtime_stack["system"].id,
        model_resource_id=model_id,
        model="gemini-3.1-flash-lite",
    )
    assert budget_matches(by_id, ctx) and budget_matches(by_name, ctx)
    other = BudgetEvaluationContext(
        ai_system_id=runtime_stack["system"].id, model="another-model"
    )
    assert not budget_matches(by_id, other) and not budget_matches(by_name, other)
