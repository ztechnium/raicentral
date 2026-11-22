"""Read-only policy/risk baseline snapshot for governance reconciliation (Step 05)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.models.policy_library import (
    PolicyRuleLink,
    PolicySource,
    PolicySourceObligation,
)
from rai_gateway.models.risk_intelligence import RuntimeBudget
from rai_gateway.onboarding.governance_schemas import GOVERNANCE_MATCHING_RULE_VERSION
from rai_gateway.onboarding.service import canonical_digest_obj

MAX_BASELINE_RECORDS = 20_000


@dataclass(frozen=True)
class GovernanceBaseline:
    captured_at: str
    digest: str
    matching_rule_version: str
    projection: dict[str, Any]


def capture_governance_baseline(db: Session) -> GovernanceBaseline:
    """Direct ORM read — no service helpers that lazy-create domain rows."""
    sources = db.query(PolicySource).limit(MAX_BASELINE_RECORDS).all()
    obligations = db.query(PolicySourceObligation).limit(MAX_BASELINE_RECORDS).all()
    links = db.query(PolicyRuleLink).limit(MAX_BASELINE_RECORDS).all()
    budgets = db.query(RuntimeBudget).limit(MAX_BASELINE_RECORDS).all()
    total = len(sources) + len(obligations) + len(links) + len(budgets)
    if total > MAX_BASELINE_RECORDS:
        raise ValueError("BASELINE_OVERFLOW")
    projection: dict[str, Any] = {
        "policy_sources": [
            {"id": str(r.id), "title": r.title, "source_type": r.source_type}
            for r in sources
        ],
        "obligations": [
            {
                "id": str(r.id),
                "policy_source_id": str(r.policy_source_id),
                "topic": r.topic,
            }
            for r in obligations
        ],
        "rule_links": [
            {
                "id": str(r.id),
                "policy_source_id": str(r.policy_source_id),
                "rule_type": r.rule_type,
                "rule_id": str(r.rule_id),
            }
            for r in links
        ],
        "runtime_budgets": [
            {
                "id": str(r.id),
                "name": r.name,
                "metric": r.metric,
                "limit_value": r.limit_value,
            }
            for r in budgets
        ],
        "record_count": total,
    }
    captured = datetime.now(timezone.utc).isoformat()
    digest = canonical_digest_obj(projection)
    return GovernanceBaseline(
        captured_at=captured,
        digest=digest,
        matching_rule_version=GOVERNANCE_MATCHING_RULE_VERSION,
        projection=projection,
    )