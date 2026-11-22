"""Challenge-bound admission for onboarding verification requests."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import OnboardingVerificationAttempt, OnboardingVerificationDispatch
from rai_gateway.onboarding.service import sha256_hex, utcnow


def admit_authorization(
    db: Session,
    *,
    dispatch_id: uuid.UUID,
    challenge: str,
    decision_id: uuid.UUID,
    receipt_id: uuid.UUID | None = None,
) -> bool:
    dispatch = db.get(OnboardingVerificationDispatch, dispatch_id)
    if dispatch is None:
        return False
    attempt = db.get(OnboardingVerificationAttempt, dispatch.attempt_id)
    if attempt is None or attempt.status in ("CANCELLED", "SUCCEEDED", "FAILED"):
        return False
    if attempt.expires_at and attempt.expires_at < utcnow():
        return False
    expected = sha256_hex(f"{attempt.id}:{dispatch.id}")[:32]
    if challenge != expected:
        return False
    if dispatch.admission_state not in ("PENDING", "ADMITTED"):
        return False
    dispatch.admission_state = "ADMITTED"
    dispatch.decision_id = decision_id
    dispatch.receipt_id = receipt_id
    dispatch.admitted_at = utcnow()
    db.flush()
    return True


def lookup_dispatch_for_session(
    db: Session, *, external_session_id: str
) -> OnboardingVerificationDispatch | None:
    return (
        db.query(OnboardingVerificationDispatch)
        .filter(OnboardingVerificationDispatch.external_session_id == external_session_id)
        .one_or_none()
    )