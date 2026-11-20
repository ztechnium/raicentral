"""HTTP contract for onboarding intake sessions (Step 02).

Routes live under ``/v1/productization/onboarding/sessions``. Permissions come
from the exact server permission list: ``sdk:read`` for sanitized progress,
``sdk:read`` + ``onboarding:sources:read`` for content, and additionally
``sdk:manage`` for every mutation.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Iterator
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from rai_gateway.core.authorization import require_permission
from rai_gateway.core.config import settings
from rai_gateway.core.database import get_db
from rai_gateway.core.errors import ErrorCode, RaiException
from rai_gateway.core.tenant_context import TenantContext
from rai_gateway.onboarding.service import (
    SOURCES_PERMISSION,
    Access,
    IntakeError,
    IntakeService,
)
from rai_gateway.onboarding.storage import FAMILY_CONTENT_TYPE, get_source_store

PREFIX = "/v1/productization/onboarding/sessions"
router = APIRouter(prefix=PREFIX, tags=["onboarding-intake"])

_KEY_RE = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")
_READ_CHUNK = 1024 * 1024


# --------------------------------------------------------------------- guards
def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def idempotency_key(
    key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    if not key or not _KEY_RE.match(key):
        raise IntakeError(
            422,
            "IDEMPOTENCY_KEY_REQUIRED",
            "An Idempotency-Key header of 8-128 safe characters is required",
        )
    return key


def _deny(permission: str) -> RaiException:
    return RaiException(
        error_code=ErrorCode.NOT_ALLOWED,
        message="Missing required permission",
        details={"required_permission": permission},
    )


def read_access(
    tenant: TenantContext = Depends(require_permission("sdk:read")),
) -> tuple[TenantContext, Access]:
    return tenant, Access.from_tenant(tenant)


def content_access(
    pair: tuple[TenantContext, Access] = Depends(read_access),
) -> tuple[TenantContext, Access]:
    if not pair[1].can_content:
        raise _deny(SOURCES_PERMISSION)
    return pair


def manage_access(
    tenant: TenantContext = Depends(require_permission("sdk:manage")),
) -> tuple[TenantContext, Access]:
    access = Access.from_tenant(tenant)
    if not access.can_manage:
        raise _deny(SOURCES_PERMISSION)
    return tenant, access


def _service(db: Session, tenant: TenantContext) -> IntakeService:
    return IntakeService(db, get_source_store(), actor=tenant.user_id)


# -------------------------------------------------------------------- bodies
class CreateSessionBody(BaseModel):
    title: str


class UpdateSessionBody(BaseModel):
    title: str
    expected_row_version: int = Field(ge=1)


class VersionBody(BaseModel):
    expected_row_version: int = Field(ge=1)


class TextSourceBody(BaseModel):
    role: str = Field(pattern="^(USER_PROMPT|SUPPORTING_MATERIAL)$")
    text: str
    title: str | None = Field(default=None, max_length=120)
    expected_row_version: int = Field(ge=1)


class TextRevisionBody(BaseModel):
    text: str
    title: str | None = Field(default=None, max_length=120)
    expected_row_version: int = Field(ge=1)


class SourcePatchBody(BaseModel):
    included: bool
    expected_row_version: int = Field(ge=1)


class RunRequestBody(BaseModel):
    expected_row_version: int = Field(ge=1)
    requested_input_revision: int = Field(ge=0)


# ------------------------------------------------------------------ sessions
@router.post("", status_code=201)
def create_session(
    body: CreateSessionBody,
    response: Response,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).create_session(
        title=body.title, idempotency_key=key, access=access
    )


@router.get("")
def list_sessions(
    response: Response,
    archived: bool = Query(default=False),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(read_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).list_sessions(
        access, archived=archived, limit=limit, offset=offset
    )


@router.get("/{session_id}")
def get_session(
    session_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(read_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).session_view(session_id, access)


@router.patch("/{session_id}")
def update_session(
    session_id: UUID,
    body: UpdateSessionBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).update_title(
        session_id,
        title=body.title,
        expected_row_version=body.expected_row_version,
        access=access,
    )


@router.post("/{session_id}/archive")
def archive_session(
    session_id: UUID,
    body: VersionBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).set_lifecycle(
        session_id,
        archive=True,
        expected_row_version=body.expected_row_version,
        access=access,
    )


@router.post("/{session_id}/restore")
def restore_session(
    session_id: UUID,
    body: VersionBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).set_lifecycle(
        session_id,
        archive=False,
        expected_row_version=body.expected_row_version,
        access=access,
    )


# ------------------------------------------------------------------- sources
async def _read_upload(request: Request) -> tuple[UploadFile, int]:
    """Parse the (already size-bounded) multipart body: exactly one file part."""
    try:
        form = await request.form(max_files=1, max_fields=8)
    except Exception as exc:  # noqa: BLE001 - malformed / extra parts
        raise IntakeError(
            400, "INVALID_UPLOAD", "Send exactly one file part with the upload"
        ) from exc
    files = [v for v in form.multi_items() if isinstance(v[1], UploadFile)]
    if len(files) != 1 or files[0][0] != "file":
        raise IntakeError(
            400, "INVALID_UPLOAD", "Send exactly one file part named 'file'"
        )
    raw_version = form.get("expected_row_version")
    try:
        version = int(str(raw_version))
        if version < 1:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise IntakeError(
            422, "EXPECTED_ROW_VERSION_REQUIRED", "expected_row_version is required"
        ) from exc
    return files[0][1], version


def _chunks(upload: UploadFile) -> Iterator[bytes]:
    while True:
        block = upload.file.read(_READ_CHUNK)
        if not block:
            return
        yield block


def _upload_deadline() -> float:
    return time.monotonic() + settings.onboarding_upload_timeout_seconds


@router.post("/{session_id}/sources/files", status_code=201)
async def upload_file_source(
    session_id: UUID,
    request: Request,
    response: Response,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    upload, version = await _read_upload(request)

    def work() -> dict[str, Any]:
        service = _service(db, tenant)
        service._get_session(session_id)
        staged, family = service.stage_file(
            _chunks(upload),
            filename=upload.filename or "",
            declared_type=upload.content_type,
            deadline=_upload_deadline(),
        )
        return service.add_source(
            session_id,
            kind="FILE",
            role="SUPPORTING_MATERIAL",
            staged=staged,
            family=family,
            display_name=(upload.filename or "")[:255],
            declared_type=upload.content_type,
            expected_row_version=version,
            idempotency_key=key,
            access=access,
        )

    return await run_in_threadpool(work)


@router.post("/{session_id}/sources/text", status_code=201)
def add_text_source(
    session_id: UUID,
    body: TextSourceBody,
    response: Response,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    service = _service(db, tenant)
    service._get_session(session_id)
    staged = service.stage_text(body.text)
    name = "Prompt" if body.role == "USER_PROMPT" else (body.title or "Pasted text")
    return service.add_source(
        session_id,
        kind="TEXT",
        role=body.role,
        staged=staged,
        family="TEXT",
        display_name=name,
        declared_type="text/plain",
        expected_row_version=body.expected_row_version,
        idempotency_key=key,
        access=access,
    )


@router.post("/{session_id}/sources/{source_id}/revisions/file", status_code=201)
async def replace_file_source(
    session_id: UUID,
    source_id: UUID,
    request: Request,
    response: Response,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    upload, version = await _read_upload(request)

    def work() -> dict[str, Any]:
        service = _service(db, tenant)
        service._get_session(session_id)
        staged, family = service.stage_file(
            _chunks(upload),
            filename=upload.filename or "",
            declared_type=upload.content_type,
            deadline=_upload_deadline(),
        )
        return service.add_revision(
            session_id,
            source_id,
            kind="FILE",
            staged=staged,
            family=family,
            display_name=(upload.filename or "")[:255],
            declared_type=upload.content_type,
            expected_row_version=version,
            idempotency_key=key,
            access=access,
        )

    return await run_in_threadpool(work)


@router.post("/{session_id}/sources/{source_id}/revisions/text", status_code=201)
def revise_text_source(
    session_id: UUID,
    source_id: UUID,
    body: TextRevisionBody,
    response: Response,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    service = _service(db, tenant)
    service._get_session(session_id)
    staged = service.stage_text(body.text)
    return service.add_revision(
        session_id,
        source_id,
        kind="TEXT",
        staged=staged,
        family="TEXT",
        display_name=body.title,
        declared_type="text/plain",
        expected_row_version=body.expected_row_version,
        idempotency_key=key,
        access=access,
    )


@router.patch("/{session_id}/sources/{source_id}")
def patch_source(
    session_id: UUID,
    source_id: UUID,
    body: SourcePatchBody,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).set_included(
        session_id,
        source_id,
        included=body.included,
        expected_row_version=body.expected_row_version,
        access=access,
    )


@router.get("/{session_id}/sources/{source_id}/revisions")
def list_revisions(
    session_id: UUID,
    source_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(content_access),
):
    tenant, _ = pair
    _no_store(response)
    return _service(db, tenant).list_revisions(session_id, source_id)


@router.get("/{session_id}/sources/{source_id}/revisions/{revision_id}/content")
def download_revision(
    session_id: UUID,
    source_id: UUID,
    revision_id: UUID,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(content_access),
):
    tenant, _ = pair
    revision, handle = _service(db, tenant).open_content(
        session_id, source_id, revision_id
    )

    def body() -> Iterator[bytes]:
        try:
            while True:
                block = handle.read(_READ_CHUNK)
                if not block:
                    return
                yield block
        finally:
            handle.close()

    family = revision.media_family
    media_type = FAMILY_CONTENT_TYPE.get(family, "application/octet-stream")
    if media_type.startswith("text/"):
        media_type += "; charset=utf-8"
    name = revision.display_name or "source"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "source"
    return StreamingResponse(
        body(),
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Length": str(int(revision.byte_length)),
        },
    )


# ---------------------------------------------------------------------- runs
@router.post("/{session_id}/prepare", status_code=202)
def prepare_sources(
    session_id: UUID,
    body: RunRequestBody,
    response: Response,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    result = _service(db, tenant).prepare(
        session_id,
        expected_row_version=body.expected_row_version,
        requested_input_revision=body.requested_input_revision,
        idempotency_key=key,
        access=access,
    )
    if result["run"]["state"] == "SUCCEEDED":
        response.status_code = 200
    return result


@router.get("/{session_id}/runs")
def list_runs(
    session_id: UUID,
    response: Response,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(read_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).list_runs(
        session_id, access, limit=limit, offset=offset
    )


@router.get("/{session_id}/runs/{run_id}")
def get_run(
    session_id: UUID,
    run_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(read_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).run_view(session_id, run_id, access)


@router.post("/{session_id}/runs/{run_id}/cancel")
def cancel_run(
    session_id: UUID,
    run_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).cancel(session_id, run_id, access=access)


@router.post("/{session_id}/runs/{run_id}/retry", status_code=202)
def retry_run(
    session_id: UUID,
    run_id: UUID,
    body: RunRequestBody,
    response: Response,
    key: str = Depends(idempotency_key),
    db: Session = Depends(get_db),
    pair: tuple[TenantContext, Access] = Depends(manage_access),
):
    tenant, access = pair
    _no_store(response)
    return _service(db, tenant).retry(
        session_id,
        run_id,
        expected_row_version=body.expected_row_version,
        requested_input_revision=body.requested_input_revision,
        idempotency_key=key,
        access=access,
    )


# ------------------------------------------------------ errors and body limits
async def intake_error_handler(_request: Request, exc: IntakeError) -> JSONResponse:
    return JSONResponse(
        exc.to_dict(), status_code=exc.status, headers={"Cache-Control": "no-store"}
    )


class _BodyLimit(BaseException):
    """Raised inside ``receive`` so no handler or parser sees an over-limit body."""

    def __init__(self, code: str) -> None:
        self.code = code


class OnboardingBodyLimitMiddleware:
    """Bound actual request bytes and duration before multipart parsing/spooling."""

    _UPLOAD = re.compile(r"/(sources/files|revisions/file)$")
    _TEXT = re.compile(r"/(sources/text|revisions/text)$")

    def __init__(self, app) -> None:
        self.app = app

    def _limit(self, path: str) -> int:
        overhead = settings.onboarding_multipart_overhead_bytes
        if self._UPLOAD.search(path):
            return settings.onboarding_max_file_bytes + overhead
        if self._TEXT.search(path):
            return settings.onboarding_max_text_chars * 6 + overhead
        return overhead

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("POST", "PUT", "PATCH")
            or not scope["path"].startswith(PREFIX)
        ):
            await self.app(scope, receive, send)
            return
        limit = self._limit(scope["path"])
        is_upload = bool(self._UPLOAD.search(scope["path"]))
        timeout = settings.onboarding_upload_timeout_seconds if is_upload else 60.0
        deadline = time.monotonic() + timeout
        state = {"received": 0, "started": False}

        content_length = dict(scope.get("headers") or []).get(b"content-length")
        if content_length and content_length.isdigit() and int(content_length) > limit:
            await self._reject(scope, receive, send, "SOURCE_TOO_LARGE")
            return

        async def guarded_receive():
            remaining = max(0.001, deadline - time.monotonic())
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise _BodyLimit("UPLOAD_TIMEOUT") from exc
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > limit:
                    raise _BodyLimit("SOURCE_TOO_LARGE")
            return message

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except _BodyLimit as exc:
            if state["started"]:
                raise RuntimeError("Body limit reached after response started") from exc
            await self._reject(scope, receive, send, exc.code)

    @staticmethod
    async def _reject(scope, receive, send, code: str) -> None:
        status = 408 if code == "UPLOAD_TIMEOUT" else 413
        message = (
            "Upload took too long"
            if code == "UPLOAD_TIMEOUT"
            else "Request exceeds the maximum allowed size"
        )
        response = JSONResponse(
            IntakeError(status, code, message).to_dict(),
            status_code=status,
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


def register(app) -> None:
    app.include_router(router)
    app.add_exception_handler(IntakeError, intake_error_handler)
    app.add_middleware(OnboardingBodyLimitMiddleware)
