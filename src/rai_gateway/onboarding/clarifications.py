"""Clarification answers for onboarding inventory (Step 04)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func

from rai_gateway.models.onboarding import OnboardingClarificationAnswer, OnboardingSession
from rai_gateway.onboarding.service import IntakeError, IntakeService, canonical_digest_obj


class ClarificationService:
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
        expected_clarification_revision: int,
        set_id: uuid.UUID | None = None,
        proposal_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version)
            if session.clarification_revision != expected_clarification_revision:
                raise IntakeError(
                    409, "CLARIFICATION_CONFLICT", "Clarification revision changed"
                )
            next_revision = session.clarification_revision + 1
            body = {
                "question_id": question_id,
                "answer_text": answer_text,
                "unknown": unknown,
                "revision": next_revision,
            }
            answer = OnboardingClarificationAnswer(
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
            session.clarification_revision = next_revision
            session.row_version += 1
            self.db.add(answer)
            self.svc._audit(
                "onboarding.clarification.answer",
                {"session_id": str(session_id), "answer_id": str(answer.id)},
                "ANSWER_SAVED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return {
            "answer_id": str(answer.id),
            "clarification_revision": session.clarification_revision,
            "row_version": session.row_version,
            "message": "Answer saved — reanalyze to use it",
        }

    def get_answer(self, session_id: uuid.UUID, answer_id: uuid.UUID) -> dict[str, Any]:
        row = self.db.get(OnboardingClarificationAnswer, answer_id)
        if row is None or row.session_id != session_id:
            raise IntakeError(404, "ANSWER_NOT_FOUND", "Answer not found")
        return {
            "id": str(row.id),
            "question_id": row.question_id,
            "question_snapshot": row.question_snapshot,
            "answer_text": row.answer_text,
            "unknown": row.unknown,
            "author": row.author,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def latest_answers(self, session_id: uuid.UUID) -> list[dict[str, Any]]:
        sub = (
            self.db.query(
                OnboardingClarificationAnswer.question_id,
                func.max(OnboardingClarificationAnswer.answer_revision).label("rev"),
            )
            .filter(OnboardingClarificationAnswer.session_id == session_id)
            .group_by(OnboardingClarificationAnswer.question_id)
            .subquery()
        )
        rows = (
            self.db.query(OnboardingClarificationAnswer)
            .join(
                sub,
                (OnboardingClarificationAnswer.question_id == sub.c.question_id)
                & (OnboardingClarificationAnswer.answer_revision == sub.c.rev),
            )
            .filter(OnboardingClarificationAnswer.session_id == session_id)
            .all()
        )
        return [
            {
                "id": str(r.id),
                "question_id": r.question_id,
                "answer_text": r.answer_text,
                "unknown": r.unknown,
            }
            for r in rows
        ]