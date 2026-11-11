"""Domain types for cloud governance connectors."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

CLOUD_GOV_API_PREFIX: Final[str] = "/v1/cloud-governance"


class CloudProvider(StrEnum):
    GCP = "gcp"
    AWS = "aws"
    AZURE = "azure"


class ConnectionStatus(StrEnum):
    DRAFT = "draft"
    PENDING_VERIFICATION = "pending_verification"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    REVOKED = "revoked"
    DISCONNECTED = "disconnected"


class AuthMode(StrEnum):
    WIF_EXTERNAL_ACCOUNT = "wif_external_account"
    ADC_IMPERSONATION = "adc_impersonation"
    CUSTOMER_ADC = "customer_adc"


class AssetStatus(StrEnum):
    CANDIDATE = "candidate"
    LINKED = "linked"
    NOT_OBSERVED = "not_observed"
    RETIRED = "retired"


class SyncRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class PolicyChangeStatus(StrEnum):
    PREVIEW = "preview"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    APPLYING = "applying"
    APPLIED = "applied"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CloudJobType(StrEnum):
    CLOUD_ASSET_SYNC = "CLOUD_ASSET_SYNC"
    CLOUD_EVIDENCE_COLLECT = "CLOUD_EVIDENCE_COLLECT"
    CLOUD_DRIFT_CHECK = "CLOUD_DRIFT_CHECK"


GCP_DISCOVERY_ASSET_TYPES: Final[tuple[str, ...]] = (
    "aiplatform.googleapis.com/Model",
    "aiplatform.googleapis.com/Endpoint",
    "aiplatform.googleapis.com/Dataset",
    "aiplatform.googleapis.com/ReasoningEngine",
    "aiplatform.googleapis.com/PipelineJob",
    "aiplatform.googleapis.com/Featurestore",
    "aiplatform.googleapis.com/Index",
    "aiplatform.googleapis.com/IndexEndpoint",
    "aiplatform.googleapis.com/NotebookRuntime",
)
