"""Record cloud governance evidence links (metadata only)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.models.cloud_governance import CloudEvidenceLink


def record_evidence_link(
    db: Session,
    *,
    connection_id: UUID,
    ref_type: str,
    ref_uri: str,
    summary: str | None = None,
    complete: bool = True,
    evidence_metadata: dict[str, Any] | None = None,
    linked_entity_type: str | None = None,
    linked_entity_id: UUID | None = None,
) -> CloudEvidenceLink:
    row = CloudEvidenceLink(
        connection_id=connection_id,
        ref_type=ref_type,
        ref_uri=ref_uri,
        summary=summary,
        complete=complete,
        evidence_metadata=evidence_metadata,
        linked_entity_type=linked_entity_type,
        linked_entity_id=linked_entity_id,
    )
    db.add(row)
    db.flush()
    return row
