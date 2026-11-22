"""Runtime connection context: eligibility read and durable selection (Step 07)."""

from __future__ import annotations

import uuid
from typing import Any

from rai_gateway.models.onboarding import (
    OnboardingApplication,
    OnboardingAppliedBinding,
    OnboardingRuntimeContext,
)
from rai_gateway.onboarding.runtime_preflight import inspect_runtime_readiness
from rai_gateway.onboarding.service import Access, IntakeError, IntakeService, canonical_digest_obj
from rai_gateway.onboarding.verification_schemas import DELEGATION_MODES


class RuntimeContextService:
    def __init__(self, svc: IntakeService) -> None:
        self.svc = svc
        self.db = svc.db

    def get_runtime_context(
        self, session_id: uuid.UUID, access: Access
    ) -> dict[str, Any]:
        """Pure eligibility preflight; does not create or mutate persisted state."""
        if not access.can_read:
            raise IntakeError(403, "NOT_ALLOWED", "Missing read permission")
        session = self.svc._get_session(session_id, lock=False)
        eligibility = self._eligibility_read(session_id)
        row = (
            self.db.query(OnboardingRuntimeContext)
            .filter(OnboardingRuntimeContext.session_id == session_id)
            .one_or_none()
        )
        readiness: dict[str, Any] | None = None
        selected = (row.selected if row else None) or {}
        if selected.get("ai_system_id") and selected.get("agent_version_id"):
            try:
                readiness = inspect_runtime_readiness(
                    self.db,
                    ai_system_id=uuid.UUID(str(selected["ai_system_id"])),
                    agent_id=uuid.UUID(str(selected.get("agent_id"))),
                    agent_version_id=uuid.UUID(str(selected["agent_version_id"])),
                    runtime_identity_id=(
                        uuid.UUID(str(selected["runtime_identity_id"]))
                        if selected.get("runtime_identity_id")
                        else None
                    ),
                    sdk_integration_id=None,
                )
            except (ValueError, TypeError):
                readiness = {"ok": False, "reason": "invalid_selection"}
        return {
            "session_id": str(session_id),
            "session_row_version": session.row_version,
            "eligibility": eligibility["status"],
            "reasons": eligibility["reasons"],
            "application": eligibility.get("application"),
            "selectable": eligibility.get("selectable", {}),
            "context": self._context_view(row) if row else None,
            "readiness": readiness,
        }

    def save_runtime_context(
        self,
        session_id: uuid.UUID,
        *,
        expected_row_version: int,
        delegation_mode: str,
        selected: dict[str, Any],
        access: Access,
    ) -> dict[str, Any]:
        if not access.can_apply:
            raise IntakeError(403, "NOT_ALLOWED", "Missing apply permission")
        if delegation_mode not in DELEGATION_MODES:
            raise IntakeError(422, "INVALID_DELEGATION", "Unsupported delegation mode")
        session = self.svc._get_session(session_id, lock=True)
        try:
            self.svc._check_mutable(session, expected_row_version, require_idle=False)
            eligibility = self._eligibility_read(session_id)
            if eligibility["status"] != "READY":
                raise IntakeError(
                    409,
                    "RUNTIME_NOT_READY",
                    "Runtime connection is not eligible yet",
                    details={"reasons": eligibility["reasons"]},
                )
            self._validate_selected(eligibility, selected)
            digest = canonical_digest_obj(
                {
                    "delegation_mode": delegation_mode,
                    "selected": selected,
                }
            )
            row = (
                self.db.query(OnboardingRuntimeContext)
                .filter(OnboardingRuntimeContext.session_id == session_id)
                .one_or_none()
            )
            app_id = uuid.UUID(str(selected["application_id"]))
            if row is None:
                row = OnboardingRuntimeContext(
                    session_id=session_id,
                    application_id=app_id,
                    selected=selected,
                    selection_digest=digest,
                    delegation_mode=delegation_mode,
                    row_version=1,
                    updated_by=self.svc.actor,
                )
                self.db.add(row)
            else:
                row.application_id = app_id
                row.selected = selected
                row.selection_digest = digest
                row.delegation_mode = delegation_mode
                row.row_version = row.row_version + 1
                row.updated_by = self.svc.actor
            self.db.commit()
        except IntakeError:
            self.db.rollback()
            raise
        except Exception:
            self.db.rollback()
            raise
        return self.get_runtime_context(session_id, access)

    def _eligibility_read(self, session_id: uuid.UUID) -> dict[str, Any]:
        app = (
            self.db.query(OnboardingApplication)
            .filter(
                OnboardingApplication.session_id == session_id,
                OnboardingApplication.status == "SUCCEEDED",
            )
            .order_by(OnboardingApplication.created_at.desc())
            .first()
        )
        if app is None:
            return {
                "status": "NOT_APPLIED",
                "reasons": ["No successful application for this session"],
                "selectable": {},
            }
        bindings = (
            self.db.query(OnboardingAppliedBinding)
            .filter(OnboardingAppliedBinding.application_id == app.id)
            .all()
        )
        if not bindings:
            return {
                "status": "BLOCKED",
                "reasons": ["Application succeeded but produced no registry bindings"],
                "application": self._application_summary(app),
                "selectable": {},
            }
        selectable = self._selectable_from_bindings(bindings)
        return {
            "status": "READY",
            "reasons": [],
            "application": self._application_summary(app),
            "selectable": selectable,
        }

    @staticmethod
    def _application_summary(app: OnboardingApplication) -> dict[str, str]:
        return {
            "id": str(app.id),
            "change_set_id": str(app.change_set_id),
            "status": app.status,
        }

    @staticmethod
    def _selectable_from_bindings(
        bindings: list[OnboardingAppliedBinding],
    ) -> dict[str, list[dict[str, str]]]:
        out: dict[str, list[dict[str, str]]] = {
            "ai_systems": [],
            "agents": [],
            "content_rules": [],
            "policy_sources": [],
        }
        seen: set[tuple[str, str]] = set()
        for binding in bindings:
            key = (binding.target_kind, str(binding.target_id))
            if key in seen:
                continue
            seen.add(key)
            entry = {
                "id": str(binding.target_id),
                "binding_key": binding.binding_key,
            }
            if binding.target_kind == "ai_system":
                out["ai_systems"].append(entry)
            elif binding.target_kind == "agent":
                out["agents"].append(entry)
            elif binding.target_kind == "content_rule":
                out["content_rules"].append(entry)
            elif binding.target_kind == "policy_source":
                out["policy_sources"].append(entry)
        return out

    def _validate_selected(
        self, eligibility: dict[str, Any], selected: dict[str, Any]
    ) -> None:
        app = eligibility.get("application")
        if not app:
            raise IntakeError(409, "RUNTIME_NOT_READY", "No application")
        if str(selected.get("application_id")) != app["id"]:
            raise IntakeError(422, "APPLICATION_MISMATCH", "Application id mismatch")
        selectable = eligibility.get("selectable") or {}
        ai_ids = {x["id"] for x in selectable.get("ai_systems", [])}
        agent_ids = {x["id"] for x in selectable.get("agents", [])}
        rule_ids = {x["id"] for x in selectable.get("content_rules", [])}
        ai_system_id = selected.get("ai_system_id")
        if ai_system_id and str(ai_system_id) not in ai_ids:
            raise IntakeError(422, "UNKNOWN_TARGET", "Unknown ai_system_id")
        agent_id = selected.get("agent_id")
        if agent_id and str(agent_id) not in agent_ids:
            raise IntakeError(422, "UNKNOWN_TARGET", "Unknown agent_id")
        controls = selected.get("controls") or {}
        for rule_id in controls.get("content_rule_ids") or []:
            if str(rule_id) not in rule_ids:
                raise IntakeError(422, "UNKNOWN_TARGET", "Unknown content_rule_id")

    @staticmethod
    def _context_view(row: OnboardingRuntimeContext) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "row_version": row.row_version,
            "application_id": str(row.application_id) if row.application_id else None,
            "delegation_mode": row.delegation_mode,
            "selection_digest": row.selection_digest,
            "selected": row.selected,
            "updated_by": row.updated_by,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def require_context(
        self, session_id: uuid.UUID, *, expected_row_version: int
    ) -> OnboardingRuntimeContext:
        row = (
            self.db.query(OnboardingRuntimeContext)
            .filter(OnboardingRuntimeContext.session_id == session_id)
            .one_or_none()
        )
        if row is None:
            raise IntakeError(404, "RUNTIME_CONTEXT_NOT_FOUND", "Save runtime context first")
        if row.row_version != expected_row_version:
            raise IntakeError(409, "ROW_VERSION_CONFLICT", "Runtime context version mismatch")
        return row