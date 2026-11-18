"""Trajectory list filters: numeric comparisons, model, department/delegator."""

from __future__ import annotations

import pytest

from rai_gateway.evidence.repository import EvidenceRepository, parse_numeric_filter
from rai_gateway.evidence.service import TrajectoryService
from rai_gateway.models.metering import RuntimeMeteringSpan


def _make(db_session, runtime_stack, key, events=0, findings=0):
    traj = TrajectoryService(db_session).get_or_create_trajectory(
        ai_system_id=runtime_stack["system"].id,
        agent_id=runtime_stack["agent"].id,
        agent_version_id=runtime_stack["version"].id,
        runtime_identity_id=runtime_stack["identity"].id,
        conversation_id=key,
        correlation_id=key,
    )
    traj.event_count = events
    traj.finding_count = findings
    db_session.flush()
    return traj


def test_parse_numeric_filter():
    assert parse_numeric_filter("gt:5") == ("gt", 5.0)
    assert parse_numeric_filter(None) is None
    with pytest.raises(ValueError):
        parse_numeric_filter("bigger:5")
    with pytest.raises(ValueError):
        parse_numeric_filter("5")


def test_numeric_model_and_metering_filters(db_session, runtime_stack):
    a = _make(db_session, runtime_stack, "f-a", events=2, findings=0)
    b = _make(db_session, runtime_stack, "f-b", events=10, findings=3)
    db_session.add(
        RuntimeMeteringSpan(
            trajectory_id=b.id, model="gpt-4o", total_tokens=500, billed_cost=0.5
        )
    )
    db_session.commit()
    repo = EvidenceRepository(db_session)

    def ids(**f):
        return {t.id for t in repo.list_trajectories(**f)}

    assert ids(events=("gt", 5)) >= {b.id} and a.id not in ids(events=("gt", 5))
    assert b.id not in ids(events=("lte", 5)) and a.id in ids(events=("lte", 5))
    assert b.id in ids(findings=("eq", 3)) and a.id not in ids(findings=("eq", 3))
    assert b.id in ids(tokens=("gte", 500)) and a.id not in ids(tokens=("gte", 500))
    # no spans counts as zero
    assert a.id in ids(tokens=("lt", 100)) and b.id not in ids(tokens=("lt", 100))
    assert b.id in ids(cost=("gt", 0.1)) and a.id not in ids(cost=("gt", 0.1))
    assert b.id in ids(model="GPT-4") and a.id not in ids(model="GPT-4")
    assert repo.count_trajectories(model="nomatch") == 0
