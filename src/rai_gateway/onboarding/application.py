"""Application coordinator: enqueue and execute reviewed change sets (Step 06)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from rai_gateway.models.onboarding import (
    OnboardingApplication,
    OnboardingApplicationAttempt,
    OnboardingApplicationReceipt,
    OnboardingAppliedBinding,
    OnboardingChangeOperation,
    OnboardingChangeSet,
    OnboardingRun,
    OnboardingSession,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.application_adapters import (
    ApplyAdapterError,
    create_agent,
    create_ai_system,
    create_content_rule_disabled,
    create_policy_source_structured,
    reuse_target,
    update_ai_system_draft,
)
from rai_gateway.onboarding.service import (
    APPLY_JOB_TYPE,
    STAGE_APPLY,
    Access,
    IntakeError,
    IntakeService,
    sha256_hex,
    utcnow,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue


class ApplicationCoordinator:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    def enqueue_apply(
        self,
        session_id: uuid.UUID,
        *,
        change_set_id: uuid.UUID,
        expected_row_version: int,
        requested_input_revision: int,
        expected_payload_digest: str,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        require_apply_writes(access)
        scope = self.svc._scope("apply_change_set", session_id)
        fingerprint = self.svc._fingerprint(
            {
                "change_set_id": str(change_set_id),
                "payload_digest": expected_payload_digest,
                "input_revision": requested_input_revision,
            }
        )
        receipt = self._lookup_apply_receipt(scope, idempotency_key, fingerprint)
        if receipt is not None:
            app = self.db.get(OnboardingApplication, receipt.application_id)
            assert app is not None
            return self._application_view(app, replayed=True)
        session = self.svc._get_session(session_id, lock=True)
        try:
            receipt = self._lookup_apply_receipt(scope, idempotency_key, fingerprint)
            if receipt is not None:
                self.db.rollback()
                app = self.db.get(OnboardingApplication, receipt.application_id)
                assert app is not None
                return self._application_view(app, replayed=True)
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            if requested_input_revision != session.input_revision:
                raise IntakeError(409, "REVISION_CONFLICT", "Input revision changed")
            if self.svc._active_run(session_id) is not None:
                raise IntakeError(409, "RUN_ACTIVE", "A session run is already active")
            cs = self.db.get(OnboardingChangeSet, change_set_id)
            if cs is None or cs.session_id != session_id:
                raise IntakeError(404, "CHANGE_SET_NOT_FOUND", "Change set not found")
            if cs.status != "PREPARED":
                raise IntakeError(409, "CHANGE_SET_NOT_PREPARED", "Change set is not prepared")
            if cs.payload_digest != expected_payload_digest:
                raise IntakeError(409, "PAYLOAD_STALE", "Change set payload digest mismatch")
            existing_app = (
                self.db.query(OnboardingApplication)
                .filter(OnboardingApplication.change_set_id == cs.id)
                .one_or_none()
            )
            if existing_app is not None:
                raise IntakeError(
                    409, "APPLICATION_EXISTS", "An application already exists for this change set"
                )
            application = OnboardingApplication(
                session_id=session_id,
                change_set_id=cs.id,
                status="QUEUED",
                created_by=self.svc.actor,
            )
            self.db.add(application)
            self.db.flush()
            run = self._create_run(session, cs, application)
            self.db.add(
                OnboardingApplicationAttempt(
                    application_id=application.id,
                    attempt_number=1,
                    run_id=run.id,
                    job_id=run.job_id,
                )
            )
            cs.status = "APPLYING"
            self._add_apply_receipt(scope, idempotency_key, fingerprint, application.id)
            self.svc._audit(
                "onboarding.apply.enqueue",
                {
                    "session_id": str(session_id),
                    "application_id": str(application.id),
                    "change_set_id": str(cs.id),
                },
                "APPLY_REQUESTED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self._application_view(application, replayed=False)

    def _create_run(
        self,
        session: OnboardingSession,
        cs: OnboardingChangeSet,
        application: OnboardingApplication,
    ) -> OnboardingRun:
        run_id = uuid.uuid4()
        number = (
            self.db.query(func.max(OnboardingRun.run_number))
            .filter(OnboardingRun.session_id == session.id)
            .scalar()
            or 0
        ) + 1
        job = JobQueue(self.db).enqueue(
            job_type=APPLY_JOB_TYPE,
            idempotency_key=f"onboarding:apply:{application.id}",
            allow_reserved=True,
            payload={
                "run_id": str(run_id),
                "session_id": str(session.id),
                "application_id": str(application.id),
                "change_set_id": str(cs.id),
                "payload_digest": cs.payload_digest,
                "actor": self.svc.actor,
            },
        )
        run = OnboardingRun(
            id=run_id,
            session_id=session.id,
            run_number=number,
            stage=STAGE_APPLY,
            input_revision=session.input_revision,
            manifest={"application_id": str(application.id)},
            manifest_digest=cs.payload_digest,
            change_set_id=cs.id,
            job_id=uuid.UUID(job["id"]),
            created_by=self.svc.actor,
        )
        self.db.add(run)
        self.db.flush()
        application.status = "RUNNING"
        return run

    def execute_application_job(
        self,
        db: Session,
        *,
        application_id: uuid.UUID,
        actor: str,
    ) -> dict[str, Any]:
        """Apply all accepted operations in one transaction; record bindings and receipt."""
        application = db.get(OnboardingApplication, application_id)
        if application is None:
            raise ApplyAdapterError("NOT_FOUND", "Application not found")
        cs = db.get(OnboardingChangeSet, application.change_set_id)
        if cs is None:
            raise ApplyAdapterError("NOT_FOUND", "Change set not found")
        ops = (
            db.query(OnboardingChangeOperation)
            .filter(OnboardingChangeOperation.change_set_id == cs.id)
            .order_by(OnboardingChangeOperation.ordinal)
            .all()
        )
        from rai_gateway.models.onboarding import OnboardingReviewDecision

        decisions = {
            str(row.operation_id): row.decision
            for row in db.query(OnboardingReviewDecision)
            .filter(OnboardingReviewDecision.change_set_id == cs.id)
            .all()
        }
        bindings: dict[int, uuid.UUID] = {}
        applied: list[dict[str, Any]] = []
        try:
            for op in ops:
                decision = decisions.get(str(op.id), "ACCEPT")
                if decision == "SKIP":
                    op.status = "SKIPPED"
                    continue
                for dep in op.dependency_ordinals or []:
                    if dep not in bindings:
                        raise ApplyAdapterError(
                            "DEPENDENCY_MISSING",
                            f"Operation {op.ordinal} missing dependency {dep}",
                        )
                target_id = self._execute_operation(
                    db, actor=actor, op=op, bindings=bindings
                )
                bindings[op.ordinal] = target_id
                op.status = "APPLIED"
                binding_key = f"{op.target_kind}:{op.source_proposal_id or op.ordinal}"
                db.add(
                    OnboardingAppliedBinding(
                        application_id=application.id,
                        operation_id=op.id,
                        target_kind=op.target_kind,
                        target_id=target_id,
                        binding_key=binding_key[:128],
                    )
                )
                applied.append(
                    {
                        "ordinal": op.ordinal,
                        "target_kind": op.target_kind,
                        "target_id": str(target_id),
                    }
                )
            application.status = "SUCCEEDED"
            cs.status = "APPLIED"
            db.flush()
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"bindings": applied, "application_id": str(application.id)}

    @staticmethod
    def _execute_operation(
        db: Session,
        *,
        actor: str,
        op: OnboardingChangeOperation,
        bindings: dict[int, uuid.UUID],
    ) -> uuid.UUID:
        action = op.action_payload or {}
        verb = op.verb
        kind = op.target_kind
        if verb == "REUSE":
            rid = action.get("target_id")
            if not rid:
                raise ApplyAdapterError("INVALID_ACTION", "REUSE requires target_id")
            return reuse_target(db, target_kind=kind, target_id=uuid.UUID(str(rid)))

        if kind == "ai_system":
            if verb == "CREATE":
                row = create_ai_system(db, actor=actor, claims=action.get("claims") or {})
                return row.id
            if verb == "UPDATE":
                rid = action.get("target_id")
                if not rid:
                    raise ApplyAdapterError("INVALID_ACTION", "UPDATE requires target_id")
                row = update_ai_system_draft(
                    db,
                    actor=actor,
                    system_id=uuid.UUID(str(rid)),
                    fields=action.get("fields") or {},
                )
                return row.id

        if kind == "agent":
            if verb != "CREATE":
                raise ApplyAdapterError("UNSUPPORTED_VERB", "Agent supports CREATE only")
            deps = op.dependency_ordinals or []
            if not deps:
                raise ApplyAdapterError("MISSING_PARENT", "Agent requires parent system")
            parent_id = bindings.get(deps[0])
            if parent_id is None:
                raise ApplyAdapterError("MISSING_PARENT", "Parent system binding missing")
            row = create_agent(
                db,
                actor=actor,
                ai_system_id=parent_id,
                claims=action.get("claims") or {},
            )
            return row.id

        if kind == "policy_source" and verb == "CREATE":
            generated = action.get("generated") or action
            row = create_policy_source_structured(db, actor=actor, generated=generated)
            return row.id

        if kind == "content_rule" and verb == "CREATE":
            generated = action.get("generated") or action
            sys_key = generated.get("system_key")
            ai_system_id = None
            if sys_key:
                from rai_gateway.models.registry import AISystem

                system = (
                    db.query(AISystem).filter(AISystem.system_key == sys_key).first()
                )
                if system:
                    ai_system_id = system.id
            deps = op.dependency_ordinals or []
            if ai_system_id is None and deps:
                ai_system_id = bindings.get(deps[0])
            if ai_system_id is None:
                raise ApplyAdapterError("UNBOUND_SYSTEM", "Content rule requires ai_system")
            row = create_content_rule_disabled(
                db, ai_system_id=ai_system_id, generated=generated
            )
            return row.id

        raise ApplyAdapterError(
            "UNSUPPORTED_OPERATION", f"Unsupported {verb} on {kind}"
        )

    def cancel(
        self,
        session_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        app = self.db.get(OnboardingApplication, application_id)
        if app is None or app.session_id != session_id:
            raise IntakeError(404, "APPLICATION_NOT_FOUND", "Application not found")
        if app.status not in ("QUEUED", "RUNNING"):
            raise IntakeError(409, "APPLICATION_NOT_ACTIVE", "Application is not active")
        attempt = (
            self.db.query(OnboardingApplicationAttempt)
            .filter(OnboardingApplicationAttempt.application_id == app.id)
            .order_by(OnboardingApplicationAttempt.attempt_number.desc())
            .first()
        )
        if attempt and attempt.run_id:
            run = self.db.get(OnboardingRun, attempt.run_id)
            if run and run.ended_at is None:
                job = (
                    self.db.query(BackgroundJob)
                    .filter(BackgroundJob.id == run.job_id)
                    .with_for_update()
                    .one_or_none()
                )
                if job and job.status == BackgroundJobStatus.RUNNING.value:
                    job.status = BackgroundJobStatus.CANCELLED.value
                    job.completed_at = utcnow()
                    job.lease_token = None
                    job.lease_expires_at = None
                run.ended_at = utcnow()
        app.status = "CANCELLED"
        cs = self.db.get(OnboardingChangeSet, app.change_set_id)
        if cs and cs.status == "APPLYING":
            cs.status = "PREPARED"
        self.db.commit()
        return self._application_view(app, replayed=False)

    def retry(
        self,
        session_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        expected_row_version: int,
        idempotency_key: str,
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        session = self.svc._get_session(session_id, lock=True)
        self.svc._check_mutable(session, expected_row_version, require_idle=False)
        app = self.db.get(OnboardingApplication, application_id)
        if app is None or app.session_id != session_id:
            raise IntakeError(404, "APPLICATION_NOT_FOUND", "Application not found")
        if app.status != "FAILED":
            raise IntakeError(409, "APPLICATION_NOT_FAILED", "Only failed applications may retry")
        if self.svc._active_run(session_id) is not None:
            raise IntakeError(409, "RUN_ACTIVE", "A session run is already active")
        cs = self.db.get(OnboardingChangeSet, app.change_set_id)
        if cs is None or cs.status not in ("PREPARED", "APPLYING"):
            raise IntakeError(409, "CHANGE_SET_NOT_RETRYABLE", "Change set not retryable")
        attempt_no = (
            self.db.query(func.max(OnboardingApplicationAttempt.attempt_number))
            .filter(OnboardingApplicationAttempt.application_id == app.id)
            .scalar()
            or 0
        ) + 1
        run = self._create_run(session, cs, app)
        self.db.add(
            OnboardingApplicationAttempt(
                application_id=app.id,
                attempt_number=attempt_no,
                run_id=run.id,
                job_id=run.job_id,
            )
        )
        app.status = "RUNNING"
        self.db.commit()
        return self._application_view(app, replayed=False)

    def get_application(
        self, session_id: uuid.UUID, application_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        if not access.can_read:
            raise IntakeError(403, "NOT_ALLOWED", "Missing read permission")
        app = self.db.get(OnboardingApplication, application_id)
        if app is None or app.session_id != session_id:
            raise IntakeError(404, "APPLICATION_NOT_FOUND", "Application not found")
        return self._application_view(app, replayed=False)

    def _application_view(
        self, app: OnboardingApplication, *, replayed: bool
    ) -> dict[str, Any]:
        latest = (
            self.db.query(OnboardingApplicationAttempt)
            .filter(OnboardingApplicationAttempt.application_id == app.id)
            .order_by(OnboardingApplicationAttempt.attempt_number.desc())
            .first()
        )
        run_id = str(latest.run_id) if latest and latest.run_id else None
        job = None
        if latest:
            job_row = self.db.get(BackgroundJob, latest.job_id)
            if job_row:
                job = {
                    "id": str(job_row.id),
                    "status": job_row.status,
                    "last_error": job_row.last_error,
                }
        return {
            "id": str(app.id),
            "session_id": str(app.session_id),
            "change_set_id": str(app.change_set_id),
            "status": app.status,
            "run_id": run_id,
            "job": job,
            "replayed": replayed,
        }

    def _lookup_apply_receipt(
        self, scope: str, key: str, fingerprint: str
    ) -> OnboardingApplicationReceipt | None:
        receipt = (
            self.db.query(OnboardingApplicationReceipt)
            .filter(
                OnboardingApplicationReceipt.scope == scope,
                OnboardingApplicationReceipt.key_digest == sha256_hex(key),
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

    def _add_apply_receipt(
        self, scope: str, key: str, fingerprint: str, application_id: uuid.UUID
    ) -> None:
        self.db.add(
            OnboardingApplicationReceipt(
                application_id=application_id,
                scope=scope,
                key_digest=sha256_hex(key),
                fingerprint=fingerprint,
            )
        )


def require_apply_writes(access: Access) -> None:
    if not access.can_registry_write:
        raise IntakeError(403, "NOT_ALLOWED", "Missing registry:write")
    if not access.can_policies_write:
        raise IntakeError(403, "NOT_ALLOWED", "Missing policies:write")
