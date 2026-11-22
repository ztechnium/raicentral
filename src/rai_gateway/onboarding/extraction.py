"""Extraction orchestration (Step 03): plans, identity, fenced publication, citations.

Extraction is deterministic and runs with AI disabled. Parsing happens in a killable
child process (see ``parsers/runner.py``). Nothing here calls a provider or writes
registry, policy, rule, budget, approval, credential or runtime-evidence records.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.database import AdminAudit
from rai_gateway.models.onboarding import (
    OnboardingExtractedBlock,
    OnboardingRun,
    OnboardingRunSource,
    OnboardingSession,
    OnboardingSource,
    OnboardingSourceExtraction,
    OnboardingSourceRevision,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.parsers import Limits, effective_options
from rai_gateway.onboarding.parsers.contracts import (
    ATTENTION_OUTCOMES,
    EXTRACTION_OUTCOMES,
    NOT_PROCESSED,
    digest_of,
)
from rai_gateway.onboarding.parsers.profile import parser_profile, supported_options
from rai_gateway.onboarding.parsers.runner import ParserStopped, run_parser
from rai_gateway.onboarding.service import (
    EXTRACT_JOB_TYPE,
    MANIFEST_VERSION,
    STAGE_EXTRACT,
    STAGE_PREPARE,
    Access,
    IntakeError,
    IntakeService,
    _iso,
    canonical_digest,
    sha256_hex,
    utcnow,
)
from rai_gateway.onboarding.storage import (
    OnboardingSourceStore,
    SourceIntegrityError,
    SourceStorageError,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue

NOT_CACHED_REASONS = frozenset({"PARSER_CRASHED"})  # transient/unattributable: run-only


# --------------------------------------------------------------------- policy
def configured_limits() -> Limits:
    s = settings
    return Limits(
        wall_seconds=s.onboarding_parser_wall_seconds,
        memory_bytes=s.onboarding_parser_memory_bytes,
        pdf_pages=s.onboarding_pdf_max_pages,
        zip_entries=s.onboarding_ooxml_max_entries,
        zip_total_bytes=s.onboarding_ooxml_max_total_bytes,
        zip_member_bytes=s.onboarding_ooxml_max_member_bytes,
        zip_ratio=s.onboarding_ooxml_max_ratio,
        xlsx_sheets=s.onboarding_xlsx_max_sheets,
        max_rows=s.onboarding_tabular_max_rows,
        max_columns=s.onboarding_tabular_max_columns,
        cells_visited=s.onboarding_max_cells_visited,
        nonempty_cells=s.onboarding_max_nonempty_cells,
        output_bytes=s.onboarding_extract_output_bytes_per_source,
        run_output_bytes=s.onboarding_extract_output_bytes_per_run,
    )


def current_profile() -> dict[str, Any]:
    return parser_profile(configured_limits())


def family_of(kind: str, media_family: str) -> str:
    return "TEXT" if kind == "TEXT" else media_family


def identity_digest(
    *,
    source_revision_id: str,
    sha256: str,
    profile: dict[str, Any],
    effective: dict[str, Any],
    policy_digest: str,
) -> str:
    """The complete extraction identity used for cache reuse (never filename/hash alone)."""
    return digest_of(
        {
            "source_revision_id": source_revision_id,
            "original_sha256": sha256,
            "build": profile["build"],
            "dependencies": profile["dependencies"],
            "schema_version": profile["schema_version"],
            "normalization_version": profile["normalization_version"],
            "effective_options": effective,
            "policy_digest": policy_digest,
        }
    )


def build_plan(prep_run: OnboardingRun) -> dict[str, Any]:
    """Freeze the extraction plan from an exact preparation manifest (version >= 2)."""
    profile = current_profile()
    policy_digest = profile["limits_digest"]
    entries = []
    for entry in prep_run.manifest["entries"]:
        family = family_of(entry["kind"], entry["media_family"])
        effective = effective_options(
            family, entry["kind"], entry.get("parsing_options")
        )
        entries.append(
            {
                "source_id": entry["source_id"],
                "source_revision_id": entry["source_revision_id"],
                "sha256": entry["sha256"],
                "kind": entry["kind"],
                "media_family": family,
                "effective_options": effective,
                "options_digest": digest_of(effective),
                "identity_digest": identity_digest(
                    source_revision_id=entry["source_revision_id"],
                    sha256=entry["sha256"],
                    profile=profile,
                    effective=effective,
                    policy_digest=policy_digest,
                ),
            }
        )
    plan = {
        "version": 1,
        "preparation_run_id": str(prep_run.id),
        "manifest_digest": prep_run.manifest_digest,
        "parser_fingerprint": profile["fingerprint"],
        "policy_digest": policy_digest,
        "entries": entries,
    }
    return plan


def plan_digest(plan: dict[str, Any]) -> str:
    return digest_of(plan)


def finalize_pending_sources(db: Session, run_id: uuid.UUID, reason: str) -> int:
    """Account for every unattempted source: never leave PENDING under a terminal job."""
    rows = (
        db.query(OnboardingRunSource)
        .filter(
            OnboardingRunSource.run_id == run_id, OnboardingRunSource.state == "PENDING"
        )
        .all()
    )
    for row in rows:
        row.state = "FINAL"
        row.outcome = NOT_PROCESSED
        row.reason = reason
        row.finished_at = utcnow()
    return len(rows)


# --------------------------------------------------------------- API-side logic
class ExtractionService:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    # ----------------------------------------------------------- eligibility
    def _eligible_preparation(
        self, session: OnboardingSession, prep_run_id: uuid.UUID
    ) -> OnboardingRun:
        prep = self.svc._get_run(session.id, prep_run_id)
        if prep.stage != STAGE_PREPARE:
            raise IntakeError(
                422, "PREPARATION_REQUIRED", "A preparation run is required"
            )
        latest = self.svc._latest_run(session.id, STAGE_PREPARE)
        if latest is None or latest.id != prep.id:
            raise IntakeError(
                409,
                "PREPARATION_STALE",
                "A newer preparation run exists. Use the current one.",
                details={"current_run_id": str(latest.id) if latest else None},
            )
        job = self.svc._job(prep)
        if (
            job.status != BackgroundJobStatus.SUCCEEDED.value
            or prep.input_revision != session.input_revision
        ):
            raise IntakeError(
                409,
                "PREPARATION_REQUIRED",
                "Prepare the current sources before extracting them",
            )
        if (prep.manifest or {}).get("version", 1) < MANIFEST_VERSION:
            raise IntakeError(
                409,
                "PREPARATION_REQUIRED",
                "This preparation predates parsing options. Prepare the sources again.",
            )
        return prep

    # -------------------------------------------------------------- request
    def extract(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        requested_input_revision: int,
        preparation_run_id: uuid.UUID,
        expected_parser_fingerprint: str,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        svc = self.svc
        scope = svc._scope("extract", session_id)
        fingerprint = svc._fingerprint(
            {
                "op": "extract",
                "requested_input_revision": requested_input_revision,
                "preparation_run_id": str(preparation_run_id),
                "parser_fingerprint": expected_parser_fingerprint,
            }
        )
        svc._get_session(session_id)
        receipt = svc._lookup_receipt(scope, idempotency_key, fingerprint)
        if receipt is not None:
            return svc._run_result(
                session_id, receipt.resource_id, access, replayed=True
            )
        session = svc._get_session(session_id, lock=True)
        try:
            receipt = svc._lookup_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                self.db.rollback()
                return svc._run_result(
                    session_id, receipt.resource_id, access, replayed=True
                )
            svc._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(
                    409,
                    "REVISION_CONFLICT",
                    "Inputs changed since this request was made",
                    details={"current_input_revision": session.input_revision},
                )
            prep = self._eligible_preparation(session, preparation_run_id)
            if expected_parser_fingerprint != current_profile()["fingerprint"]:
                raise IntakeError(
                    409,
                    "PARSER_PROFILE_MISMATCH",
                    "The parser configuration changed. Reload and request again.",
                    details={"current_fingerprint": current_profile()["fingerprint"]},
                )
            plan = build_plan(prep)
            digest = plan_digest(plan)
            latest = svc._latest_run(session.id, STAGE_EXTRACT)
            if latest is not None and latest.plan_digest == digest:
                job = svc._job(latest)
                if (
                    latest.ended_at is None
                    or job.status == BackgroundJobStatus.SUCCEEDED.value
                ):
                    run = latest  # converge on the exact matching plan
                else:
                    raise IntakeError(
                        409,
                        "RETRY_REQUIRED",
                        "The last extraction of this exact plan did not succeed. Retry it.",
                        details={"run_id": str(latest.id)},
                    )
            else:
                if svc._active_run(session.id) is not None:
                    raise IntakeError(
                        409, "PREPARATION_ACTIVE", "A run is already active"
                    )
                run = self._create_run(session, prep, plan, digest, retry_of=None)
                svc._audit(
                    "onboarding.run.extract",
                    {"session_id": str(session.id), "run_id": str(run.id)},
                    "EXTRACTION_REQUESTED",
                )
            svc._add_receipt(scope, idempotency_key, fingerprint, "RUN", run.id)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return svc._run_result(session_id, run.id, access, replayed=False)

    def _create_run(
        self,
        session: OnboardingSession,
        prep: OnboardingRun,
        plan: dict[str, Any],
        digest: str,
        *,
        retry_of: OnboardingRun | None,
    ) -> OnboardingRun:
        run_id = uuid.uuid4()
        number = (
            self.db.query(func.max(OnboardingRun.run_number))
            .filter(OnboardingRun.session_id == session.id)
            .scalar()
            or 0
        ) + 1
        job = JobQueue(self.db).enqueue(
            job_type=EXTRACT_JOB_TYPE,
            payload={
                "run_id": str(run_id),
                "session_id": str(session.id),
                "input_revision": session.input_revision,
                "plan_digest": digest,
                "preparation_run_id": str(prep.id),
            },
            idempotency_key=f"onboarding:extract:{run_id}",
            created_by=self.svc.actor,
            max_attempts=settings.onboarding_job_max_attempts,
            allow_reserved=True,
        )
        run = OnboardingRun(
            id=run_id,
            session_id=session.id,
            run_number=number,
            stage=STAGE_EXTRACT,
            input_revision=session.input_revision,
            manifest=prep.manifest,
            manifest_digest=prep.manifest_digest,
            preparation_run_id=prep.id,
            plan=plan,
            plan_digest=digest,
            job_id=uuid.UUID(job["id"]),
            retry_of_run_id=retry_of.id if retry_of else None,
            created_by=self.svc.actor,
        )
        self.db.add(run)
        self.db.flush()
        for entry in plan["entries"]:
            self.db.add(
                OnboardingRunSource(
                    run_id=run_id,
                    source_id=uuid.UUID(entry["source_id"]),
                    source_revision_id=uuid.UUID(entry["source_revision_id"]),
                    state="PENDING",
                )
            )
        self.db.flush()
        return run

    def retry_run(
        self, session: OnboardingSession, run: OnboardingRun
    ) -> OnboardingRun:
        """Linked retry of a failed/cancelled extraction, only for the same exact plan."""
        prep = self._eligible_preparation(session, run.preparation_run_id)
        plan = build_plan(prep)
        digest = plan_digest(plan)
        if digest != run.plan_digest or run.input_revision != session.input_revision:
            raise IntakeError(
                409,
                "INPUTS_CHANGED",
                "The inputs or parser configuration changed. Request a new extraction.",
            )
        new_run = self._create_run(session, prep, plan, digest, retry_of=run)
        self.svc._audit(
            "onboarding.run.retry",
            {
                "session_id": str(session.id),
                "run_id": str(new_run.id),
                "retry_of_run_id": str(run.id),
            },
            "RETRY_REQUESTED",
        )
        return new_run

    # -------------------------------------------------------------- summaries
    def summary(self, session: OnboardingSession, access: Access) -> dict[str, Any]:
        prep = self.svc._latest_run(session.id, STAGE_PREPARE)
        ext = self.svc._latest_run(session.id, STAGE_EXTRACT)
        current = (
            ext
            if ext is not None
            and ext.input_revision == session.input_revision
            and prep is not None
            and ext.preparation_run_id == prep.id
            else None
        )
        out: dict[str, Any] = {
            "state": "NOT_STARTED",
            "run_id": None,
            "current": False,
            "sources": [],
            "counts": {},
            "attention_count": 0,
            "all_sources_accounted": False,
            "coverage_acceptable": False,
        }
        if ext is None:
            return out
        job = self.svc._job(ext)
        out["run_id"] = str(ext.id)
        out["current"] = current is not None
        out["state"] = {
            "QUEUED": "QUEUED",
            "RETRY_SCHEDULED": "EXTRACTING",
            "PREPARING": "EXTRACTING",
            "SUCCEEDED": "EXTRACTED",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
        }[self.svc.run_state(job)]
        rows = (
            self.db.query(OnboardingRunSource)
            .filter(OnboardingRunSource.run_id == ext.id)
            .order_by(OnboardingRunSource.source_id)
            .all()
        )
        counts: dict[str, int] = {}
        sources = []
        for index, row in enumerate(rows, 1):
            key = row.outcome or "PENDING"
            counts[key] = counts.get(key, 0) + 1
            item: dict[str, Any] = {
                "label": f"Source {index}",
                "state": row.state,
                "outcome": row.outcome,
                "block_count": row.block_count,
            }
            if access.can_content:
                item.update(
                    source_id=str(row.source_id),
                    source_revision_id=str(row.source_revision_id),
                    extraction_id=str(row.extraction_id) if row.extraction_id else None,
                    reason=row.reason,
                    coverage=row.coverage,
                )
            sources.append(item)
        entries = (ext.manifest or {}).get("entries", [])
        accounted = (
            len(rows) == len(entries)
            and all(r.state == "FINAL" for r in rows)
            and counts.get("PENDING", 0) == 0
        )
        attention = sum(
            c for k, c in counts.items() if k in ATTENTION_OUTCOMES or k == "PENDING"
        )
        out.update(
            sources=sources,
            counts=counts,
            attention_count=attention,
            all_sources_accounted=accounted,
            # Eligibility for later generation: current run, all sources accounted, all COMPLETE.
            coverage_acceptable=bool(
                current is not None
                and out["state"] == "EXTRACTED"
                and accounted
                and attention == 0
            ),
        )
        return out

    # ------------------------------------------------------------- listings
    def list_extractions(
        self, session_id: uuid.UUID, access: Access, *, limit: int, offset: int
    ) -> dict[str, Any]:
        self.svc._get_session(session_id)
        query = (
            self.db.query(
                OnboardingSourceExtraction, OnboardingSourceRevision, OnboardingSource
            )
            .join(
                OnboardingSourceRevision,
                OnboardingSourceRevision.id
                == OnboardingSourceExtraction.source_revision_id,
            )
            .join(
                OnboardingSource,
                OnboardingSource.id == OnboardingSourceRevision.source_id,
            )
            .filter(OnboardingSourceRevision.session_id == session_id)
        )
        total = query.count()
        rows = (
            query.order_by(
                OnboardingSourceExtraction.created_at.desc(),
                OnboardingSourceExtraction.id,
            )
            .limit(limit)
            .offset(offset)
            .all()
        )
        items = []
        for ext, rev, source in rows:
            cov = ext.coverage or {}
            item: dict[str, Any] = {
                "id": str(ext.id),
                "outcome": ext.outcome,
                "block_count": ext.block_count,
                "coverage": {k: cov.get(k) for k in ("unit", "total", "extracted")},
                "created_at": _iso(ext.created_at),
                "is_current_input": bool(
                    source.included and source.current_revision_id == rev.id
                ),
            }
            if access.can_content:
                item.update(
                    source_id=str(source.id),
                    source_revision_id=str(rev.id),
                    revision_number=rev.revision_number,
                    display_name=rev.display_name,
                    original_sha256=ext.original_sha256,
                    reason=ext.reason,
                    coverage=cov,
                    warnings=ext.warnings,
                    extractor_build=ext.extractor_build,
                    dependency_versions=ext.dependency_versions,
                    options=ext.options_snapshot,
                    normalization=ext.normalization,
                )
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def _extraction_chain(
        self, session_id: uuid.UUID, extraction_id: uuid.UUID
    ) -> tuple[OnboardingSourceExtraction, OnboardingSourceRevision, OnboardingSource]:
        self.svc._get_session(session_id)
        row = (
            self.db.query(
                OnboardingSourceExtraction, OnboardingSourceRevision, OnboardingSource
            )
            .join(
                OnboardingSourceRevision,
                OnboardingSourceRevision.id
                == OnboardingSourceExtraction.source_revision_id,
            )
            .join(
                OnboardingSource,
                OnboardingSource.id == OnboardingSourceRevision.source_id,
            )
            .filter(
                OnboardingSourceExtraction.id == extraction_id,
                OnboardingSourceRevision.session_id == session_id,
            )
            .one_or_none()
        )
        if row is None:
            raise IntakeError(404, "EXTRACTION_NOT_FOUND", "Extraction not found")
        return row

    def list_blocks(
        self,
        session_id: uuid.UUID,
        extraction_id: uuid.UUID,
        *,
        after: int,
        limit: int,
    ) -> dict[str, Any]:
        ext, rev, source = self._extraction_chain(session_id, extraction_id)
        rows = (
            self.db.query(OnboardingExtractedBlock)
            .filter(
                OnboardingExtractedBlock.extraction_id == ext.id,
                OnboardingExtractedBlock.ordinal > after,
            )
            .order_by(OnboardingExtractedBlock.ordinal)
            .limit(limit + 1)
            .all()
        )
        page = rows[:limit]
        return {
            "extraction": {
                "id": str(ext.id),
                "source_id": str(source.id),
                "source_revision_id": str(rev.id),
                "revision_number": rev.revision_number,
                "original_sha256": ext.original_sha256,
                "outcome": ext.outcome,
                "reason": ext.reason,
                "coverage": ext.coverage,
                "warnings": ext.warnings,
                "extractor_build": ext.extractor_build,
                "options": ext.options_snapshot,
                "normalization": ext.normalization,
                "block_count": ext.block_count,
                "is_current_input": bool(
                    source.included and source.current_revision_id == rev.id
                ),
            },
            "items": [
                {
                    "id": str(b.id),
                    "ordinal": b.ordinal,
                    "kind": b.kind,
                    "text": b.text,
                    "cell_data": b.cell_data,
                    "locator": b.locator,
                    "content_digest": b.content_digest,
                    "warnings": b.warnings,
                }
                for b in page
            ],
            "next_after": page[-1].ordinal if len(rows) > limit and page else None,
        }

    def citation(
        self,
        session_id: uuid.UUID,
        block_id: uuid.UUID,
        *,
        start: int | None,
        end: int | None,
    ) -> dict[str, Any]:
        self.svc._get_session(session_id)
        row = (
            self.db.query(
                OnboardingExtractedBlock,
                OnboardingSourceExtraction,
                OnboardingSourceRevision,
                OnboardingSource,
            )
            .join(
                OnboardingSourceExtraction,
                OnboardingSourceExtraction.id == OnboardingExtractedBlock.extraction_id,
            )
            .join(
                OnboardingSourceRevision,
                OnboardingSourceRevision.id
                == OnboardingSourceExtraction.source_revision_id,
            )
            .join(
                OnboardingSource,
                OnboardingSource.id == OnboardingSourceRevision.source_id,
            )
            .filter(
                OnboardingExtractedBlock.id == block_id,
                OnboardingSourceRevision.session_id == session_id,
            )
            .one_or_none()
        )
        if row is None:
            raise IntakeError(404, "BLOCK_NOT_FOUND", "Block not found")
        block, ext, rev, source = row
        text = block.text
        if (start is None) != (end is None):
            raise IntakeError(
                422, "INVALID_RANGE", "Provide both start and end, or neither"
            )
        if start is None:
            start, end = 0, len(text)
        assert end is not None
        # Python str indexes are Unicode code points: the contract's offset unit.
        if not (0 <= start < end <= len(text)):
            raise IntakeError(
                422,
                "INVALID_RANGE",
                "Range is outside the stored block text",
                details={"length": len(text)},
            )
        current = bool(source.included and source.current_revision_id == rev.id)
        return {
            "source_id": str(source.id),
            "source_revision_id": str(rev.id),
            "revision_number": rev.revision_number,
            "source_sha256": rev.sha256,
            "display_name": rev.display_name,
            "extraction_id": str(ext.id),
            "extractor_build": ext.extractor_build,
            "options_digest": ext.options_digest,
            "block_id": str(block.id),
            "block_kind": block.kind,
            "locator": block.locator,
            "cell_data": block.cell_data,
            "text_range": {"start": start, "end": end, "unit": "UNICODE_CODE_POINT"},
            "excerpt": text[start:end],
            "block_text": text,
            "integrity_ok": sha256_hex(text) == block.content_digest,
            "is_current_input": current,
            "historical": not current,
        }


# ------------------------------------------------------------------ worker side
class _Lost(Exception):
    pass


class _Ownership:
    """Throttled ownership/cancellation probe plus lease renewal for the parent."""

    def __init__(self, session_factory, job_id: uuid.UUID, token: str) -> None:
        self.factory, self.job_id, self.token = session_factory, job_id, token
        self._last_check = 0.0
        self._last_renew = time.monotonic()
        self.lost = False

    def ok(self) -> bool:
        now = time.monotonic()
        if self.lost:
            return False
        if now - self._last_check < 1.0:
            return True
        self._last_check = now
        db = self.factory()
        try:
            if now - self._last_renew >= settings.onboarding_job_renew_seconds:
                held = JobQueue(db).renew_lease(
                    job_id=self.job_id,
                    lease_token=self.token,
                    lease_seconds=settings.onboarding_job_lease_seconds,
                )
                db.commit()
                self._last_renew = now
            else:
                job = db.get(BackgroundJob, self.job_id)
                held = bool(
                    job is not None
                    and job.status == BackgroundJobStatus.RUNNING.value
                    and job.lease_token == self.token
                )
        finally:
            db.close()
        self.lost = not held
        return held


def _validate_binding(db: Session, job: BackgroundJob) -> OnboardingRun:
    from rai_gateway.onboarding.preparation import TerminalError

    payload = job.payload if isinstance(job.payload, dict) else {}
    try:
        run_id = uuid.UUID(str(payload.get("run_id")))
    except ValueError as exc:
        raise TerminalError("INVALID_RUN_BINDING") from exc
    run = db.query(OnboardingRun).filter(OnboardingRun.id == run_id).one_or_none()
    if (
        run is None
        or run.job_id != job.id
        or job.job_type != EXTRACT_JOB_TYPE
        or run.stage != STAGE_EXTRACT
        or run.plan is None
        or payload.get("session_id") != str(run.session_id)
        or payload.get("input_revision") != run.input_revision
        or payload.get("plan_digest") != run.plan_digest
        or payload.get("preparation_run_id") != str(run.preparation_run_id)
        or plan_digest(run.plan) != run.plan_digest
    ):
        raise TerminalError("INVALID_RUN_BINDING")
    return run


def _publish_source(
    db: Session,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    token: str,
    entry: dict[str, Any],
    result: dict[str, Any] | None,
    *,
    reuse_extraction_id: uuid.UUID | None,
    final_outcome: str | None = None,
    final_reason: str | None = None,
    cacheable: bool = True,
) -> bool:
    """Fenced per-source publication: extraction + blocks + run-source row, atomically."""
    from rai_gateway.onboarding.preparation import _is_latest, _lock_owned

    try:
        owned = _lock_owned(db, run_id, job_id, token)
        if owned is None:
            db.rollback()
            return False
        run, session, _job = owned
        if (
            session.input_revision != run.input_revision
            or session.lifecycle != "OPEN"
            or not _is_latest(db, run)
        ):
            db.rollback()
            return False
        row = (
            db.query(OnboardingRunSource)
            .filter(
                OnboardingRunSource.run_id == run_id,
                OnboardingRunSource.source_revision_id
                == uuid.UUID(entry["source_revision_id"]),
            )
            .with_for_update()
            .one()
        )
        if row.state == "FINAL":
            db.commit()
            return True  # already accounted (idempotent after a retry)
        extraction_id = reuse_extraction_id
        if result is not None and extraction_id is None and cacheable:
            extraction_id = _insert_extraction(db, entry, result)
        if extraction_id is not None:
            ext = db.get(OnboardingSourceExtraction, extraction_id)
            row.extraction_id = ext.id
            row.outcome, row.reason = ext.outcome, ext.reason
            row.coverage, row.block_count = ext.coverage, ext.block_count
        else:
            row.outcome = final_outcome or (result or {}).get("outcome", "FAILED")
            row.reason = final_reason or (result or {}).get("reason")
            row.coverage = (result or {}).get("coverage")
        row.state = "FINAL"
        row.finished_at = utcnow()
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


def _insert_extraction(
    db: Session, entry: dict[str, Any], result: dict[str, Any]
) -> uuid.UUID:
    """Insert the immutable extraction; on identity conflict reuse the published one."""
    profile = current_profile()
    ext = OnboardingSourceExtraction(
        id=uuid.uuid4(),
        source_revision_id=uuid.UUID(entry["source_revision_id"]),
        original_sha256=entry["sha256"],
        extractor_build=profile["build"],
        dependency_versions=profile["dependencies"],
        schema_version=profile["schema_version"],
        normalization_version=profile["normalization_version"],
        options_snapshot=entry["effective_options"],
        options_digest=entry["options_digest"],
        policy_digest=profile["limits_digest"],
        identity_digest=entry["identity_digest"],
        outcome=result["outcome"],
        reason=result.get("reason"),
        coverage=result["coverage"],
        warnings=result.get("warnings", []),
        normalization=result.get("normalization", ""),
        block_count=len(result["blocks"]),
    )
    try:
        with db.begin_nested():
            db.add(ext)
            db.flush()
            db.bulk_insert_mappings(
                OnboardingExtractedBlock,
                [
                    {
                        "id": uuid.uuid4(),
                        "extraction_id": ext.id,
                        "ordinal": index,
                        "kind": blk["kind"],
                        "text": blk["text"],
                        "cell_data": blk.get("cell_data"),
                        "locator": blk["locator"],
                        "content_digest": sha256_hex(blk["text"]),
                        "warnings": blk.get("warnings", []),
                    }
                    for index, blk in enumerate(result["blocks"])
                ],
            )
        return ext.id
    except IntegrityError:
        existing = (
            db.query(OnboardingSourceExtraction)
            .filter(
                OnboardingSourceExtraction.identity_digest == entry["identity_digest"]
            )
            .one_or_none()
        )
        if existing is None:
            raise
        return existing.id


def process_extraction_job(
    session_factory, store: OnboardingSourceStore, job_id: uuid.UUID, token: str
) -> str:
    from rai_gateway.onboarding.preparation import (
        TerminalError,
        _fail_unbound,
        _LostOwnership,
        _set_progress,
        publish_failure,
    )

    db = session_factory()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).one()
        try:
            run = _validate_binding(db, job)
        except TerminalError as exc:
            _fail_unbound(db, job_id, token, exc.code)
            return "invalid_binding"
        run_id, plan = run.id, run.plan
        db.rollback()

        profile = current_profile()
        if (
            plan["parser_fingerprint"] != profile["fingerprint"]
            or plan["policy_digest"] != profile["limits_digest"]
        ):
            ok = publish_failure(
                db, run_id, job_id, token, code="PARSER_PROFILE_MISMATCH", terminal=True
            )
            return "failed" if ok else "lost_ownership"

        limits = configured_limits()
        owner = _Ownership(session_factory, job_id, token)
        entries = plan["entries"]
        total_bytes = 0
        counts: dict[str, int] = {}
        try:
            for index, entry in enumerate(entries):
                if not owner.ok():
                    return "lost_ownership"
                pending = (
                    db.query(OnboardingRunSource.state)
                    .filter(
                        OnboardingRunSource.run_id == run_id,
                        OnboardingRunSource.source_revision_id
                        == uuid.UUID(entry["source_revision_id"]),
                    )
                    .scalar()
                )
                db.rollback()
                if pending == "FINAL":
                    continue  # completed before a restart/retry
                _set_progress(
                    db,
                    job_id,
                    token,
                    {
                        "stage": "EXTRACTING",
                        "sources_checked": index,
                        "sources_total": len(entries),
                    },
                )
                if total_bytes > limits.run_output_bytes:
                    _finalize_rest(db, run_id, job_id, token, "RUN_OUTPUT_LIMIT")
                    break
                outcome = _extract_one(
                    db,
                    store,
                    session_factory,
                    owner,
                    limits,
                    run_id,
                    job_id,
                    token,
                    entry,
                )
                if outcome is None:
                    return "lost_ownership"
                total_bytes += outcome
        except (_LostOwnership, ParserStopped):
            db.rollback()
            return "lost_ownership"
        except TerminalError as exc:
            ok = publish_failure(
                db, run_id, job_id, token, code=exc.code, terminal=True
            )
            return "failed" if ok else "lost_ownership"
        except (SourceStorageError, OperationalError, OSError):
            db.rollback()
            ok = publish_failure(
                db, run_id, job_id, token, code="STORAGE_UNAVAILABLE", terminal=False
            )
            return "retry" if ok else "lost_ownership"

        for row in db.query(OnboardingRunSource).filter(
            OnboardingRunSource.run_id == run_id
        ):
            counts[row.outcome or "PENDING"] = (
                counts.get(row.outcome or "PENDING", 0) + 1
            )
        db.rollback()
        ok = _publish_final(
            db, run_id, job_id, token, counts, total_bytes, len(entries)
        )
        return "succeeded" if ok else "lost_ownership"
    finally:
        db.close()


def _finalize_rest(
    db: Session, run_id: uuid.UUID, job_id: uuid.UUID, token: str, reason: str
) -> None:
    from rai_gateway.onboarding.preparation import _lock_owned

    owned = _lock_owned(db, run_id, job_id, token)
    if owned is None:
        db.rollback()
        return
    finalize_pending_sources(db, run_id, reason)
    db.commit()


def _extract_one(
    db: Session,
    store: OnboardingSourceStore,
    session_factory,
    owner: _Ownership,
    limits: Limits,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    token: str,
    entry: dict[str, Any],
) -> int | None:
    """Extract (or reuse) one source. Returns serialized bytes, or None if ownership lost."""
    from rai_gateway.onboarding.preparation import TerminalError

    revision = db.get(OnboardingSourceRevision, uuid.UUID(entry["source_revision_id"]))
    if (
        revision is None
        or revision.sha256 != entry["sha256"]
        or revision.media_family
        not in ("PDF", "DOCX", "XLSX", "CSV", "TXT", "MARKDOWN", "TEXT")
    ):
        raise TerminalError("SOURCE_INTEGRITY_FAILED")
    storage_key, byte_length = revision.storage_key, int(revision.byte_length)
    existing = (
        db.query(OnboardingSourceExtraction.id)
        .filter(OnboardingSourceExtraction.identity_digest == entry["identity_digest"])
        .scalar()
    )
    db.rollback()
    if existing is not None:
        ok = _publish_source(
            db, run_id, job_id, token, entry, None, reuse_extraction_id=existing
        )
        return 0 if ok else None
    try:
        store.verify(storage_key, sha256=entry["sha256"], byte_length=byte_length)
    except SourceIntegrityError:
        return (
            0
            if _publish_source(
                db,
                run_id,
                job_id,
                token,
                entry,
                None,
                reuse_extraction_id=None,
                final_outcome="FAILED",
                final_reason="SOURCE_INTEGRITY_FAILED",
                cacheable=False,
            )
            else None
        )
    except SourceStorageError as exc:
        if "missing" in str(exc).lower():
            return (
                0
                if _publish_source(
                    db,
                    run_id,
                    job_id,
                    token,
                    entry,
                    None,
                    reuse_extraction_id=None,
                    final_outcome="FAILED",
                    final_reason="SOURCE_MISSING",
                    cacheable=False,
                )
                else None
            )
        raise
    path = store.local_path(storage_key)
    result = run_parser(
        str(path),
        entry["media_family"],
        entry["kind"],
        entry["effective_options"],
        limits,
        should_stop=lambda: not owner.ok(),
    )
    if result["outcome"] not in EXTRACTION_OUTCOMES:
        result = {**result, "outcome": "FAILED", "reason": "PARSER_ERROR", "blocks": []}
    cacheable = result.get("reason") not in NOT_CACHED_REASONS
    size = len(json.dumps(result.get("blocks", []), ensure_ascii=False))
    ok = _publish_source(
        db,
        run_id,
        job_id,
        token,
        entry,
        result,
        reuse_extraction_id=None,
        cacheable=cacheable,
    )
    return size if ok else None


def _publish_final(
    db: Session,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    token: str,
    counts: dict[str, int],
    total_bytes: int,
    total: int,
) -> bool:
    from rai_gateway.onboarding.preparation import _audit, _is_latest, _lock_owned

    try:
        owned = _lock_owned(db, run_id, job_id, token)
        if owned is None:
            db.rollback()
            return False
        run, session, job = owned
        if (
            session.input_revision != run.input_revision
            or session.lifecycle != "OPEN"
            or not _is_latest(db, run)
        ):
            db.rollback()
            return False
        finalize_pending_sources(db, run_id, "RUN_ABORTED")  # nothing may stay pending
        attention = sum(c for k, c in counts.items() if k in ATTENTION_OUTCOMES)
        job.status = BackgroundJobStatus.SUCCEEDED.value
        job.result = {
            "input_revision": run.input_revision,
            "plan_digest": run.plan_digest,
            "sources_total": total,
            "outcomes": counts,
            "attention_count": attention,
            "bytes_extracted": total_bytes,
            "completed_at": utcnow().isoformat(),
        }
        job.completed_at = utcnow()
        job.last_error = None
        job.lease_token = None
        job.lease_expires_at = None
        run.ended_at = utcnow()
        _audit(
            db,
            "onboarding.run.extracted",
            {"session_id": str(run.session_id), "run_id": str(run.id)},
            "EXTRACTION_RECORDED",
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


__all__ = [
    "AdminAudit",
    "ExtractionService",
    "build_plan",
    "canonical_digest",
    "configured_limits",
    "current_profile",
    "finalize_pending_sources",
    "process_extraction_job",
    "supported_options",
]
