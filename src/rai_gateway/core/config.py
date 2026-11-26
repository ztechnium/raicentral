"""Configuration management for RAI Gateway."""

from typing import Any, Optional
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Environment Configuration
    environment: str = "development"  # development | staging | production

    # Database Configuration
    database_url: str = "postgresql://localhost:5432/rai_gateway"
    database_pool_size: int = 20
    database_max_overflow: int = 30

    # Feature Flags (Global)
    enable_cache: bool = True
    enable_cases: bool = True
    enable_rag: bool = False
    enable_judge: bool = False
    enable_xai: bool = True
    enable_ethics_eval: bool = True

    # Security Configuration
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # External Services - LLM Providers (Multi-Provider Support)
    # OpenAI
    llm_provider: str = "openai"
    llm_provider_url: str = "https://api.openai.com/v1"
    llm_provider_model: str = "gpt-3.5-turbo"
    llm_provider_api_key: Optional[str] = None

    # Azure OpenAI
    azure_openai_api_key: Optional[str] = None
    azure_openai_endpoint: Optional[str] = None
    azure_openai_deployment: Optional[str] = None
    azure_openai_api_version: str = "2024-02-15-preview"

    # Anthropic (Claude)
    anthropic_api_key: Optional[str] = None

    # Google AI (Gemini API)
    google_ai_api_key: Optional[str] = None
    google_ai_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # AWS Bedrock
    aws_region: str = "us-east-1"  # boto3 credentials from environment/IAM
    bedrock_runtime_url: Optional[str] = None

    # Google Vertex AI
    gcp_project_id: Optional[str] = None
    gcp_location: str = "us-central1"
    # Default location used when the client does not send one. When true, the gateway
    # ignores the client's `locations/<x>` and always uses gcp_location.
    gcp_pin_location: bool = False
    # Service-account credentials the gateway uses to call Vertex AI (server-side only).
    # Either a path (standard GOOGLE_APPLICATION_CREDENTIALS) or raw JSON in one env var.
    # When neither is set the gateway falls back to the GCE/GKE metadata server.
    google_application_credentials: Optional[str] = None
    gcp_service_account_json: Optional[str] = None

    # Ollama (local/on-prem)
    ollama_endpoint: str = "http://localhost:11434"
    provider_gateway_ollama_enabled: bool = False
    provider_gateway_ollama_allowlist: str = "localhost,127.0.0.1"

    # Data Classification Service
    data_classification_url: Optional[str] = None

    # Runtime Auth
    # If true, /v1/execute requires a valid X-API-Key.
    require_api_key: bool = False

    # Cache Configuration
    redis_url: str = "redis://localhost:6379"
    cache_ttl_seconds: int = 3600

    # Content Rule evaluator (LLM-as-judge). When CONTENT_EVALUATOR_PROVIDER is set it is
    # the default for every AI system that has no evaluator row of its own, and
    # CONTENT_EVALUATOR_API_KEY / _BASE_URL fill in what a row leaves blank.
    # provider: mock | openai | openai_compatible | deepseek | gemini | vertex
    content_evaluator_provider: Optional[str] = None
    content_evaluator_model: Optional[str] = None
    content_evaluator_api_key: Optional[str] = None
    content_evaluator_base_url: Optional[str] = None
    # Vertex region for the evaluator model (Gemini 3.x models are global-only).
    content_evaluator_location: str = "global"
    # Optional dedicated Google project / service-account key for the evaluator
    # (defaults to GCP_PROJECT_ID and the gateway's own service account).
    content_evaluator_gcp_project_id: Optional[str] = None
    content_evaluator_service_account_json: Optional[str] = None
    content_evaluator_timeout_seconds: int = 30
    content_evaluator_temperature: float = 0.0
    content_evaluator_fail_closed: bool = False
    content_evaluator_min_confidence_to_intervene: float = 0.9

    # Unified internal AI. ONE environment-only configuration shared by every internal AI
    # feature (content evaluation, AI onboarding, future features). See core/ai_provider.py.
    # provider: openai | anthropic | gemini | vertex | openai_compatible
    # vertex authenticates with RAI_AI_API_KEY *or* a Google service account
    # (RAI_AI_GCP_SERVICE_ACCOUNT_JSON, else GCP_SERVICE_ACCOUNT_JSON / key file / metadata).
    rai_ai_provider: Optional[str] = None
    rai_ai_model: Optional[str] = None
    rai_ai_models: str = ""  # extra allowed models, comma separated
    rai_ai_api_key: Optional[str] = None
    rai_ai_base_url: Optional[str] = None
    rai_ai_gcp_project_id: Optional[str] = None  # defaults to GCP_PROJECT_ID
    rai_ai_location: Optional[str] = None  # defaults to GCP_LOCATION (Gemini 3.x: global)
    rai_ai_gcp_service_account_json: Optional[str] = None
    rai_ai_timeout_seconds: float = 30.0
    rai_ai_temperature: float = 0.0
    rai_ai_max_output_tokens: int = 8192
    rai_ai_structured_mode: Optional[str] = None  # JSON_SCHEMA | JSON_OBJECT | JSON_PROMPT
    rai_ai_allow_private_network: bool = False

    # Observability
    otel_endpoint: Optional[str] = None
    log_level: str = "INFO"

    # Security & Compliance
    evidence_encryption_key: str = "change-this-encryption-key"
    siem_endpoint: Optional[str] = None

    # Internal Admin Console (Release 1 Option A)
    # Simple token gate for internal admin pages/APIs. Prefer JWT/SSO later.
    admin_token: Optional[str] = None
    admin_session_minutes: int = 480
    admin_bootstrap_enabled: bool = True  # Set false in production
    admin_bootstrap_email: str = "admin@raigateway.local"
    admin_bootstrap_password: str = "ChangeMe123!"
    admin_bootstrap_name: str = "General Admin"
    admin_bootstrap_role: str = "admin"
    # Sample ACME / public-agency pilots. Off so a fresh install stays empty.
    enable_reference_pilot_seed: bool = False

    # Step-up verification for sensitive admin actions.
    # Shared-secret header is a non-prod escape hatch only — enable explicitly.
    require_step_up: bool = True
    step_up_token: Optional[str] = None
    allow_shared_secret_step_up: bool = False

    # Enterprise IdP for admin login / CRITICAL approval MFA (ACR/AMR/auth_time)
    admin_oidc_issuer: Optional[str] = None
    admin_oidc_audience: Optional[str] = None
    admin_oidc_jwks_url: Optional[str] = None
    admin_oidc_email_claim: str = "email"

    # WP5 evidence content handling (org defaults).
    # Prompt, model request/response, and agent output stay REDACTED (text kept,
    # secrets scrubbed) even when demo defaults are off. Tool parameters and
    # tool results fall back to METADATA_ONLY unless demo defaults are on.
    evidence_use_demo_content_defaults: bool = True
    evidence_prompt_handling_mode: Optional[str] = None
    evidence_model_request_handling_mode: Optional[str] = None
    evidence_model_response_handling_mode: Optional[str] = None
    evidence_agent_output_handling_mode: Optional[str] = None
    evidence_tool_parameters_handling_mode: Optional[str] = None
    evidence_tool_result_handling_mode: Optional[str] = None

    # WP10 productization
    sdk_rate_limit_per_minute: int = 600
    sdk_auth_failure_limit_per_minute: int = 30
    object_storage_backend: str = "postgres"  # postgres | s3 | minio
    object_storage_bucket: Optional[str] = None
    object_storage_endpoint: Optional[str] = None
    oidc_jwks_url: Optional[str] = None
    fail_closed_on_rai_unavailable: bool = True
    # Runtime gateway rollout posture: OBSERVE | WARN | ENFORCE. Unknown values
    # are treated as ENFORCE (fail closed).
    gateway_enforcement_mode: str = "ENFORCE"
    # Shared secret for /v1/network-observation/events collectors (flow logs, eBPF, etc.).
    network_observation_ingest_token: Optional[str] = None

    # Provider proxy (infrastructure gateway) transport limits.
    provider_gateway_max_body_bytes: int = 10 * 1024 * 1024
    # Pre-call token budgets: also reserve the caller's max-output-tokens cap. Off by
    # default (output is reconciled to real usage after the call); on is stricter and
    # can deny small budgets before any usage is recorded.
    provider_gateway_reserve_max_output_tokens: bool = False
    provider_gateway_max_header_bytes: int = 32 * 1024
    provider_gateway_max_response_bytes: int = 64 * 1024 * 1024
    provider_gateway_connect_timeout_seconds: float = 10.0
    provider_gateway_read_timeout_seconds: float = 120.0
    provider_gateway_max_stream_duration_seconds: float = 600.0
    provider_gateway_max_concurrent_streams: int = 500
    provider_gateway_max_concurrent_per_runtime: int = 20
    provider_gateway_max_concurrent_per_provider: int = 100
    provider_gateway_rate_limit_per_runtime_per_minute: int = 120
    provider_gateway_policy_cache_ttl_seconds: int = 60
    provider_gateway_policy_cache_fast_invalidate_ttl_seconds: int = 5
    provider_gateway_outbound_allowlist: str = ""
    # Private link / PSC host suffixes allowed when not in the public catalog.
    provider_gateway_private_endpoint_suffixes: str = (
        ".privatelink.azure.com,.p.googleapis.com,.private.googleapis.com"
    )
    provider_gateway_verify_tls: bool = True
    provider_gateway_ca_bundle_path: Optional[str] = None
    provider_gateway_drain_seconds: int = 30
    gateway_rollout_phase: str = "PROXY_OPTIONAL"
    provider_gateway_max_retries: int = 2
    # When true, upstream provider keys come from credential refs + grants only.
    provider_gateway_isolated_credentials: bool = False
    approval_webhooks_enabled: bool = True
    webhook_allow_private_hosts: bool = False
    webhook_allow_http: bool = False

    # mTLS trust boundary — never trust public cert headers alone
    mtls_trusted_proxy_mode: bool = False
    mtls_trusted_proxy_secret: Optional[str] = None
    # Dev/demo only. Production must keep this false.
    mtls_allow_insecure_identity_header: bool = False

    # MCP adapter: PRODUCTION denies missing endpoints; DEMO_SIMULATED allows sim.
    mcp_adapter_mode: str = "PRODUCTION"  # PRODUCTION | DEMO_SIMULATED
    # Comma-separated hostnames (exact or *.suffix). Empty = no hostname allowlist.
    mcp_allowed_hosts: Optional[str] = None
    # Allow RFC1918 / loopback / link-local targets (dev only; keep false in prod).
    mcp_allow_private_hosts: bool = False
    # Allow http:// endpoints (dev only; production should use https).
    mcp_allow_http: bool = False

    # Externally visible base URL of this API, including any reverse proxy path prefix
    # (https://rai.example.com/api). The proxy is expected to strip that prefix. Drives
    # SDK setup URLs, the refresh cookie path, the API docs and MCP discovery.
    api_public_base_url: str = ""

    # RAI management MCP server (Streamable HTTP + OAuth for Cursor and other MCP hosts)
    mcp_server_enabled: bool = True
    # Public base URL used for OAuth issuer/resource metadata. Empty: api_public_base_url
    # when it is HTTPS (or loopback), else http://127.0.0.1:8001.
    mcp_server_public_base_url: str = ""
    mcp_server_mount_path: str = "/mcp"
    mcp_oauth_enable_dcr: bool = True
    mcp_oauth_scopes: str = "rai:read,rai:manage"

    # Human approval step-up (server-derived; never trust caller body alone)
    approval_minimum_acr: Optional[str] = None
    approval_required_amr: Optional[str] = None  # comma-separated
    approval_max_auth_age_seconds: Optional[int] = None

    @field_validator("approval_max_auth_age_seconds", mode="before")
    @classmethod
    def _empty_optional_int(cls, value: Any) -> Any:
        """Compose often passes APPROVAL_MAX_AUTH_AGE_SECONDS=; treat as unset."""
        if value is None or value == "":
            return None
        return value

    # SaaS human session authentication
    auth_access_token_minutes: int = 480  # 8h access JWT; refresh token extends further
    auth_refresh_token_days: int = 30
    auth_set_cookies: bool = False
    auth_cookie_secure: bool = True
    auth_cookie_samesite: str = "lax"
    auth_access_cookie_name: str = "rai_access"
    auth_refresh_cookie_name: str = "rai_refresh"
    app_public_url: str = "http://localhost:3000"

    @model_validator(mode="after")
    def _resolve_public_urls(self) -> "Settings":
        api = self.api_public_base_url.strip().rstrip("/")
        mcp = self.mcp_server_public_base_url.strip().rstrip("/")
        if not mcp:
            # OAuth issuers must be HTTPS or loopback; anything else would stop startup.
            parsed = urlsplit(api)
            usable = (parsed.scheme == "https" and bool(parsed.netloc)) or (
                parsed.scheme == "http"
                and parsed.hostname in ("localhost", "127.0.0.1", "::1")
            )
            mcp = api if usable else "http://127.0.0.1:8001"
        self.api_public_base_url = api
        self.mcp_server_public_base_url = mcp
        return self

    @property
    def api_public_path_prefix(self) -> str:
        """Path the reverse proxy strips before requests arrive ('' at the root)."""
        path = urlsplit(self.api_public_base_url).path.rstrip("/")
        return path if path.startswith("/") else ""

    # CORS Configuration (Phase 23)
    # Comma-separated list of allowed origins (exact match)
    # Example: https://app.example.com,https://admin.example.com
    # Use "*" for development only. NEVER use "*" with credentials in production.
    cors_allowed_origins: str = "*"
    cors_allow_credentials: bool = True
    cors_allow_methods: str = "*"
    # Include X-Admin-Token — control-plane-ui sends it on every fetch when
    # NEXT_PUBLIC_ADMIN_TOKEN is set; omitting it fails browser preflight (400).
    # Include X-RAI-Agent-Key — the enforcement test console sends the runtime
    # credential from the browser; omitting it fails that preflight (400).
    # Include Idempotency-Key — onboarding writes (AI test, sources) send it directly from the browser.
    cors_allow_headers: str = (
        "Content-Type,Authorization,X-CSRF-Token,X-Admin-Token,X-RAI-Agent-Key,Idempotency-Key"
    )
    cors_max_age: int = 600

    # Transactional email (Resend SMTP by default; provider-agnostic transport)
    email_enabled: bool = False
    email_provider: str = "resend"
    email_from_name: str = "RAI Control Plane"
    email_from_address: Optional[str] = None
    email_reply_to: Optional[str] = None
    smtp_host: str = "smtp.resend.com"
    smtp_port: int = 465
    smtp_username: str = "resend"
    smtp_password: Optional[str] = None
    smtp_use_ssl: bool = True
    smtp_use_starttls: bool = False
    smtp_timeout_seconds: float = 15.0
    email_verification_ttl_minutes: int = 60
    password_reset_ttl_minutes: int = 30
    email_docs_url: str = "https://docs.rai.example.com"

    # Onboarding intake (Step 02): private filesystem original-source storage.
    onboarding_source_storage_root: str = "./data/onboarding-sources"
    onboarding_max_file_bytes: int = 25 * 1024 * 1024
    onboarding_max_sources_per_session: int = 20
    onboarding_max_session_bytes: int = 200 * 1024 * 1024
    onboarding_max_text_chars: int = 50_000
    onboarding_upload_timeout_seconds: float = 600.0
    onboarding_orphan_min_age_seconds: float = 24 * 3600.0
    onboarding_multipart_overhead_bytes: int = 64 * 1024
    onboarding_job_lease_seconds: float = 60.0
    onboarding_job_renew_seconds: float = 15.0
    onboarding_job_max_attempts: int = 5

    # Onboarding extraction (Step 03): parser resource bounds. They are part of the
    # extraction identity, so changing one produces a genuinely new extraction.
    onboarding_parser_wall_seconds: float = 120.0
    onboarding_parser_memory_bytes: int = 512 * 1024 * 1024
    onboarding_pdf_max_pages: int = 300
    onboarding_ooxml_max_entries: int = 10_000
    onboarding_ooxml_max_total_bytes: int = 100 * 1024 * 1024
    onboarding_ooxml_max_member_bytes: int = 25 * 1024 * 1024
    onboarding_ooxml_max_ratio: int = 100
    onboarding_xlsx_max_sheets: int = 50
    onboarding_tabular_max_rows: int = 10_000
    onboarding_tabular_max_columns: int = 200
    onboarding_max_cells_visited: int = 200_000
    onboarding_max_nonempty_cells: int = 50_000
    onboarding_extract_output_bytes_per_source: int = 16 * 1024 * 1024
    onboarding_extract_output_bytes_per_run: int = 64 * 1024 * 1024

    # Explicit onboarding AI configuration (Step 03). A deployment-managed registry of
    # approved targets (JSON list). No default provider, model or ambient credential.
    onboarding_ai_targets_json: str = ""
    onboarding_ai_probe_timeout_seconds: float = 30.0
    onboarding_ai_probe_max_response_bytes: int = 64 * 1024
    onboarding_ai_probe_default_max_tokens: int = 256
    # Step 04: deterministic partition parser for CI (no live provider calls).
    onboarding_discovery_fixture: bool = False
    # Live inventory/governance model calls: per-call deadline (the lease is renewed to match).
    onboarding_ai_call_timeout_seconds: float = 120.0
    onboarding_governance_fixture: bool = False
    # Step 08: incremental update workflow (default off until worker + migrations land).
    onboarding_update_workflow_enabled: bool = False

    @model_validator(mode="after")
    def _validate_onboarding_limits(self) -> "Settings":
        positive = {
            "onboarding_max_file_bytes": self.onboarding_max_file_bytes,
            "onboarding_max_sources_per_session": self.onboarding_max_sources_per_session,
            "onboarding_max_session_bytes": self.onboarding_max_session_bytes,
            "onboarding_max_text_chars": self.onboarding_max_text_chars,
            "onboarding_upload_timeout_seconds": self.onboarding_upload_timeout_seconds,
            "onboarding_job_lease_seconds": self.onboarding_job_lease_seconds,
            "onboarding_job_renew_seconds": self.onboarding_job_renew_seconds,
            "onboarding_job_max_attempts": self.onboarding_job_max_attempts,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if (
            self.onboarding_orphan_min_age_seconds
            <= self.onboarding_upload_timeout_seconds
        ):
            raise ValueError(
                "onboarding_orphan_min_age_seconds must exceed "
                "onboarding_upload_timeout_seconds"
            )
        if self.onboarding_job_renew_seconds >= self.onboarding_job_lease_seconds:
            raise ValueError(
                "onboarding_job_renew_seconds must be shorter than the lease"
            )
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra environment variables


# Global settings instance
settings = Settings()
