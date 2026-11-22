"""Governance clarification answers (Step 05) — separate from inventory revisions."""

from __future__ import annotations

import uuid
from typing import Any

from rai_gateway.models.onboarding import (
    OnboardingGovernanceClarificationAnswer,
    OnboardingSession,
)
from rai_gateway.onboarding.service import IntakeError, IntakeService, canonical_digest_obj


class GovernanceClarificationService:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    def save_answer(
        self,
        session_id: uuid.UUID,
        *,
        question_id: str,
        question_snapshot: dict[str, Any],
        answer_text: str,
        unknown: bool,
        expected_row_version: int,
        expected_governance_clarification_revision: int,
        set_id: uuid.UUID | None = None,
        proposal_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version)
            if (
                session.governance_clarification_revision
                != expected_governance_clarification_revision
            ):
                raise IntakeError(
                    409,
                    "GOVERNANCE_CLARIFICATION_CONFLICT",
                    "Governance clarification revision changed",
                )
            next_revision = session.governance_clarification_revision + 1
            body = {
                "question_id": question_id,
                "answer_text": answer_text,
                "unknown": unknown,
                "revision": next_revision,
                "owner": "governance",
            }
            answer = OnboardingGovernanceClarificationAnswer(
                session_id=session_id,
                answer_revision=next_revision,
                question_id=question_id,
                question_snapshot=question_snapshot,
                answer_text=answer_text,
                unknown=unknown,
                set_id=set_id,
                proposal_id=proposal_id,
                digest=canonical_digest_obj(body),
                author=self.svc.actor,
            )
            session.governance_clarification_revision = next_revision
            session.row_version += 1
            self.db.add(answer)
            self.svc._audit(
                "onboarding.governance.answer",
                {"session_id": str(session_id), "answer_id": str(answer.id)},
                "ANSWER_SAVED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {
            "answer_id": str(answer.id),
            "governance_clarification_revision": session.governance_clarification_revision,
            "row_version": session.row_version,
            "message": "Governance answer saved — regenerate to apply",
        }

    def get_answer(self, session_id: uuid.UUID, answer_id: uuid.UUID) -> dict[str, Any]:
        row = self.db.get(OnboardingGovernanceClarificationAnswer, answer_id)
        if row is None or row.session_id != session_id:
            raise IntakeError(404, "ANSWER_NOT_FOUND", "Answer not found")
        return {
            "id": str(row.id),
            "question_id": row.question_id,
            "question_snapshot": row.question_snapshot,
            "answer_text": row.answer_text,
            "unknown": row.unknown,
            "owner": "governance",
            "author": row.author,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }