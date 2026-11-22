"""Explicit onboarding AI configuration (Step 03).

Deployment-approved targets only. There is no default provider or model, no ambient
key lookup, no fallback provider and no mock. The configuration row stores choices,
never secrets. Saving never makes an external request.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.database import AdminAudit
from rai_gateway.models.onboarding import (
    OnboardingAIConfiguration,
    OnboardingProviderProbe,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.parsers.contracts import digest_of
from rai_gateway.onboarding.service import (
    PROBE_JOB_TYPE,
    IntakeError,
    _iso,
    sha256_hex,
    utcnow,
)
from rai_gateway.provider_gateway.credentials.secret_resolver import (
    EnvironmentSecretResolver,
    SecretResolutionError,
)
from rai_gateway.sdk.domain import BackgroundJobStatus
from rai_gateway.sdk.jobs import JobQueue

PROTOCOL = "OPENAI_COMPATIBLE_CHAT"
STRUCTURED_MODES = ("JSON_SCHEMA", "JSON_OBJECT")
TOKEN_PARAMS = ("max_tokens", "max_completion_tokens")
_ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "169.254.169.254",
    }
)
CREDENTIAL_ENV_PREFIX = "ONBOARDING_AI_KEY_"


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    base_url: str
    models: tuple[str, ...]
    structured_modes: tuple[str, ...]
    token_param: str
    max_output_tokens: int
    credential_alias: str
    credential_version: int
    timeout_seconds: float
    allow_private_network: bool

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    @property
    def credential_env(self) -> str:
        return f"{CREDENTIAL_ENV_PREFIX}{self.credential_alias.upper()}"


class TargetRegistryError(ValueError):
    pass


def _parse_target(raw: dict[str, Any]) -> Target:
    key = str(raw.get("key", ""))
    if not _KEY_RE.match(key):
        raise TargetRegistryError("invalid target key")
    if raw.get("protocol") != PROTOCOL:
        raise TargetRegistryError(f"{key}: unsupported protocol")
    url = str(raw.get("base_url", "")).rstrip("/")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or parts.query
        or parts.hostname.lower() in _METADATA_HOSTS
    ):
        raise TargetRegistryError(f"{key}: base_url must be a plain https URL")
    models = tuple(str(m) for m in raw.get("models", []) if str(m))
    modes = tuple(raw.get("structured_modes", []))
    if not models or not modes or any(m not in STRUCTURED_MODES for m in modes):
        raise TargetRegistryError(f"{key}: models and structured_modes are required")
    token_param = raw.get("token_param", "max_tokens")
    alias = str(raw.get("credential_alias", ""))
    if token_param not in TOKEN_PARAMS or not _ALIAS_RE.match(alias):
        raise TargetRegistryError(f"{key}: invalid token_param or credential_alias")
    timeout = min(
        float(raw.get("timeout_seconds", settings.onboarding_ai_probe_timeout_seconds)),
        30.0,
    )
    return Target(
        key=key,
        label=str(raw.get("label", key))[:80],
        base_url=url,
        models=models,
        structured_modes=modes,
        token_param=token_param,
        max_output_tokens=int(raw.get("max_output_tokens", 1024)),
        credential_alias=alias,
        credential_version=int(raw.get("credential_version", 1)),
        timeout_seconds=timeout,
        allow_private_network=bool(raw.get("allow_private_network", False)),
    )


def load_targets() -> tuple[dict[str, Target], list[str]]:
    """Parse the deployment registry. Invalid entries are skipped and reported by key."""
    raw = (settings.onboarding_ai_targets_json or "").strip()
    if not raw:
        return {}, []
    try:
        items = json.loads(raw)
    except ValueError:
        return {}, ["registry: invalid JSON"]
    if not isinstance(items, list):
        return {}, ["registry: expected a list"]
    targets: dict[str, Target] = {}
    problems: list[str] = []
    for item in items:
        try:
            target = _parse_target(item)
        except (TargetRegistryError, TypeError, ValueError, AttributeError) as exc:
            problems.append(str(exc)[:120])
            continue
        targets[target.key] = target
    return targets, problems


def credential_configured(target: Target) -> bool:
    return bool(os.environ.get(target.credential_env))


def resolve_secret(target: Target) -> str:
    """Resolve the onboarding-only credential through the existing low-level resolver."""
    resolved = EnvironmentSecretResolver().resolve("ENVIRONMENT", target.credential_env)
    return str(resolved.payload["value"])


def effective_snapshot(
    target: Target, cfg: OnboardingAIConfiguration
) -> dict[str, Any]:
    """Nonsecret effective configuration: DB choices plus deployment target policy."""
    return {
        "protocol": PROTOCOL,
        "target_key": target.key,
        "base_url": target.base_url,
        "model": cfg.model,
        "structured_mode": cfg.structured_mode,
        "token_param": target.token_param,
        "max_output_tokens": cfg.max_output_tokens
        or settings.onboarding_ai_probe_default_max_tokens,
        "credential_alias": target.credential_alias,
        "credential_version": target.credential_version,
        "timeout_seconds": target.timeout_seconds,
        "allow_private_network": target.allow_private_network,
        "tls_verify": True,
        "redirects": False,
        "config_version": cfg.config_version,
    }


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    return digest_of(snapshot)


def current_effective(
    cfg: OnboardingAIConfiguration | None,
) -> tuple[Target | None, dict[str, Any] | None, str | None]:
    """The exact policy a probe/analysis would use right now, or (None, None, None)."""
    if cfg is None or not cfg.enabled or not cfg.target_key:
        return None, None, None
    target = load_targets()[0].get(cfg.target_key)
    if (
        target is None
        or cfg.model not in target.models
        or cfg.structured_mode not in target.structured_modes
    ):
        return None, None, None
    snap = effective_snapshot(target, cfg)
    return target, snap, snapshot_fingerprint(snap)


class AIConfigService:
    def __init__(self, db: Session, *, actor: str) -> None:
        self.db = db
        self.actor = actor

    # ---------------------------------------------------------------- helpers
    def _audit(self, action: str, target: dict[str, Any], reason: str) -> None:
        self.db.add(
            AdminAudit(
                trace_id=uuid.uuid4().hex,
                actor_user_id=self.actor,
                actor_role="onboarding_ai",
                action=action,
                target=target,
                reason=reason,
            )
        )

    def _row(self, *, lock: bool = False) -> OnboardingAIConfiguration | None:
        query = self.db.query(OnboardingAIConfiguration).filter(
            OnboardingAIConfiguration.singleton_key == "default"
        )
        if lock:
            query = query.with_for_update().populate_existing()
        return query.one_or_none()

    def _ensure_row(self, *, lock: bool = False) -> OnboardingAIConfiguration:
        row = self._row(lock=lock)
        if row is not None:
            return row
        try:
            with self.db.begin_nested():
                self.db.add(
                    OnboardingAIConfiguration(
                        singleton_key="default",
                        config_version=1,
                        enabled=False,
                        updated_by="system",
                    )
                )
        except IntegrityError:
            pass
        row = self._row(lock=lock)
        assert row is not None
        return row

    def _matching_probe(
        self, cfg: OnboardingAIConfiguration | None, fingerprint: str | None
    ) -> OnboardingProviderProbe | None:
        if cfg is None or fingerprint is None:
            return None
        return (
            self.db.query(OnboardingProviderProbe)
            .filter(
                OnboardingProviderProbe.configuration_version == cfg.config_version,
                OnboardingProviderProbe.target_fingerprint == fingerprint,
            )
            .order_by(OnboardingProviderProbe.probe_number.desc())
            .first()
        )

    # ---------------------------------------------------------------- status
    def summary(self, *, detailed: bool) -> dict[str, Any]:
        cfg = self._row()
        targets, problems = load_targets()
        out: dict[str, Any] = {"state": "UNCONFIGURED", "enabled": False}
        if cfg is None or not cfg.target_key:
            out["available_targets"] = len(targets)
            if detailed:
                out.update(
                    configuration_version=cfg.config_version if cfg else 1,
                    target_problems=len(problems),
                )
            return out
        out["enabled"] = bool(cfg.enabled)
        if not cfg.enabled:
            out["state"] = "DISABLED"
        target, snapshot, fingerprint = current_effective(cfg)
        last = self._matching_probe(cfg, fingerprint)
        if cfg.enabled:
            if target is None:
                out["state"] = "TARGET_UNAVAILABLE"
            elif last is None:
                any_probe = (
                    self.db.query(OnboardingProviderProbe.id).first() is not None
                )
                out["state"] = "TEST_STALE" if any_probe else "CONFIGURED_UNTESTED"
            elif last.ended_at is None:
                out["state"] = "TEST_PENDING"
            else:
                out["state"] = {
                    "PASSED": "TEST_PASSED",
                    "FAILED": "TEST_FAILED",
                    "OUTCOME_UNKNOWN": "TEST_UNKNOWN",
                    "CANCELLED": "CONFIGURED_UNTESTED",
                }[last.outcome]
        if last is not None and last.ended_at is not None:
            out["last_test_at"] = _iso(last.ended_at)
        if detailed:
            out.update(
                configuration_version=cfg.config_version,
                target_key=cfg.target_key,
                model=cfg.model,
                structured_mode=cfg.structured_mode,
                max_output_tokens=cfg.max_output_tokens,
                target_fingerprint=fingerprint,
                credential_configured=bool(target and credential_configured(target)),
                destination_label=target.host if target else None,
                last_test=self.probe_view(last) if last else None,
            )
        return out

    def targets_view(self) -> dict[str, Any]:
        targets, problems = load_targets()
        return {
            "items": [
                {
                    "key": t.key,
                    "label": t.label,
                    "protocol": PROTOCOL,
                    "destination_label": t.host,
                    "models": list(t.models),
                    "structured_modes": list(t.structured_modes),
                    "max_output_tokens": t.max_output_tokens,
                    "credential_configured": credential_configured(t),
                    "credential_version": t.credential_version,
                }
                for t in targets.values()
            ],
            "invalid_entries": len(problems),
        }

    @staticmethod
    def probe_view(probe: OnboardingProviderProbe) -> dict[str, Any]:
        snap = probe.snapshot or {}
        return {
            "id": str(probe.id),
            "probe_number": probe.probe_number,
            "configuration_version": probe.configuration_version,
            "target_key": snap.get("target_key"),
            "model": snap.get("model"),
            "structured_mode": snap.get("structured_mode"),
            "credential_version": probe.credential_version,
            "target_fingerprint": probe.target_fingerprint,
            "state": "ACTIVE" if probe.ended_at is None else probe.outcome,
            "outcome": probe.outcome,
            "error_code": probe.error_code,
            "dispatch_started": probe.dispatch_started_at is not None,
            "latency_ms": probe.latency_ms,
            "usage": probe.usage,  # None means unknown, never zero
            "created_at": _iso(probe.created_at),
            "ended_at": _iso(probe.ended_at),
        }

    # ----------------------------------------------------------------- save
    def save(
        self,
        *,
        expected_configuration_version: int,
        enabled: bool,
        target_key: str | None,
        model: str | None,
        structured_mode: str | None,
        max_output_tokens: int | None,
    ) -> dict[str, Any]:
        cfg = self._ensure_row(lock=True)
        try:
            if cfg.config_version != expected_configuration_version:
                raise IntakeError(
                    409,
                    "CONFIGURATION_CONFLICT",
                    "The AI configuration changed. Reload and review it.",
                    details={"current_configuration_version": cfg.config_version},
                )
            targets, _ = load_targets()
            if enabled or target_key:
                target = targets.get(target_key or "")
                if target is None:
                    raise IntakeError(
                        422, "UNKNOWN_TARGET", "Choose an approved target"
                    )
                if model not in target.models:
                    raise IntakeError(
                        422, "MODEL_NOT_ALLOWED", "Model is not allowed for this target"
                    )
                if structured_mode not in target.structured_modes:
                    raise IntakeError(
                        422,
                        "MODE_NOT_SUPPORTED",
                        "Structured-output mode is not supported",
                    )
                if max_output_tokens is not None and not (
                    1 <= max_output_tokens <= target.max_output_tokens
                ):
                    raise IntakeError(
                        422, "INVALID_TOKEN_LIMIT", "Output token limit is out of range"
                    )
            cfg.enabled = enabled
            cfg.target_key = target_key
            cfg.model = model
            cfg.structured_mode = structured_mode
            cfg.max_output_tokens = max_output_tokens
            cfg.config_version += 1
            cfg.updated_by = self.actor
            self._audit(
                "onboarding.ai.configure",
                {
                    "configuration_version": cfg.config_version,
                    "enabled": enabled,
                    "target_key": target_key,
                },
                "CONFIGURATION_SAVED",
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return self.summary(detailed=True)

    # ----------------------------------------------------------------- tests
    def request_test(
        self,
        *,
        expected_configuration_version: int,
        expected_target_fingerprint: str,
        idempotency_key: str,
    ) -> OnboardingProviderProbe:
        from rai_gateway.onboarding.service import IntakeService

        scope = f"ai-test:{self.actor}"
        fingerprint = digest_of(
            {
                "op": "ai_test",
                "version": expected_configuration_version,
                "fingerprint": expected_target_fingerprint,
            }
        )
        receipt = IntakeService._receipt_lookup(
            self.db, scope, idempotency_key, fingerprint
        )
        if receipt is not None:
            return self.db.get(OnboardingProviderProbe, receipt.resource_id)
        cfg = self._ensure_row(lock=True)
        try:
            receipt = IntakeService._receipt_lookup(
                self.db, scope, idempotency_key, fingerprint
            )
            if receipt is not None:
                self.db.rollback()
                return self.db.get(OnboardingProviderProbe, receipt.resource_id)
            if cfg.config_version != expected_configuration_version:
                raise IntakeError(
                    409,
                    "CONFIGURATION_CONFLICT",
                    "The AI configuration changed. Reload before testing.",
                    details={"current_configuration_version": cfg.config_version},
                )
            target, snapshot, current_fp = current_effective(cfg)
            if target is None or snapshot is None or current_fp is None:
                raise IntakeError(
                    409,
                    "AI_NOT_CONFIGURED",
                    "Save an enabled configuration for an approved target first",
                )
            if current_fp != expected_target_fingerprint:
                raise IntakeError(
                    409,
                    "TARGET_POLICY_CHANGED",
                    "The deployment target policy changed. Reload before testing.",
                    details={"current_target_fingerprint": current_fp},
                )
            active = (
                self.db.query(OnboardingProviderProbe)
                .filter(
                    OnboardingProviderProbe.configuration_version == cfg.config_version,
                    OnboardingProviderProbe.target_fingerprint == current_fp,
                    OnboardingProviderProbe.ended_at.is_(None),
                )
                .one_or_none()
            )
            if active is not None:
                probe = active  # concurrent clicks reuse one active probe
            else:
                probe_id = uuid.uuid4()
                number = (
                    self.db.query(
                        func.max(OnboardingProviderProbe.probe_number)
                    ).scalar()
                    or 0
                ) + 1
                job = JobQueue(self.db).enqueue(
                    job_type=PROBE_JOB_TYPE,
                    payload={
                        "probe_id": str(probe_id),
                        "configuration_version": cfg.config_version,
                        "target_fingerprint": current_fp,
                    },
                    idempotency_key=f"onboarding:probe:{probe_id}",
                    created_by=self.actor,
                    max_attempts=1,  # never auto-resend a provider request
                    allow_reserved=True,
                )
                probe = OnboardingProviderProbe(
                    id=probe_id,
                    probe_number=number,
                    configuration_version=cfg.config_version,
                    target_fingerprint=current_fp,
                    snapshot=snapshot,
                    credential_alias=target.credential_alias,
                    credential_version=target.credential_version,
                    nonce=secrets.token_hex(16),
                    job_id=uuid.UUID(job["id"]),
                    created_by=self.actor,
                )
                self.db.add(probe)
                self.db.flush()
                self._audit(
                    "onboarding.ai.test_requested",
                    {
                        "probe_id": str(probe_id),
                        "configuration_version": cfg.config_version,
                    },
                    "SYNTHETIC_TEST_REQUESTED",
                )
            IntakeService._receipt_add(
                self.db,
                scope,
                idempotency_key,
                fingerprint,
                "PROBE",
                probe.id,
                self.actor,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return probe

    def get_probe(self, probe_id: uuid.UUID) -> OnboardingProviderProbe:
        probe = self.db.get(OnboardingProviderProbe, probe_id)
        if probe is None:
            raise IntakeError(404, "PROBE_NOT_FOUND", "Test not found")
        return probe

    def list_probes(self, *, limit: int, offset: int) -> dict[str, Any]:
        query = self.db.query(OnboardingProviderProbe)
        total = query.count()
        rows = (
            query.order_by(OnboardingProviderProbe.probe_number.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        return {
            "items": [self.probe_view(p) for p in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def cancel_probe(self, probe_id: uuid.UUID) -> OnboardingProviderProbe:
        self._ensure_row(lock=True)  # lock order: configuration, probe, job
        try:
            probe = (
                self.db.query(OnboardingProviderProbe)
                .filter(OnboardingProviderProbe.id == probe_id)
                .with_for_update()
                .populate_existing()
                .one_or_none()
            )
            if probe is None:
                raise IntakeError(404, "PROBE_NOT_FOUND", "Test not found")
            job = (
                self.db.query(BackgroundJob)
                .filter(BackgroundJob.id == probe.job_id)
                .with_for_update()
                .populate_existing()
                .one()
            )
            if probe.ended_at is None:
                job.status = BackgroundJobStatus.CANCELLED.value
                job.completed_at = utcnow()
                job.lease_token = None
                job.lease_expires_at = None
                probe.outcome = "CANCELLED"
                probe.error_code = "CANCELLED_BY_USER"
                probe.ended_at = utcnow()
                self._audit(
                    "onboarding.ai.test_cancelled",
                    {"probe_id": str(probe_id)},
                    "CANCELLED_BY_USER",
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return probe


__all__ = [
    "AIConfigService",
    "PROTOCOL",
    "Target",
    "credential_configured",
    "current_effective",
    "load_targets",
    "resolve_secret",
    "SecretResolutionError",
    "sha256_hex",
]
