"""Immutable discovery plan construction (Step 04)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import (
    OnboardingExtractedBlock,
    OnboardingRun,
    OnboardingRunSource,
    OnboardingSourceExtraction,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.onboarding.ai_configuration import (
    AIConfigService,
    current_effective,
    snapshot_fingerprint,
)
from rai_gateway.onboarding.service import STAGE_EXTRACT
from rai_gateway.onboarding.extraction import configured_limits
from rai_gateway.onboarding.registry_snapshot import capture_snapshot
from rai_gateway.onboarding.service import canonical_digest_obj, sha256_hex
from rai_gateway.sdk.domain import BackgroundJobStatus

PLAN_VERSION = 1
DISCOVERY_LIMITS = {
    "max_calls_per_run": 32,
    "max_request_bytes_per_call": 96 * 1024,
    "max_request_bytes_per_run": 2 * 1024 * 1024,
    "max_output_tokens_per_call": 6000,
    "max_response_bytes_per_call": 512 * 1024,
    "max_stored_output_bytes_per_run": 8 * 1024 * 1024,
    "max_proposals": 500,
    "max_relationships": 1500,
    "max_claims": 5000,
    "max_clarification_questions": 200,
}


def _blocks_for_run(db: Session, extraction_run_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        db.query(OnboardingRunSource)
        .filter(OnboardingRunSource.run_id == extraction_run_id)
        .all()
    )
    blocks: list[dict[str, Any]] = []
    for row in rows:
        if row.outcome != "COMPLETE" or row.extraction_id is None:
            continue
        extraction = db.get(OnboardingSourceExtraction, row.extraction_id)
        if extraction is None or extraction.outcome != "COMPLETE":
            continue
        block_rows = (
            db.query(OnboardingExtractedBlock)
            .filter(OnboardingExtractedBlock.extraction_id == extraction.id)
            .order_by(OnboardingExtractedBlock.ordinal)
            .all()
        )
        for block in block_rows:
            blocks.append(
                {
                    "block_id": str(block.id),
                    "extraction_id": str(extraction.id),
                    "source_revision_id": str(row.source_revision_id),
                    "ordinal": block.ordinal,
                    "kind": block.kind,
                    "text": block.text,
                    "locator": block.locator,
                    "content_digest": block.content_digest,
                }
            )
    return blocks


def build_discovery_plan(
    db: Session,
    *,
    session_id: uuid.UUID,
    input_revision: int,
    clarification_revision: int,
    extraction_run: OnboardingRun,
    actor: str,
) -> tuple[dict[str, Any], str]:
    blocks = _blocks_for_run(db, extraction_run.id)
    if not blocks:
        raise ValueError("NO_EXTRACTED_BLOCKS")
    partitions: list[dict[str, Any]] = []
    primary_coverage: list[str] = []
    serialized = 0
    for idx, block in enumerate(blocks):
        text = block["text"]
        size = len(text.encode("utf-8"))
        if size > DISCOVERY_LIMITS["max_request_bytes_per_call"]:
            raise ValueError("PARTITION_TOO_LARGE")
        serialized += size
        if serialized > DISCOVERY_LIMITS["max_request_bytes_per_run"]:
            raise ValueError("PLAN_TOO_LARGE")
        bid = block["block_id"]
        primary_coverage.append(bid)
        partitions.append(
            {
                "ordinal": idx,
                "primary_block_ids": [bid],
                "context_block_ids": [],
                "request_digest": sha256_hex(
                    f"{bid}:{block['content_digest']}:{idx}"
                ),
            }
        )
    ai_svc = AIConfigService(db, actor=actor)
    cfg = ai_svc._ensure_row()
    if not cfg.enabled:
        raise ValueError("AI_CONFIGURATION_DISABLED")
    target, snapshot_ai, _fp = current_effective(cfg)
    if target is None or snapshot_ai is None:
        raise ValueError("AI_CONFIGURATION_INVALID")
    probe = ai_svc._matching_probe(cfg, snapshot_fingerprint(snapshot_ai))
    if probe is None or probe.outcome != "PASSED" or probe.ended_at is None:
        raise ValueError("AI_TEST_REQUIRED")
    ai = {
        "configuration_version": cfg.config_version,
        "target_key": cfg.target_key,
        "model": cfg.model,
        "structured_mode": cfg.structured_mode,
        "max_output_tokens": cfg.max_output_tokens,
        "target_fingerprint": snapshot_fingerprint(snapshot_ai),
        "credential_alias": target.credential_env,
    }
    snapshot = capture_snapshot(db)
    body: dict[str, Any] = {
        "plan_version": PLAN_VERSION,
        "session_id": str(session_id),
        "input_revision": input_revision,
        "clarification_revision": clarification_revision,
        "extraction_run_id": str(extraction_run.id),
        "extraction_plan_digest": extraction_run.plan_digest,
        "limits": DISCOVERY_LIMITS,
        "parser_limits": configured_limits().__dict__,
        "partitions": partitions,
        "primary_coverage": primary_coverage,
        "partition_count": len(partitions),
        "ai_configuration": ai,
        "registry_snapshot_digest": snapshot.digest,
        "registry_snapshot_captured_at": snapshot.captured_at,
        "matching_rule_version": snapshot.matching_rule_version,
        "requested_by_preview": actor,
        "fixture_mode": bool(getattr(settings, "onboarding_discovery_fixture", False)),
    }
    digest = canonical_digest_obj(body)
    return body, digest


def eligible_extraction(db: Session, session_id: uuid.UUID) -> OnboardingRun:
    run = (
        db.query(OnboardingRun)
        .filter(
            OnboardingRun.session_id == session_id,
            OnboardingRun.stage == STAGE_EXTRACT,
        )
        .order_by(OnboardingRun.run_number.desc())
        .first()
    )
    if run is None or run.ended_at is None:
        raise ValueError("EXTRACTION_REQUIRED")
    job = db.get(BackgroundJob, run.job_id)
    if job is None or job.status != BackgroundJobStatus.SUCCEEDED.value:
        raise ValueError("EXTRACTION_INCOMPLETE")
    incomplete = (
        db.query(OnboardingRunSource)
        .filter(
            OnboardingRunSource.run_id == run.id,
            OnboardingRunSource.outcome != "COMPLETE",
        )
        .count()
    )
    if incomplete:
        raise ValueError("EXTRACTION_COVERAGE_INCOMPLETE")
    return run