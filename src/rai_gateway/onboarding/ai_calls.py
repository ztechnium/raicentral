"""Bounded, single-shot AI calls over extracted source blocks (shared by Steps 04 and 05).

Every request goes through the destination-pinned transport used by the connection test,
is sent once, and is never retried or repaired. The first provider failure or malformed
reply fails the whole run, so a partial result is never published.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import (
    OnboardingAIConfiguration,
    OnboardingExtractedBlock,
)
from rai_gateway.onboarding import ai_adapter
from rai_gateway.onboarding.ai_configuration import (
    SecretResolutionError,
    current_effective,
    resolve_secret,
)
from rai_gateway.sdk.jobs import JobQueue


class LiveFailure(Exception):
    """The run must fail with this stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LiveContext:
    snapshot: dict[str, Any]
    secret: str
    max_tokens: int
    timeout: float
    max_bytes: int


def resolve_context(db: Session, plan: dict[str, Any]) -> LiveContext:
    """The approved AI target exactly as planned, or a LiveFailure."""
    limits = plan.get("limits") or {}
    cfg = (
        db.query(OnboardingAIConfiguration)
        .filter(OnboardingAIConfiguration.singleton_key == "default")
        .one_or_none()
    )
    target, snapshot, fingerprint = current_effective(cfg)
    db.rollback()
    if target is None or snapshot is None:
        raise LiveFailure("AI_CONFIGURATION_UNAVAILABLE")
    if fingerprint != (plan.get("ai_configuration") or {}).get("target_fingerprint"):
        raise LiveFailure("AI_CONFIGURATION_CHANGED")
    try:
        secret = resolve_secret(target)
    except SecretResolutionError as exc:
        raise LiveFailure("AI_CREDENTIAL_UNAVAILABLE") from exc
    # The stored token limit only caps the connection test; real work gets the plan's
    # per-call limit bounded by what the approved target allows.
    max_tokens = min(
        int(limits.get("max_output_tokens_per_call", 6000)), int(target.max_output_tokens)
    )
    return LiveContext(
        snapshot=snapshot,
        secret=secret,
        max_tokens=max_tokens,
        timeout=float(settings.onboarding_ai_call_timeout_seconds),
        max_bytes=int(limits.get("max_response_bytes_per_call", 512 * 1024)),
    )


def execute_partitions(
    db: Session,
    plan: dict[str, Any],
    ctx: LiveContext,
    *,
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    token: str,
    call_model: type,
    system: str,
    schema: dict[str, Any],
    schema_name: str,
    user_prompt: Callable[[list[dict[str, Any]]], str],
    parse: Callable[[str, list[dict[str, Any]]], Any],
    summarize: Callable[[Any], dict[str, Any]],
) -> list[Any]:
    """One call per partition, in order; returns the parsed result of each."""
    db.query(call_model).filter(call_model.run_id == run_id).delete()
    db.commit()
    results: list[Any] = []
    for ordinal, part in enumerate(plan.get("partitions") or []):
        blocks: list[dict[str, Any]] = []
        for index, block_id in enumerate(part.get("primary_block_ids") or [], start=1):
            row = db.get(OnboardingExtractedBlock, uuid.UUID(block_id))
            if row is None:
                raise LiveFailure("SOURCE_BLOCK_MISSING")
            blocks.append(
                {
                    "label": f"B{index}",
                    "block_id": block_id,
                    "kind": row.kind,
                    "locator": row.locator,
                    "text": row.text,
                }
            )
        call = call_model(
            run_id=run_id, ordinal=ordinal, request_digest=part.get("request_digest", "")
        )
        db.add(call)
        db.flush()
        try:
            prepared = ai_adapter.prepare_request(
                ctx.snapshot,
                ctx.secret,
                "",
                generation=ai_adapter.Generation(
                    system=system,
                    user=user_prompt(blocks),
                    schema=schema,
                    schema_name=schema_name,
                    max_output_tokens=ctx.max_tokens,
                    timeout_seconds=ctx.timeout,
                ),
            )
        except ai_adapter.DestinationRejected as exc:
            call.outcome, call.error_code = "NOT_DISPATCHED", exc.code
            db.commit()
            raise LiveFailure(f"AI_{exc.code}") from exc
        # Keep the job lease alive for the whole call, and record the dispatch before
        # sending so a crash leaves an honest "may have been sent" marker.
        JobQueue(db).renew_lease(
            job_id=job_id, lease_token=token, lease_seconds=int(ctx.timeout) + 30
        )
        call.dispatch_started_at = datetime.now(timezone.utc)
        db.commit()
        result = ai_adapter.send_text(prepared, max_bytes=ctx.max_bytes)
        call.latency_ms = result.latency_ms
        call.usage = result.usage
        if result.text is None:
            call.outcome = (
                "OUTCOME_UNKNOWN" if result.outcome == ai_adapter.OUTCOME_UNKNOWN else "FAILED"
            )
            call.error_code = result.error_code
            db.commit()
            raise LiveFailure(f"AI_{result.error_code}")
        try:
            parsed = parse(result.text, blocks)
        except ValueError as exc:  # InvalidModelOutput subclasses ValueError
            call.outcome, call.error_code = "FAILED", "INVALID_OUTPUT"
            db.commit()
            raise LiveFailure("AI_INVALID_OUTPUT") from exc
        call.outcome = "SUCCEEDED"
        call.result = summarize(parsed)
        db.commit()
        results.append(parsed)
    return results
