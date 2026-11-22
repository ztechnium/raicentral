"""Baseline capture from committed receipts or reviewed current domain state (Step 08)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingApplication,
    OnboardingApplicationReceipt,
)
from rai_gateway.onboarding.governance_snapshot import capture_governance_baseline
from rai_gateway.onboarding.registry_snapshot import capture_snapshot
from rai_gateway.onboarding.service import canonical_digest_obj


def _merge_projections(registry: dict[str, Any], governance: dict[str, Any]) -> dict[str, Any]:
    return {
        "registry": registry,
        "governance": governance,
        "record_count": (registry.get("record_count") or 0)
        + (governance.get("record_count") or 0),
    }


def capture_current_domain(db: Session) -> tuple[dict[str, Any], str]:
    registry = capture_snapshot(db)
    governance = capture_governance_baseline(db)
    merged = _merge_projections(
        {
            "captured_at": registry.captured_at,
            "digest": registry.digest,
            "projection": registry.projection,
            "record_count": registry.record_count,
        },
        {
            "captured_at": governance.captured_at,
            "digest": governance.digest,
            "projection": governance.projection,
            "record_count": governance.projection.get("record_count", 0),
        },
    )
    return merged, canonical_digest_obj(merged)


def list_baseline_choices(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    receipts = (
        db.query(OnboardingApplicationReceipt)
        .join(OnboardingApplication)
        .filter(OnboardingApplication.session_id == session_id)
        .order_by(OnboardingApplicationReceipt.created_at.desc())
        .all()
    )
    choices: list[dict[str, Any]] = []
    for row in receipts:
        choices.append(
            {
                "kind": "APPLIED_RECEIPT",
                "application_receipt_id": str(row.id),
                "application_id": str(row.application_id),
                "fingerprint": row.fingerprint,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    choices.append(
        {
            "kind": "REVIEWED_CURRENT_STATE",
            "description": "Capture current authoritative domain configuration as comparison scope",
        }
    )
    return choices
