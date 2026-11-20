"""Onboarding intake service: sessions, immutable source revisions, runs.

Commit ownership is explicit: every public mutating method commits (or rolls
back) its own short transaction. Nothing here parses document content, calls an
AI provider, or writes catalog / registry / policy / budget / credential state.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.database import AdminAudit
from rai_gateway.models.onboarding import (
    OnboardingMutationReceipt,
    OnboardingRun,
    OnboardingSession,
    OnboardingSource,
    OnboardingSourceRevision,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.storage import (
    OnboardingSourceStore,
    SourceLimitError,
    SourceStorageError,
    StagedObject,
    UnsupportedMedia,
    detect_media_family,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue

JOB_TYPE = "ONBOARDING_PREPARE_SOURCES"
SOURCES_PERMISSION = "onboarding:sources:read"
SAFE_ERROR_CODES = frozenset(
    {
        "SOURCE_INTEGRITY_FAILED",
        "SOURCE_MISSING",
        "STORAGE_UNAVAILABLE",
        "UNSUPPORTED_MEDIA",
        "SOURCE_TOO_LARGE",
        "INVALID_RUN_BINDING",
        "LEASE_EXPIRED",
        "INTERNAL_ERROR",
    }
)


class IntakeError(Exception):
    """Structured, safe API error."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            body["details"] = self.details
        return {"error": body}


@dataclass(frozen=True)
class Access:
    """What the caller may see/do, derived from the server permission list."""

    can_read: bool
    can_content: bool
    can_manage: bool

    @classmethod
    def from_tenant(cls, tenant: Any) -> "Access":
        read = tenant.has_permission("sdk:read")
        content = read and tenant.has_permission(SOURCES_PERMISSION)
        manage = content and tenant.has_permission("sdk:manage")
        return cls(can_read=read, can_content=content, can_manage=manage)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def canonical_digest(entries: list[dict[str, Any]]) -> str:
    return sha256_hex(json.dumps(entries, sort_keys=True, separators=(",", ":")))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class IntakeService:
    def __init__(
        self,
        db: Session,
        store: OnboardingSourceStore,
        *,
        actor: str,
    ) -> None:
        self.db = db
        self.store = store
        self.actor = actor

    # ------------------------------------------------------------------ utils
    def _audit(self, action: str, target: dict[str, Any], reason: str) -> None:
        self.db.add(
            AdminAudit(
                trace_id=uuid.uuid4().hex,
                actor_user_id=self.actor,
                actor_role="onboarding_intake",
                action=action,
                target=target,
                reason=reason,
            )
        )

    def _get_session(
        self, session_id: uuid.UUID, *, lock: bool = False
    ) -> OnboardingSession:
        query = self.db.query(OnboardingSession).filter(
            OnboardingSession.id == session_id
        )
        if lock:
            query = query.with_for_update().populate_existing()
        row = query.one_or_none()
        if row is None:
            raise IntakeError(404, "SESSION_NOT_FOUND", "Session not found")
        return row

    def _active_run(self, session_id: uuid.UUID) -> OnboardingRun | None:
        return (
            self.db.query(OnboardingRun)
            .filter(
                OnboardingRun.session_id == session_id, OnboardingRun.ended_at.is_(None)
            )
            .one_or_none()
        )

    def _latest_run(self, session_id: uuid.UUID) -> OnboardingRun | None:
        return (
            self.db.query(OnboardingRun)
            .filter(OnboardingRun.session_id == session_id)
            .order_by(OnboardingRun.run_number.desc())
            .first()
        )

    def _job(self, run: OnboardingRun) -> BackgroundJob:
        job = self.db.get(BackgroundJob, run.job_id)
        assert job is not None
        return job

    # -------------------------------------------------------------- receipts
    @staticmethod
    def _fingerprint(parts: dict[str, Any]) -> str:
        return sha256_hex(json.dumps(parts, sort_keys=True, separators=(",", ":")))

    def _scope(self, op: str, session_id: uuid.UUID | None, target: str = "") -> str:
        if session_id is None:
            return f"{op}:{self.actor}"
        return f"{session_id}:{self.actor}:{op}:{target}"

    def _lookup_receipt(
        self, scope: str, key: str, fingerprint: str
    ) -> OnboardingMutationReceipt | None:
        receipt = (
            self.db.query(OnboardingMutationReceipt)
            .filter(
                OnboardingMutationReceipt.scope == scope,
                OnboardingMutationReceipt.key_digest == sha256_hex(key),
            )
            .one_or_none()
        )
        if receipt is None:
            return None
        if receipt.fingerprint != fingerprint:
            raise IntakeError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This idempotency key was already used for a different request",
            )
        return receipt

    def _add_receipt(
        self,
        scope: str,
        key: str,
        fingerprint: str,
        kind: str,
        resource_id: uuid.UUID,
    ) -> None:
        self.db.add(
            OnboardingMutationReceipt(
                scope=scope,
                key_digest=sha256_hex(key),
                fingerprint=fingerprint,
                resource_kind=kind,
                resource_id=resource_id,
                actor=self.actor,
            )
        )

    # -------------------------------------------------------------- sessions
    def create_session(
        self, *, title: str, idempotency_key: str, access: Access
    ) -> dict[str, Any]:
        title = self._clean_title(title)
        scope = self._scope("create_session", None)
        fingerprint = self._fingerprint({"op": "create_session", "title": title})
        receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is None:
            session_id = uuid.uuid4()
            try:
                self.db.add(
                    OnboardingSession(
                        id=session_id,
                        title=title,
                        lifecycle="OPEN",
                        row_version=1,
                        input_revision=0,
                        created_by=self.actor,
                        updated_by=self.actor,
                    )
                )
                self._add_receipt(
                    scope, idempotency_key, fingerprint, "SESSION", session_id
                )
                self._audit(
                    "onboarding.session.create",
                    {"session_id": str(session_id)},
                    "SESSION_CREATED",
                )
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
                if receipt is None:
                    raise
                session_id = receipt.resource_id
        else:
            session_id = receipt.resource_id
        return self.session_view(session_id, access)

    @staticmethod
    def _clean_title(title: str) -> str:
        cleaned = (title or "").strip()
        if not cleaned or len(cleaned) > 200:
            raise IntakeError(422, "INVALID_TITLE", "Title must be 1-200 characters")
        return cleaned

    def _check_mutable(
        self,
        session: OnboardingSession,
        expected_row_version: int,
        *,
        require_open: bool = True,
        require_idle: bool = True,
    ) -> None:
        if session.row_version != expected_row_version:
            raise IntakeError(
                409,
                "REVISION_CONFLICT",
                "This session changed since you loaded it. Refresh and reconcile.",
                details={"current_row_version": session.row_version},
            )
        if require_open and session.lifecycle != "OPEN":
            raise IntakeError(
                409, "SESSION_ARCHIVED", "Session is archived and read-only"
            )
        if require_idle:
            active = self._active_run(session.id)
            if active is not None:
                raise IntakeError(
                    409,
                    "PREPARATION_ACTIVE",
                    "Cancel the active preparation before changing inputs",
                    details={"run_id": str(active.id)},
                )

    def update_title(
        self,
        session_id: uuid.UUID,
        *,
        title: str,
        expected_row_version: int,
        access: Access,
    ) -> dict[str, Any]:
        title = self._clean_title(title)
        session = self._get_session(session_id, lock=True)
        try:
            self._check_mutable(session, expected_row_version, require_idle=False)
            if session.title != title:
                session.title = title
                session.updated_by = self.actor
                session.row_version += 1
                self._audit(
                    "onboarding.session.rename",
                    {"session_id": str(session.id)},
                    "TITLE_CHANGED",
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.session_view(session_id, access)

    def set_lifecycle(
        self,
        session_id: uuid.UUID,
        *,
        archive: bool,
        expected_row_version: int,
        access: Access,
    ) -> dict[str, Any]:
        session = self._get_session(session_id, lock=True)
        try:
            self._check_mutable(
                session,
                expected_row_version,
                require_open=archive,
                require_idle=archive,
            )
            target = "ARCHIVED" if archive else "OPEN"
            if session.lifecycle != target:
                session.lifecycle = target
                session.updated_by = self.actor
                session.row_version += 1
                self._audit(
                    "onboarding.session.archive"
                    if archive
                    else "onboarding.session.restore",
                    {"session_id": str(session.id)},
                    "LIFECYCLE_CHANGED",
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.session_view(session_id, access)

    # --------------------------------------------------------------- sources
    def stage_file(
        self,
        chunks: Iterable[bytes],
        *,
        filename: str,
        declared_type: str | None,
        deadline: float | None,
    ) -> tuple[StagedObject, str]:
        """Stream bytes to private staging and identify the media family."""
        filename = (filename or "").strip()
        if not filename:
            raise IntakeError(422, "UNSUPPORTED_MEDIA", "A file name is required")
        try:
            staged = self.store.stage(
                chunks, max_bytes=settings.onboarding_max_file_bytes, deadline=deadline
            )
        except SourceLimitError as exc:
            if exc.code == "UPLOAD_TIMEOUT":
                raise IntakeError(
                    408, "UPLOAD_TIMEOUT", "Upload took too long", retryable=True
                ) from exc
            raise IntakeError(
                413, "SOURCE_TOO_LARGE", "File exceeds the maximum allowed size"
            ) from exc
        except SourceStorageError as exc:
            raise IntakeError(
                503,
                "STORAGE_UNAVAILABLE",
                "Source storage is unavailable",
                retryable=True,
            ) from exc
        try:
            family = detect_media_family(filename, declared_type, staged)
        except UnsupportedMedia as exc:
            self.store.discard(staged)
            raise IntakeError(415, "UNSUPPORTED_MEDIA", str(exc)) from exc
        return staged, family

    def stage_text(self, text: str) -> StagedObject:
        if not isinstance(text, str) or not text.strip():
            raise IntakeError(422, "INPUT_REQUIRED", "Text must not be empty")
        if len(text) > settings.onboarding_max_text_chars:
            raise IntakeError(
                413, "SOURCE_TOO_LARGE", "Text exceeds the maximum allowed length"
            )
        data = text.encode("utf-8")
        try:
            return self.store.stage(
                [data],
                max_bytes=settings.onboarding_max_text_chars * 4,
            )
        except SourceStorageError as exc:
            raise IntakeError(
                503,
                "STORAGE_UNAVAILABLE",
                "Source storage is unavailable",
                retryable=True,
            ) from exc

    def add_source(
        self,
        session_id: uuid.UUID,
        *,
        kind: str,
        role: str,
        staged: StagedObject,
        family: str,
        display_name: str,
        declared_type: str | None,
        expected_row_version: int,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        fingerprint = self._fingerprint(
            {
                "op": "add_source",
                "kind": kind,
                "role": role,
                "name": display_name,
                "type": declared_type,
                "sha": staged.sha256,
                "len": staged.byte_length,
            }
        )
        return self._publish_input(
            session_id,
            source_id=None,
            kind=kind,
            role=role,
            staged=staged,
            family=family,
            display_name=display_name,
            declared_type=declared_type,
            expected_row_version=expected_row_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            scope=self._scope("add_source", session_id, kind),
            access=access,
        )

    def add_revision(
        self,
        session_id: uuid.UUID,
        source_id: uuid.UUID,
        *,
        kind: str,
        staged: StagedObject,
        family: str,
        display_name: str | None,
        declared_type: str | None,
        expected_row_version: int,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        fingerprint = self._fingerprint(
            {
                "op": "add_revision",
                "source": str(source_id),
                "kind": kind,
                "name": display_name,
                "type": declared_type,
                "sha": staged.sha256,
                "len": staged.byte_length,
            }
        )
        return self._publish_input(
            session_id,
            source_id=source_id,
            kind=kind,
            role=None,
            staged=staged,
            family=family,
            display_name=display_name,
            declared_type=declared_type,
            expected_row_version=expected_row_version,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            scope=self._scope("add_revision", session_id, str(source_id)),
            access=access,
        )

    def _replay_input(
        self, receipt: OnboardingMutationReceipt, session_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        revision = self.db.get(OnboardingSourceRevision, receipt.resource_id)
        return {
            "session": self.session_view(session_id, access),
            "source_id": str(revision.source_id) if revision else None,
            "revision_id": str(receipt.resource_id),
            "replayed": True,
        }

    def _publish_input(
        self,
        session_id: uuid.UUID,
        *,
        source_id: uuid.UUID | None,
        kind: str,
        role: str | None,
        staged: StagedObject,
        family: str,
        display_name: str | None,
        declared_type: str | None,
        expected_row_version: int,
        idempotency_key: str,
        fingerprint: str,
        scope: str,
        access: Access,
    ) -> dict[str, Any]:
        finalized_key: str | None = None
        try:
            self._get_session(session_id)  # 404 before touching anything else
            receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                self.store.discard(staged)
                return self._replay_input(receipt, session_id, access)

            session = self._get_session(session_id, lock=True)
            receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                self.db.rollback()
                self.store.discard(staged)
                return self._replay_input(receipt, session_id, access)
            self._check_mutable(session, expected_row_version)

            source: OnboardingSource | None = None
            if source_id is not None:
                source = (
                    self.db.query(OnboardingSource)
                    .filter(
                        OnboardingSource.id == source_id,
                        OnboardingSource.session_id == session_id,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if source is None:
                    raise IntakeError(404, "SOURCE_NOT_FOUND", "Source not found")
                if source.kind != kind:
                    raise IntakeError(
                        422,
                        "KIND_MISMATCH",
                        f"This source holds {source.kind.lower()} content",
                    )
                role = source.role
            else:
                included = (
                    self.db.query(func.count(OnboardingSource.id))
                    .filter(
                        OnboardingSource.session_id == session_id,
                        OnboardingSource.included.is_(True),
                    )
                    .scalar()
                )
                if included >= settings.onboarding_max_sources_per_session:
                    raise IntakeError(
                        409,
                        "SESSION_LIMIT_EXCEEDED",
                        "This session has reached its source limit",
                    )
                if role == "USER_PROMPT":
                    self._require_no_prompt(session_id)

            retained = (
                self.db.query(
                    func.coalesce(func.sum(OnboardingSourceRevision.byte_length), 0)
                )
                .filter(OnboardingSourceRevision.session_id == session_id)
                .scalar()
            )
            if (
                int(retained) + staged.byte_length
                > settings.onboarding_max_session_bytes
            ):
                raise IntakeError(
                    409,
                    "SESSION_LIMIT_EXCEEDED",
                    "This session has reached its retained storage limit",
                )

            finalized_key = self.store.finalize(staged)

            if source is None:
                source = OnboardingSource(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    kind=kind,
                    role=role,
                    included=True,
                    created_by=self.actor,
                )
                self.db.add(source)
                self.db.flush()
                number = 1
            else:
                number = (
                    self.db.query(func.max(OnboardingSourceRevision.revision_number))
                    .filter(OnboardingSourceRevision.source_id == source.id)
                    .scalar()
                    or 0
                ) + 1
            revision = OnboardingSourceRevision(
                id=uuid.uuid4(),
                source_id=source.id,
                session_id=session_id,
                revision_number=number,
                display_name=(display_name or "Untitled")[:255],
                declared_media_type=(declared_type or None),
                media_family=family,
                storage_key=finalized_key,
                sha256=staged.sha256,
                byte_length=staged.byte_length,
                created_by=self.actor,
            )
            self.db.add(revision)
            self.db.flush()
            source.current_revision_id = revision.id
            session.input_revision += 1
            session.row_version += 1
            session.updated_by = self.actor
            self._add_receipt(
                scope, idempotency_key, fingerprint, "REVISION", revision.id
            )
            self._audit(
                "onboarding.source.add" if number == 1 else "onboarding.source.revise",
                {
                    "session_id": str(session_id),
                    "source_id": str(source.id),
                    "revision_id": str(revision.id),
                },
                "SOURCE_SAVED",
            )
            self.db.commit()
            result = {
                "session": self.session_view(session_id, access),
                "source_id": str(source.id),
                "revision_id": str(revision.id),
                "replayed": False,
            }
            return result
        except IntegrityError:
            self.db.rollback()
            if finalized_key:
                self._discard_key(finalized_key)
            else:
                self.store.discard(staged)
            receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                return self._replay_input(receipt, session_id, access)
            raise
        except SourceStorageError as exc:
            self.db.rollback()
            self.store.discard(staged)
            raise IntakeError(
                503,
                "STORAGE_UNAVAILABLE",
                "Source storage is unavailable",
                retryable=True,
            ) from exc
        except BaseException:
            self.db.rollback()
            if finalized_key:
                self._discard_key(finalized_key)
            else:
                self.store.discard(staged)
            raise

    def _discard_key(self, key: str) -> None:
        discard = getattr(self.store, "discard_key", None)
        if discard:
            discard(key)

    def _require_no_prompt(self, session_id: uuid.UUID) -> None:
        exists = (
            self.db.query(OnboardingSource.id)
            .filter(
                OnboardingSource.session_id == session_id,
                OnboardingSource.role == "USER_PROMPT",
                OnboardingSource.included.is_(True),
            )
            .first()
        )
        if exists:
            raise IntakeError(
                409,
                "PROMPT_EXISTS",
                "This session already has a prompt. Save a new revision of it instead.",
            )

    def set_included(
        self,
        session_id: uuid.UUID,
        source_id: uuid.UUID,
        *,
        included: bool,
        expected_row_version: int,
        access: Access,
    ) -> dict[str, Any]:
        session = self._get_session(session_id, lock=True)
        try:
            self._check_mutable(session, expected_row_version)
            source = (
                self.db.query(OnboardingSource)
                .filter(
                    OnboardingSource.id == source_id,
                    OnboardingSource.session_id == session_id,
                )
                .with_for_update()
                .one_or_none()
            )
            if source is None:
                raise IntakeError(404, "SOURCE_NOT_FOUND", "Source not found")
            if source.included != included:
                if included:
                    count = (
                        self.db.query(func.count(OnboardingSource.id))
                        .filter(
                            OnboardingSource.session_id == session_id,
                            OnboardingSource.included.is_(True),
                        )
                        .scalar()
                    )
                    if count >= settings.onboarding_max_sources_per_session:
                        raise IntakeError(
                            409,
                            "SESSION_LIMIT_EXCEEDED",
                            "This session has reached its source limit",
                        )
                    if source.role == "USER_PROMPT":
                        self._require_no_prompt(session_id)
                source.included = included
                session.input_revision += 1
                session.row_version += 1
                session.updated_by = self.actor
                self._audit(
                    "onboarding.source.include"
                    if included
                    else "onboarding.source.exclude",
                    {"session_id": str(session_id), "source_id": str(source_id)},
                    "INCLUSION_CHANGED",
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.session_view(session_id, access)

    def _revision_chain(
        self,
        session_id: uuid.UUID,
        source_id: uuid.UUID,
        revision_id: uuid.UUID | None = None,
    ) -> tuple[OnboardingSource, list[OnboardingSourceRevision]]:
        self._get_session(session_id)
        source = (
            self.db.query(OnboardingSource)
            .filter(
                OnboardingSource.id == source_id,
                OnboardingSource.session_id == session_id,
            )
            .one_or_none()
        )
        if source is None:
            raise IntakeError(404, "SOURCE_NOT_FOUND", "Source not found")
        query = self.db.query(OnboardingSourceRevision).filter(
            OnboardingSourceRevision.source_id == source_id,
            OnboardingSourceRevision.session_id == session_id,
        )
        if revision_id is not None:
            query = query.filter(OnboardingSourceRevision.id == revision_id)
        revisions = query.order_by(
            OnboardingSourceRevision.revision_number.desc()
        ).all()
        if revision_id is not None and not revisions:
            raise IntakeError(404, "REVISION_NOT_FOUND", "Revision not found")
        return source, revisions

    def list_revisions(
        self, session_id: uuid.UUID, source_id: uuid.UUID
    ) -> dict[str, Any]:
        source, revisions = self._revision_chain(session_id, source_id)
        return {
            "source_id": str(source.id),
            "kind": source.kind,
            "role": source.role,
            "included": source.included,
            "items": [
                {
                    **self._revision_view(rev, content=True),
                    "current": rev.id == source.current_revision_id,
                }
                for rev in revisions
            ],
        }

    def open_content(
        self, session_id: uuid.UUID, source_id: uuid.UUID, revision_id: uuid.UUID
    ):
        """Authorized exact original bytes. Records an audit entry."""
        _, revisions = self._revision_chain(session_id, source_id, revision_id)
        revision = revisions[0]
        try:
            handle = self.store.open_read(revision.storage_key)
        except SourceStorageError as exc:
            raise IntakeError(
                503,
                "STORAGE_UNAVAILABLE",
                "The original is currently unavailable",
                retryable=True,
            ) from exc
        self._audit(
            "onboarding.source.content_read",
            {
                "session_id": str(session_id),
                "source_id": str(source_id),
                "revision_id": str(revision_id),
            },
            "CONTENT_ACCESSED",
        )
        self.db.commit()
        return revision, handle

    # --------------------------------------------------------------- manifest
    def build_manifest(self, session_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = (
            self.db.query(OnboardingSource, OnboardingSourceRevision)
            .join(
                OnboardingSourceRevision,
                OnboardingSourceRevision.id == OnboardingSource.current_revision_id,
            )
            .filter(
                OnboardingSource.session_id == session_id,
                OnboardingSource.included.is_(True),
            )
            .all()
        )
        entries = [
            {
                "source_id": str(source.id),
                "source_revision_id": str(rev.id),
                "kind": source.kind,
                "role": source.role,
                "sha256": rev.sha256,
                "byte_length": int(rev.byte_length),
                "media_family": rev.media_family,
            }
            for source, rev in rows
        ]
        entries.sort(key=lambda e: e["source_id"])
        return entries

    # ------------------------------------------------------------------ runs
    def _new_run(
        self,
        session: OnboardingSession,
        entries: list[dict[str, Any]],
        *,
        retry_of: OnboardingRun | None,
    ) -> OnboardingRun:
        run_id = uuid.uuid4()
        digest = canonical_digest(entries)
        number = (
            self.db.query(func.max(OnboardingRun.run_number))
            .filter(OnboardingRun.session_id == session.id)
            .scalar()
            or 0
        ) + 1
        job = JobQueue(self.db).enqueue(
            job_type=JOB_TYPE,
            payload={
                "run_id": str(run_id),
                "session_id": str(session.id),
                "input_revision": session.input_revision,
                "manifest_digest": digest,
            },
            idempotency_key=f"onboarding:prepare:{run_id}",
            created_by=self.actor,
            max_attempts=settings.onboarding_job_max_attempts,
            allow_reserved=True,
        )
        run = OnboardingRun(
            id=run_id,
            session_id=session.id,
            run_number=number,
            stage="PREPARE_SOURCES",
            input_revision=session.input_revision,
            manifest={"version": 1, "entries": entries},
            manifest_digest=digest,
            job_id=uuid.UUID(job["id"]),
            retry_of_run_id=retry_of.id if retry_of else None,
            created_by=self.actor,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def _run_result(
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        access: Access,
        *,
        replayed: bool,
    ):
        return {
            "session": self.session_view(session_id, access),
            "run": self.run_view(session_id, run_id, access),
            "replayed": replayed,
        }

    def prepare(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        scope = self._scope("prepare", session_id)
        fingerprint = self._fingerprint(
            {"op": "prepare", "requested_input_revision": requested_input_revision}
        )
        self._get_session(session_id)
        receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is not None:
            return self._run_result(
                session_id, receipt.resource_id, access, replayed=True
            )
        session = self._get_session(session_id, lock=True)
        try:
            receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                self.db.rollback()
                return self._run_result(
                    session_id, receipt.resource_id, access, replayed=True
                )
            self._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(
                    409,
                    "REVISION_CONFLICT",
                    "Inputs changed since this request was made",
                    details={"current_input_revision": session.input_revision},
                )
            run = self._reuse_or_create_prepare_run(session)
            self._add_receipt(scope, idempotency_key, fingerprint, "RUN", run.id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self._run_result(session_id, run.id, access, replayed=False)

    def _reuse_or_create_prepare_run(self, session: OnboardingSession) -> OnboardingRun:
        latest = self._latest_run(session.id)
        if latest is not None and latest.input_revision == session.input_revision:
            job = self._job(latest)
            if (
                latest.ended_at is None
                or job.status == BackgroundJobStatus.SUCCEEDED.value
            ):
                return latest  # converge on the matching active / successful run
            raise IntakeError(
                409,
                "RETRY_REQUIRED",
                "The last preparation for these inputs did not succeed. Retry it explicitly.",
                details={"run_id": str(latest.id)},
            )
        active = self._active_run(session.id)
        if active is not None:
            raise IntakeError(
                409, "PREPARATION_ACTIVE", "Preparation is already running"
            )
        entries = self.build_manifest(session.id)
        if not entries:
            raise IntakeError(
                422, "INPUT_REQUIRED", "Add at least one included file or prompt first"
            )
        run = self._new_run(session, entries, retry_of=None)
        self._audit(
            "onboarding.run.prepare",
            {"session_id": str(session.id), "run_id": str(run.id)},
            "PREPARATION_REQUESTED",
        )
        return run

    def retry(
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        scope = self._scope("retry", session_id, str(run_id))
        fingerprint = self._fingerprint(
            {
                "op": "retry",
                "run": str(run_id),
                "requested_input_revision": requested_input_revision,
            }
        )
        self._get_session(session_id)
        receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is not None:
            return self._run_result(
                session_id, receipt.resource_id, access, replayed=True
            )
        session = self._get_session(session_id, lock=True)
        try:
            receipt = self._lookup_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                self.db.rollback()
                return self._run_result(
                    session_id, receipt.resource_id, access, replayed=True
                )
            run = self._get_run(session_id, run_id)
            self._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(
                    409,
                    "REVISION_CONFLICT",
                    "Inputs changed since this request was made",
                    details={"current_input_revision": session.input_revision},
                )
            latest = self._latest_run(session_id)
            assert latest is not None
            if latest.id != run.id:
                if latest.retry_of_run_id == run.id and latest.ended_at is None:
                    new_run = latest  # parallel retries converge
                else:
                    raise IntakeError(
                        409,
                        "RUN_SUPERSEDED",
                        "A newer preparation run exists",
                        details={"current_run_id": str(latest.id)},
                    )
            else:
                job = self._job(run)
                if run.ended_at is None:
                    raise IntakeError(
                        409, "PREPARATION_ACTIVE", "Preparation is still active"
                    )
                if job.status == BackgroundJobStatus.SUCCEEDED.value:
                    raise IntakeError(
                        409, "RUN_NOT_RETRYABLE", "This run already succeeded"
                    )
                entries = self.build_manifest(session_id)
                if run.input_revision != session.input_revision or (
                    canonical_digest(entries) != run.manifest_digest
                ):
                    raise IntakeError(
                        409,
                        "INPUTS_CHANGED",
                        "Inputs changed since this run. Prepare the new inputs instead.",
                    )
                new_run = self._new_run(session, entries, retry_of=run)
                self._audit(
                    "onboarding.run.retry",
                    {
                        "session_id": str(session_id),
                        "run_id": str(new_run.id),
                        "retry_of_run_id": str(run.id),
                    },
                    "RETRY_REQUESTED",
                )
            self._add_receipt(scope, idempotency_key, fingerprint, "RUN", new_run.id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self._run_result(session_id, new_run.id, access, replayed=False)

    def cancel(
        self, session_id: uuid.UUID, run_id: uuid.UUID, *, access: Access
    ) -> dict[str, Any]:
        # Lock order everywhere: session, then job.
        self._get_session(session_id, lock=True)
        try:
            run = self._get_run(session_id, run_id, lock=True)
            job = (
                self.db.query(BackgroundJob)
                .filter(BackgroundJob.id == run.job_id)
                .with_for_update()
                .populate_existing()
                .one()
            )
            if run.ended_at is not None:
                if job.status == BackgroundJobStatus.CANCELLED.value:
                    self.db.rollback()
                    return self._run_result(session_id, run_id, access, replayed=True)
                raise IntakeError(409, "RUN_NOT_ACTIVE", "This run already finished")
            job.status = BackgroundJobStatus.CANCELLED.value
            job.completed_at = utcnow()
            job.lease_token = None
            job.lease_expires_at = None
            run.ended_at = utcnow()
            self._audit(
                "onboarding.run.cancel",
                {"session_id": str(session_id), "run_id": str(run_id)},
                "CANCELLED_BY_USER",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self._run_result(session_id, run_id, access, replayed=False)

    def _get_run(
        self, session_id: uuid.UUID, run_id: uuid.UUID, *, lock: bool = False
    ) -> OnboardingRun:
        query = self.db.query(OnboardingRun).filter(
            OnboardingRun.id == run_id, OnboardingRun.session_id == session_id
        )
        if lock:
            query = query.with_for_update().populate_existing()
        run = query.one_or_none()
        if run is None:
            raise IntakeError(404, "RUN_NOT_FOUND", "Run not found")
        return run

    # ------------------------------------------------------------------ views
    @staticmethod
    def run_state(job: BackgroundJob) -> str:
        status = job.status
        if status == BackgroundJobStatus.PENDING.value:
            return "RETRY_SCHEDULED" if (job.attempt_count or 0) > 0 else "QUEUED"
        if status == BackgroundJobStatus.RUNNING.value:
            return "PREPARING"
        if status == BackgroundJobStatus.SUCCEEDED.value:
            return "SUCCEEDED"
        if status == BackgroundJobStatus.CANCELLED.value:
            return "CANCELLED"
        return "FAILED"

    def run_view(
        self, session_id: uuid.UUID, run_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        run = self._get_run(session_id, run_id)
        return self._run_dict(run, access)

    def _run_dict(self, run: OnboardingRun, access: Access) -> dict[str, Any]:
        job = (
            self.db.query(BackgroundJob)
            .filter(BackgroundJob.id == run.job_id)
            .populate_existing()
            .one()
        )
        state = self.run_state(job)
        result = job.result if isinstance(job.result, dict) else {}
        progress = (
            result.get("progress") if isinstance(result.get("progress"), dict) else {}
        )
        entries = (
            run.manifest.get("entries", []) if isinstance(run.manifest, dict) else []
        )
        view: dict[str, Any] = {
            "id": str(run.id),
            "run_number": run.run_number,
            "stage": run.stage,
            "state": state,
            "active": run.ended_at is None,
            "input_revision": run.input_revision,
            "retry_of_run_id": str(run.retry_of_run_id)
            if run.retry_of_run_id
            else None,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "source_count": len(entries),
            "sources_checked": progress.get(
                "sources_checked", result.get("sources_checked")
            ),
            "sources_total": progress.get(
                "sources_total", result.get("sources_total", len(entries))
            ),
            "created_at": _iso(run.created_at),
            "ended_at": _iso(run.ended_at),
            "error_code": job.last_error
            if job.last_error in SAFE_ERROR_CODES
            else None,
        }
        if state == "SUCCEEDED":
            view["checked_bytes"] = result.get("bytes_checked")
            view["checked_at"] = result.get("checked_at")
        if access.can_content:
            view["manifest_digest"] = run.manifest_digest
            view["manifest"] = {"version": 1, "entries": entries}
        return view

    def _revision_view(
        self, rev: OnboardingSourceRevision, *, content: bool
    ) -> dict[str, Any]:
        view: dict[str, Any] = {
            "id": str(rev.id),
            "revision_number": rev.revision_number,
            "media_family": rev.media_family,
            "byte_length": int(rev.byte_length),
            "received_at": _iso(rev.received_at),
        }
        if content:
            view["display_name"] = rev.display_name
            view["sha256"] = rev.sha256
            view["created_by"] = rev.created_by
        return view

    def _derive_view(
        self, session: OnboardingSession, latest: OnboardingRun | None
    ) -> str:
        if session.lifecycle == "ARCHIVED":
            return "ARCHIVED"
        if latest is None or latest.input_revision != session.input_revision:
            return "DRAFT"
        state = self.run_state(self._job(latest))
        return {
            "QUEUED": "QUEUED",
            "RETRY_SCHEDULED": "QUEUED",
            "PREPARING": "PREPARING",
            "SUCCEEDED": "READY_FOR_ANALYSIS",
            "FAILED": "PREPARATION_FAILED",
            "CANCELLED": "DRAFT",
        }[state]

    def session_view(self, session_id: uuid.UUID, access: Access) -> dict[str, Any]:
        session = (
            self.db.query(OnboardingSession)
            .filter(OnboardingSession.id == session_id)
            .populate_existing()
            .one_or_none()
        )
        if session is None:
            raise IntakeError(404, "SESSION_NOT_FOUND", "Session not found")
        latest = self._latest_run(session_id)
        view_state = self._derive_view(session, latest)
        rows = (
            self.db.query(OnboardingSource, OnboardingSourceRevision)
            .outerjoin(
                OnboardingSourceRevision,
                OnboardingSourceRevision.id == OnboardingSource.current_revision_id,
            )
            .filter(OnboardingSource.session_id == session_id)
            .order_by(OnboardingSource.created_at.asc(), OnboardingSource.id.asc())
            .all()
        )
        sources: list[dict[str, Any]] = []
        for index, (source, rev) in enumerate(rows, start=1):
            item: dict[str, Any] = {
                "id": str(source.id),
                "label": f"Source {index}",
                "kind": source.kind,
                "role": source.role,
                "included": source.included,
                "created_at": _iso(source.created_at),
            }
            if rev is not None:
                item["current_revision"] = self._revision_view(
                    rev, content=access.can_content
                )
            sources.append(item)
        active = latest is not None and latest.ended_at is None
        manageable = access.can_manage and session.lifecycle == "OPEN"
        included = [s for s in sources if s["included"]]
        actions: list[str] = []
        if manageable and not active:
            actions += ["edit_inputs", "rename", "archive"]
            if view_state == "DRAFT" and included:
                actions.append("prepare")
            if latest is not None and latest.input_revision == session.input_revision:
                if view_state == "PREPARATION_FAILED" or (
                    latest.ended_at is not None
                    and self.run_state(self._job(latest)) == "CANCELLED"
                ):
                    actions = [a for a in actions if a != "prepare"] + ["retry"]
        if manageable and active:
            actions.append("cancel")
        if access.can_manage and session.lifecycle == "ARCHIVED":
            actions.append("restore")
        if access.can_content:
            actions.append("read_content")
        view = {
            "id": str(session.id),
            "label": session.title
            if access.can_content
            else f"Session {str(session.id)[:8]}",
            "lifecycle": session.lifecycle,
            "view": view_state,
            "row_version": session.row_version,
            "input_revision": session.input_revision,
            "source_count": len(included),
            "total_source_count": len(sources),
            "sources": sources,
            "current_run": self._run_dict(latest, access) if latest else None,
            "allowed_actions": actions,
            "created_at": _iso(session.created_at),
            "updated_at": _iso(session.updated_at),
        }
        if access.can_content:
            view["title"] = session.title
        return view

    def list_sessions(
        self,
        access: Access,
        *,
        archived: bool,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        query = self.db.query(OnboardingSession)
        if archived:
            query = query.filter(OnboardingSession.lifecycle == "ARCHIVED")
        else:
            query = query.filter(OnboardingSession.lifecycle == "OPEN")
        total = query.count()
        rows = (
            query.order_by(
                OnboardingSession.updated_at.desc(), OnboardingSession.id.asc()
            )
            .limit(limit)
            .offset(offset)
            .all()
        )
        items = []
        for row in rows:
            full = self.session_view(row.id, access)
            items.append(
                {
                    key: full[key]
                    for key in (
                        "id",
                        "label",
                        "lifecycle",
                        "view",
                        "row_version",
                        "source_count",
                        "updated_at",
                    )
                }
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def list_runs(
        self, session_id: uuid.UUID, access: Access, *, limit: int, offset: int
    ) -> dict[str, Any]:
        self._get_session(session_id)
        query = self.db.query(OnboardingRun).filter(
            OnboardingRun.session_id == session_id
        )
        total = query.count()
        rows = (
            query.order_by(OnboardingRun.run_number.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return {
            "items": [self._run_dict(r, access) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
