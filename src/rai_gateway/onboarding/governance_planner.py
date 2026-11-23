"""Immutable governance generation plan construction (Step 05)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from rai_gateway.core.config import settings
from rai_gateway.models.onboarding import (
    OnboardingExtractedBlock,
    OnboardingProposalSet,
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
from rai_gateway.onboarding.discovery_planner import PARTITION_TARGET_BYTES
from rai_gateway.onboarding.extraction import configured_limits
from rai_gateway.onboarding.governance_schemas import GOVERNANCE_LIMITS, GOVERNANCE_PLAN_VERSION
from rai_gateway.onboarding.governance_snapshot import capture_governance_baseline
from rai_gateway.onboarding.service import STAGE_EXTRACT, canonical_digest_obj, sha256_hex
from rai_gateway.sdk.domain import BackgroundJobStatus


def _policy_blocks(db: Session, extraction_run_id: uuid.UUID) -> list[dict[str, Any]]:
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
                    "text": block.text,
                    "content_digest": block.content_digest,
                    "locator": block.locator,
                }
            )
    return blocks


def _require_inventory_parent(
    db: Session, session_id: uuid.UUID, inventory_set_id: uuid.UUID
) -> OnboardingProposalSet:
    row = (
        db.query(OnboardingProposalSet)
        .filter(
            OnboardingProposalSet.id == inventory_set_id,
            OnboardingProposalSet.session_id == session_id,
        )
        .one_or_none()
    )
    if row is None:
        raise ValueError("INVENTORY_PARENT_NOT_FOUND")
    return row


def _require_extraction(db: Session, session_id: uuid.UUID) -> OnboardingRun:
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


def build_governance_plan(
    db: Session,
    *,
    session_id: uuid.UUID,
    input_revision: int,
    inventory_clarification_revision: int,
    governance_clarification_revision: int,
    inventory_proposal_set_id: uuid.UUID,
    extraction_run: OnboardingRun,
    include_suggested_controls: bool,
    actor: str,
) -> tuple[dict[str, Any], str]:
    inventory_parent = _require_inventory_parent(db, session_id, inventory_proposal_set_id)
    blocks = _policy_blocks(db, extraction_run.id)
    if not blocks:
        raise ValueError("NO_EXTRACTED_BLOCKS")
    partitions: list[dict[str, Any]] = []
    primary_coverage: list[str] = []
    serialized = 0
    group: list[dict[str, Any]] = []
    group_bytes = 0

    def close_group() -> None:
        nonlocal group, group_bytes
        if not group:
            return
        idx = len(partitions)
        partitions.append(
            {
                "ordinal": idx,
                "primary_block_ids": [b["block_id"] for b in group],
                "request_digest": sha256_hex(
                    "|".join(f"{b['block_id']}:{b['content_digest']}" for b in group)
                    + f":{idx}"
                ),
            }
        )
        group, group_bytes = [], 0

    # Consecutive blocks share one model call up to a byte budget.
    for block in blocks:
        size = len(block["text"].encode("utf-8"))
        if size > GOVERNANCE_LIMITS["max_request_bytes_per_call"]:
            raise ValueError("PARTITION_TOO_LARGE")
        serialized += size
        if serialized > GOVERNANCE_LIMITS["max_request_bytes_per_run"]:
            raise ValueError("PLAN_TOO_LARGE")
        if group and group_bytes + size > PARTITION_TARGET_BYTES:
            close_group()
        group.append(block)
        group_bytes += size
        primary_coverage.append(block["block_id"])
    close_group()
    if len(partitions) > GOVERNANCE_LIMITS["max_calls_per_run"]:
        raise ValueError("PLAN_TOO_LARGE")
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
    baseline = capture_governance_baseline(db)
    body: dict[str, Any] = {
        "plan_version": GOVERNANCE_PLAN_VERSION,
        "session_id": str(session_id),
        "input_revision": input_revision,
        "inventory_clarification_revision": inventory_clarification_revision,
        "governance_clarification_revision": governance_clarification_revision,
        "extraction_run_id": str(extraction_run.id),
        "inventory_proposal_set_id": str(inventory_parent.id),
        "inventory_edit_version": inventory_parent.edit_version,
        "inventory_content_digest": inventory_parent.content_digest,
        "limits": GOVERNANCE_LIMITS,
        "parser_limits": configured_limits().__dict__,
        "partitions": partitions,
        "primary_coverage": primary_coverage,
        "partition_count": len(partitions),
        "include_suggested_controls": include_suggested_controls,
        "ai_configuration": ai,
        "baseline_digest": baseline.digest,
        "baseline_captured_at": baseline.captured_at,
        "requested_by_preview": actor,
        "fixture_mode": bool(getattr(settings, "onboarding_governance_fixture", False)),
    }
    digest = canonical_digest_obj(body)
    return body, digest


def eligible_extraction_for_governance(db: Session, session_id: uuid.UUID) -> OnboardingRun:
    return _require_extraction(db, session_id)