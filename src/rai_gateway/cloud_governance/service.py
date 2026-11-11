"""Cloud governance connection and sync orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.approval.domain import IntegrationType
from rai_gateway.cloud_governance.coverage import evaluate_connection_coverage
from rai_gateway.cloud_governance.domain import (
    AssetStatus,
    AuthMode,
    CloudJobType,
    CloudProvider,
    ConnectionStatus,
    PolicyChangeStatus,
    SyncRunStatus,
)
from rai_gateway.cloud_governance.evidence_helpers import record_evidence_link
from rai_gateway.cloud_governance.onboarding import (
    cloud_shell_instructions,
    render_terraform,
)
from rai_gateway.cloud_governance.policy_approval import (
    mark_approval_consumed,
    policy_action_digest,
    validate_approval_for_policy_apply,
)
from rai_gateway.cloud_governance.providers.gcp.auth import redact_auth_config_for_api
from rai_gateway.cloud_governance.providers.gcp.connector import GcpConnector
from rai_gateway.cloud_governance.secrets import (
    decrypt_connection_config,
    encrypt_connection_config,
)
from rai_gateway.core.observability import generate_trace_id, log_admin_audit
from rai_gateway.models.approval import ApprovalRequest
from rai_gateway.models.cloud_governance import (
    CloudConnection,
    CloudCoverageSnapshot,
    CloudDiscoveredAsset,
    CloudDriftFinding,
    CloudEvidenceLink,
    CloudPolicyChangeRequest,
    CloudSyncRun,
)
from rai_gateway.models.productization import BackgroundJob
from rai_gateway.sdk.jobs import JobQueue


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_connector(provider: str):
    if provider == CloudProvider.GCP.value:
        return GcpConnector()
    raise ValueError(f"Unsupported provider: {provider}")


def _auth_payload(connection: CloudConnection) -> dict[str, Any]:
    config = decrypt_connection_config(connection.encrypted_config)
    config["_auth_mode"] = connection.auth_mode
    return config


class CloudGovernanceService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_providers(self) -> list[dict[str, Any]]:
        return [
            {
                "id": CloudProvider.GCP.value,
                "name": "Google Cloud",
                "status": "available",
            },
            {
                "id": CloudProvider.AWS.value,
                "name": "Amazon Web Services",
                "status": "coming_soon",
            },
            {
                "id": CloudProvider.AZURE.value,
                "name": "Microsoft Azure",
                "status": "coming_soon",
            },
        ]

    def list_connections(self) -> list[CloudConnection]:
        return (
            self._db.query(CloudConnection)
            .order_by(CloudConnection.created_at.desc())
            .all()
        )

    def get_connection(self, connection_id: UUID) -> CloudConnection | None:
        return (
            self._db.query(CloudConnection)
            .filter(CloudConnection.id == connection_id)
            .first()
        )

    def create_connection(
        self,
        *,
        provider: str,
        display_name: str,
        scope_identifier: str,
        auth_mode: str,
        auth_config: dict[str, Any],
        enforcement_enabled: bool,
        created_by: str,
    ) -> CloudConnection:
        if auth_mode not in {m.value for m in AuthMode}:
            raise ValueError("invalid auth_mode")
        if "private_key" in auth_config:
            raise ValueError("service account keys are not accepted")

        conn = CloudConnection(
            provider=provider,
            display_name=display_name,
            scope_type="project",
            scope_identifier=scope_identifier,
            auth_mode=auth_mode,
            encrypted_config=encrypt_connection_config(auth_config),
            status=ConnectionStatus.PENDING_VERIFICATION.value,
            capabilities={},
            enforcement_enabled=enforcement_enabled,
            created_by=created_by,
        )
        self._db.add(conn)
        self._db.flush()
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=created_by,
            actor_role="user",
            action="cloud_governance.connection.create",
            target={"cloud_connection_id": str(conn.id)},
        )
        return conn

    def serialize_connection(self, conn: CloudConnection) -> dict[str, Any]:
        config = decrypt_connection_config(conn.encrypted_config)
        return {
            "id": str(conn.id),
            "provider": conn.provider,
            "display_name": conn.display_name,
            "scope_type": conn.scope_type,
            "scope_identifier": conn.scope_identifier,
            "auth_mode": conn.auth_mode,
            "auth_config_redacted": redact_auth_config_for_api(config),
            "status": conn.status,
            "capabilities": conn.capabilities or {},
            "health": conn.health,
            "enforcement_enabled": conn.enforcement_enabled,
            "last_verified_at": conn.last_verified_at.isoformat()
            if conn.last_verified_at
            else None,
            "last_sync_at": conn.last_sync_at.isoformat() if conn.last_sync_at else None,
            "created_by": conn.created_by,
            "created_at": conn.created_at.isoformat() if conn.created_at else None,
        }

    def verify_connection(self, connection_id: UUID, *, actor_id: str) -> dict[str, Any]:
        conn = self._require_connection(connection_id)
        connector = _get_connector(conn.provider)
        scope = (
            f"projects/{conn.scope_identifier}"
            if conn.scope_type == "project"
            else conn.scope_identifier
        )
        result = connector.verify(scope=scope, auth_config=_auth_payload(conn))
        conn.capabilities = result.capabilities
        conn.health = {**result.health, "diagnostics": result.diagnostics}
        conn.last_verified_at = _utcnow()
        conn.status = (
            ConnectionStatus.CONNECTED.value
            if result.ok
            else ConnectionStatus.DEGRADED.value
        )
        self._db.flush()
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=actor_id,
            actor_role="user",
            action="cloud_governance.connection.verify",
            target={
                "cloud_connection_id": str(conn.id),
                "result": "ok" if result.ok else "degraded",
            },
        )
        record_evidence_link(
            self._db,
            connection_id=conn.id,
            ref_type="connection_verify",
            ref_uri=f"cloud-connection:{conn.id}",
            summary="IAM verification completed",
            evidence_metadata={"ok": result.ok, "capabilities": result.capabilities},
        )
        return {
            "connection": self.serialize_connection(conn),
            "verify": {
                "ok": result.ok,
                "capabilities": result.capabilities,
                "diagnostics": result.diagnostics,
            },
        }

    def enqueue_sync(self, connection_id: UUID, *, actor_id: str) -> dict[str, Any]:
        conn = self._require_connection(connection_id)
        queue = JobQueue(self._db)
        job = queue.enqueue(
            job_type=CloudJobType.CLOUD_ASSET_SYNC.value,
            payload={"connection_id": str(conn.id)},
            idempotency_key=f"cloud-sync:{conn.id}",
            created_by=actor_id,
        )
        run = CloudSyncRun(
            connection_id=conn.id,
            background_job_id=UUID(job["id"]),
            run_type="asset_discovery",
            status=SyncRunStatus.RUNNING.value,
        )
        self._db.add(run)
        self._db.flush()
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=actor_id,
            actor_role="user",
            action="cloud_governance.sync.enqueue",
            target={"cloud_connection_id": str(conn.id)},
        )
        return {"job": job, "sync_run_id": str(run.id)}

    def run_asset_sync(self, connection_id: UUID, *, job: BackgroundJob | None = None) -> dict[str, Any]:
        conn = self._require_connection(connection_id)
        connector = _get_connector(conn.provider)
        scope = f"projects/{conn.scope_identifier}"
        auth = _auth_payload(conn)

        run = (
            self._db.query(CloudSyncRun)
            .filter(
                CloudSyncRun.connection_id == conn.id,
                CloudSyncRun.status == SyncRunStatus.RUNNING.value,
            )
            .order_by(CloudSyncRun.started_at.desc())
            .first()
        )
        if run is None:
            run = CloudSyncRun(
                connection_id=conn.id,
                background_job_id=job.id if job else None,
                run_type="asset_discovery",
                status=SyncRunStatus.RUNNING.value,
            )
            self._db.add(run)
            self._db.flush()

        discovered = 0
        updated = 0
        errors: list[str] = []
        page_token: str | None = None
        seen_names: set[str] = set()

        while True:
            batch, page_token, batch_errors = connector.discover_assets(
                scope=scope, auth_config=auth, page_token=page_token
            )
            errors.extend(batch_errors)
            for item in batch:
                seen_names.add(item.canonical_name)
                existing = (
                    self._db.query(CloudDiscoveredAsset)
                    .filter(
                        CloudDiscoveredAsset.connection_id == conn.id,
                        CloudDiscoveredAsset.canonical_resource_name
                        == item.canonical_name,
                    )
                    .first()
                )
                if existing is None:
                    self._db.add(
                        CloudDiscoveredAsset(
                            connection_id=conn.id,
                            canonical_resource_name=item.canonical_name,
                            asset_type=item.asset_type,
                            display_name=item.display_name,
                            location=item.location,
                            config_summary=item.config_summary,
                            status=AssetStatus.CANDIDATE.value,
                            observed_at=_utcnow(),
                        )
                    )
                    discovered += 1
                else:
                    existing.display_name = item.display_name
                    existing.location = item.location
                    existing.config_summary = item.config_summary
                    existing.last_seen_at = _utcnow()
                    existing.observed_at = _utcnow()
                    if existing.status == AssetStatus.NOT_OBSERVED.value:
                        existing.status = AssetStatus.CANDIDATE.value
                    updated += 1
            if not page_token:
                break

        stale = (
            self._db.query(CloudDiscoveredAsset)
            .filter(CloudDiscoveredAsset.connection_id == conn.id)
            .all()
        )
        for asset in stale:
            if asset.canonical_resource_name not in seen_names:
                if asset.status != AssetStatus.RETIRED.value:
                    asset.status = AssetStatus.NOT_OBSERVED.value
                    asset.observed_at = _utcnow()

        run.assets_discovered = discovered
        run.assets_updated = updated
        run.partial_errors = errors or None
        run.completed_at = _utcnow()
        run.status = (
            SyncRunStatus.PARTIAL.value if errors else SyncRunStatus.SUCCEEDED.value
        )
        conn.last_sync_at = _utcnow()
        self._db.flush()
        record_evidence_link(
            self._db,
            connection_id=conn.id,
            ref_type="asset_sync",
            ref_uri=f"cloud-sync-run:{run.id}",
            summary="Asset discovery sync completed",
            evidence_metadata={
                "discovered": discovered,
                "updated": updated,
                "errors": errors,
            },
            linked_entity_type="cloud_sync_run",
            linked_entity_id=run.id,
        )
        return {
            "sync_run_id": str(run.id),
            "discovered": discovered,
            "updated": updated,
            "errors": errors,
        }

    def list_assets(self, connection_id: UUID) -> list[CloudDiscoveredAsset]:
        self._require_connection(connection_id)
        return (
            self._db.query(CloudDiscoveredAsset)
            .filter(CloudDiscoveredAsset.connection_id == connection_id)
            .order_by(CloudDiscoveredAsset.last_seen_at.desc())
            .all()
        )

    def correlate_asset(
        self, connection_id: UUID, asset_id: UUID, ai_system_id: UUID, *, actor_id: str
    ) -> CloudDiscoveredAsset:
        conn = self._require_connection(connection_id)
        asset = (
            self._db.query(CloudDiscoveredAsset)
            .filter(
                CloudDiscoveredAsset.id == asset_id,
                CloudDiscoveredAsset.connection_id == conn.id,
            )
            .first()
        )
        if asset is None:
            raise ValueError("asset not found")
        asset.ai_system_id = ai_system_id
        asset.status = AssetStatus.LINKED.value
        self._db.flush()
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=actor_id,
            actor_role="user",
            action="cloud_governance.asset.correlate",
            target={"cloud_discovered_asset_id": str(asset.id)},
        )
        return asset

    def coverage(self, connection_id: UUID) -> dict[str, Any]:
        conn = self._require_connection(connection_id)
        matrix = evaluate_connection_coverage(self._db, conn)
        snap = CloudCoverageSnapshot(
            connection_id=conn.id,
            methodology=matrix["methodology"],
            control_matrix=matrix,
        )
        self._db.add(snap)
        self._db.flush()
        return matrix

    def inspect_model_armor(self, connection_id: UUID) -> dict[str, Any]:
        conn = self._require_connection(connection_id)
        connector = _get_connector(conn.provider)
        scope = f"projects/{conn.scope_identifier}"
        return connector.inspect_model_armor(scope=scope, auth_config=_auth_payload(conn))

    def preview_policy_change(
        self, connection_id: UUID, desired: dict[str, Any], *, actor_id: str
    ) -> CloudPolicyChangeRequest:
        conn = self._require_connection(connection_id)
        connector = _get_connector(conn.provider)
        scope = f"projects/{conn.scope_identifier}"
        preview = connector.preview_model_armor_change(
            scope=scope, auth_config=_auth_payload(conn), desired=desired
        )
        row = CloudPolicyChangeRequest(
            connection_id=conn.id,
            target_resource=desired.get("target_resource", scope),
            desired_payload=desired,
            preview_payload=preview,
            status=PolicyChangeStatus.PREVIEW.value,
            created_by=actor_id,
        )
        self._db.add(row)
        self._db.flush()
        row.optimistic_version = policy_action_digest(
            policy_change_id=row.id,
            connection_id=conn.id,
            desired=desired,
        )
        self._db.flush()
        return row

    def bind_policy_change_approval(
        self, change_id: UUID, approval_request_id: UUID, *, actor_id: str
    ) -> CloudPolicyChangeRequest:
        change = self._require_policy_change(change_id)
        approval = (
            self._db.query(ApprovalRequest)
            .filter(ApprovalRequest.id == approval_request_id)
            .first()
        )
        if approval is None:
            raise ValueError("approval request not found")
        if approval.integration_type != IntegrationType.CLOUD_GOVERNANCE.value:
            raise ValueError("approval integration_type must be cloud_governance")
        expected = change.optimistic_version or policy_action_digest(
            policy_change_id=change.id,
            connection_id=change.connection_id,
            desired=change.desired_payload,
        )
        if approval.action_digest != expected:
            raise ValueError("approval digest does not match policy change")
        change.approval_request_id = approval_request_id
        change.status = PolicyChangeStatus.PENDING_APPROVAL.value
        self._db.flush()
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=actor_id,
            actor_role="user",
            action="cloud_governance.policy.bind_approval",
            target={"cloud_policy_change_request_id": str(change.id)},
        )
        return change

    def approval_payload_hint(self, change_id: UUID) -> dict[str, Any]:
        change = self._require_policy_change(change_id)
        digest = change.optimistic_version or policy_action_digest(
            policy_change_id=change.id,
            connection_id=change.connection_id,
            desired=change.desired_payload,
        )
        return {
            "integration_type": IntegrationType.CLOUD_GOVERNANCE.value,
            "action_digest": digest,
            "canonical_action_payload": {
                "integration_type": IntegrationType.CLOUD_GOVERNANCE.value,
                "parameters": {
                    "policy_change_id": str(change.id),
                    "connection_id": str(change.connection_id),
                },
            },
            "instructions": (
                "Create and approve a matching ApprovalRequest before apply. "
                "Use the same action_digest on the approval row."
            ),
        }

    def apply_policy_change(self, change_id: UUID, *, actor_id: str) -> CloudPolicyChangeRequest:
        change = self._require_policy_change(change_id)
        conn = self._require_connection(change.connection_id)
        if not conn.enforcement_enabled:
            raise ValueError("enforcement is not enabled on this connection")
        if change.status not in {
            PolicyChangeStatus.PENDING_APPROVAL.value,
            PolicyChangeStatus.APPROVED.value,
        }:
            raise ValueError(f"cannot apply from status {change.status}")
        if not change.approval_request_id:
            raise ValueError("approval must be bound before apply")
        approval = (
            self._db.query(ApprovalRequest)
            .filter(ApprovalRequest.id == change.approval_request_id)
            .with_for_update()
            .first()
        )
        if approval is None:
            raise ValueError("approval request not found")
        digest = change.optimistic_version or policy_action_digest(
            policy_change_id=change.id,
            connection_id=change.connection_id,
            desired=change.desired_payload,
        )
        validate_approval_for_policy_apply(
            approval, expected_digest=digest, policy_change_id=change.id
        )
        connector = _get_connector(conn.provider)
        scope = f"projects/{conn.scope_identifier}"
        change.status = PolicyChangeStatus.APPLYING.value
        self._db.flush()
        etag = (change.preview_payload or {}).get("current", {}).get("floor_settings", {})
        etag = etag.get("etag") if isinstance(etag, dict) else None
        apply_result = connector.apply_model_armor_change(
            scope=scope,
            auth_config=_auth_payload(conn),
            desired=change.desired_payload,
            etag=etag,
        )
        if not apply_result.get("applied"):
            change.status = PolicyChangeStatus.FAILED.value
            change.apply_result = apply_result
            self._db.flush()
            raise ValueError("apply failed: " + "; ".join(apply_result.get("limitations", [])))
        change.apply_result = apply_result
        change.status = PolicyChangeStatus.APPLIED.value
        mark_approval_consumed(approval)
        self._db.flush()
        record_evidence_link(
            self._db,
            connection_id=conn.id,
            ref_type="policy_apply",
            ref_uri=f"cloud-policy-change:{change.id}",
            summary="Model Armor change applied",
            evidence_metadata={"apply_result": apply_result},
            linked_entity_type="cloud_policy_change_request",
            linked_entity_id=change.id,
        )
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=actor_id,
            actor_role="user",
            action="cloud_governance.policy.apply",
            target={"cloud_policy_change_request_id": str(change.id)},
        )
        return change

    def verify_policy_change(self, change_id: UUID, *, actor_id: str) -> dict[str, Any]:
        change = self._require_policy_change(change_id)
        conn = self._require_connection(change.connection_id)
        if change.status != PolicyChangeStatus.APPLIED.value:
            raise ValueError("policy change must be APPLIED before verify")
        observed = self.inspect_model_armor(change.connection_id)
        desired_floor = change.desired_payload.get("floor_settings")
        observed_floor = observed.get("floor_settings")
        verified = desired_floor == observed_floor if desired_floor else observed.get(
            "supported"
        )
        verify_result = {
            "verified": bool(verified),
            "observed": observed,
            "desired": change.desired_payload,
        }
        change.verify_result = verify_result
        change.status = (
            PolicyChangeStatus.VERIFIED.value
            if verified
            else PolicyChangeStatus.FAILED.value
        )
        self._db.flush()
        record_evidence_link(
            self._db,
            connection_id=conn.id,
            ref_type="policy_verify",
            ref_uri=f"cloud-policy-change:{change.id}",
            summary="Post-apply verification",
            complete=bool(verified),
            evidence_metadata=verify_result,
            linked_entity_type="cloud_policy_change_request",
            linked_entity_id=change.id,
        )
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=actor_id,
            actor_role="user",
            action="cloud_governance.policy.verify",
            target={
                "cloud_policy_change_request_id": str(change.id),
                "verified": verified,
            },
        )
        return verify_result

    def enqueue_drift_check(self, connection_id: UUID, *, actor_id: str) -> dict[str, Any]:
        conn = self._require_connection(connection_id)
        queue = JobQueue(self._db)
        job = queue.enqueue(
            job_type=CloudJobType.CLOUD_DRIFT_CHECK.value,
            payload={"connection_id": str(conn.id)},
            idempotency_key=f"cloud-drift:{conn.id}",
            created_by=actor_id,
        )
        self._db.flush()
        return {"job": job}

    def disconnect(self, connection_id: UUID, *, actor_id: str) -> CloudConnection:
        conn = self._require_connection(connection_id)
        conn.status = ConnectionStatus.DISCONNECTED.value
        conn.encrypted_config = encrypt_connection_config({"revoked": True})
        self._db.flush()
        log_admin_audit(
            trace_id=generate_trace_id(),
            actor_user_id=actor_id,
            actor_role="user",
            action="cloud_governance.connection.disconnect",
            target={"cloud_connection_id": str(conn.id)},
        )
        return conn

    def onboarding_terraform(self, connection_id: UUID) -> str:
        conn = self._require_connection(connection_id)
        return render_terraform(str(conn.id), project_id=conn.scope_identifier)

    def onboarding_cloud_shell(self, connection_id: UUID) -> dict[str, Any]:
        conn = self._require_connection(connection_id)
        return cloud_shell_instructions(
            project_id=conn.scope_identifier, connection_id=str(conn.id)
        )

    def list_sync_runs(self, connection_id: UUID) -> list[CloudSyncRun]:
        self._require_connection(connection_id)
        return (
            self._db.query(CloudSyncRun)
            .filter(CloudSyncRun.connection_id == connection_id)
            .order_by(CloudSyncRun.started_at.desc())
            .all()
        )

    def list_drift(self, connection_id: UUID) -> list[CloudDriftFinding]:
        self._require_connection(connection_id)
        return (
            self._db.query(CloudDriftFinding)
            .filter(CloudDriftFinding.connection_id == connection_id)
            .order_by(CloudDriftFinding.detected_at.desc())
            .all()
        )

    def list_evidence(self, connection_id: UUID) -> list[CloudEvidenceLink]:
        self._require_connection(connection_id)
        return (
            self._db.query(CloudEvidenceLink)
            .filter(CloudEvidenceLink.connection_id == connection_id)
            .order_by(CloudEvidenceLink.created_at.desc())
            .all()
        )

    def _require_connection(self, connection_id: UUID) -> CloudConnection:
        conn = self.get_connection(connection_id)
        if conn is None:
            raise ValueError("connection not found")
        return conn

    def _require_policy_change(self, change_id: UUID) -> CloudPolicyChangeRequest:
        row = (
            self._db.query(CloudPolicyChangeRequest)
            .filter(CloudPolicyChangeRequest.id == change_id)
            .first()
        )
        if row is None:
            raise ValueError("policy change not found")
        return row
