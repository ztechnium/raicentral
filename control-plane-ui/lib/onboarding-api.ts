import { ADMIN_AUTH_TOKEN_STORAGE_KEY, refreshAccessTokenIfNeeded } from "@/lib/api";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";
const BASE = `${API_BASE_URL}/v1/productization/onboarding/sessions`;

export type SessionView =
  "DRAFT" | "QUEUED" | "PREPARING" | "READY_FOR_ANALYSIS" | "PREPARATION_FAILED" | "ARCHIVED";

export type RunState =
  "QUEUED" | "RETRY_SCHEDULED" | "PREPARING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

export type SourceRole = "USER_PROMPT" | "SUPPORTING_MATERIAL";

export interface RevisionSummary {
  id: string;
  revision_number: number;
  media_family: string;
  byte_length: number;
  received_at: string | null;
  /** Present only for callers with `onboarding:sources:read`. */
  display_name?: string;
  sha256?: string;
  created_by?: string;
}

export interface SourceSummary {
  id: string;
  label: string;
  kind: "FILE" | "TEXT";
  role: SourceRole;
  included: boolean;
  created_at: string | null;
  current_revision?: RevisionSummary;
  /** Content readers only. Versioned inputs: overrides never alter originals. */
  parsing_options?: ParsingOptions;
  parsing_options_version?: number;
}

export interface RunSummary {
  id: string;
  run_number: number;
  stage: string;
  state: RunState;
  active: boolean;
  input_revision: number;
  retry_of_run_id: string | null;
  attempt_count: number;
  max_attempts: number;
  source_count: number;
  sources_checked: number | null;
  sources_total: number | null;
  created_at: string | null;
  ended_at: string | null;
  error_code: string | null;
  checked_bytes?: number | null;
  checked_at?: string | null;
  manifest_digest?: string;
}

export interface SessionDetail {
  id: string;
  label: string;
  title?: string;
  lifecycle: "OPEN" | "ARCHIVED";
  session_kind?: "INITIAL" | "UPDATE" | "INVENTORY_CORRECTION" | "POLICY_REVISION";
  baseline_session_id?: string | null;
  update_status?: string | null;
  confirmed_baseline_id?: string | null;
  view: SessionView;
  row_version: number;
  input_revision: number;
  source_count: number;
  total_source_count: number;
  sources: SourceSummary[];
  current_run: RunSummary | null;
  preparation?: PreparationSummary;
  extraction?: ExtractionSummary;
  ai_configuration?: AIStatusSummary;
  allowed_actions: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface SessionListItem {
  id: string;
  label: string;
  lifecycle: "OPEN" | "ARCHIVED";
  view: SessionView;
  row_version: number;
  source_count: number;
  updated_at: string | null;
}

export interface SessionPage {
  items: SessionListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface SourceMutationResult {
  session: SessionDetail;
  source_id: string;
  revision_id: string;
  replayed: boolean;
}

export interface RunMutationResult {
  session: SessionDetail;
  run: RunSummary;
  replayed: boolean;
}

export interface RevisionHistoryItem extends RevisionSummary {
  current: boolean;
}

export interface RevisionHistory {
  source_id: string;
  kind: "FILE" | "TEXT";
  role: SourceRole;
  included: boolean;
  items: RevisionHistoryItem[];
}

/** Structured server error: safe code, message and retryability. */
export class IntakeApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly retryable = false,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "IntakeApiError";
  }

  get isConflict(): boolean {
    return this.code === "REVISION_CONFLICT";
  }
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function token(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ADMIN_AUTH_TOKEN_STORAGE_KEY);
}

function headers(extra?: Record<string, string>, json = true): Record<string, string> {
  const out: Record<string, string> = { Accept: "application/json", ...extra };
  if (json) out["Content-Type"] = "application/json";
  const t = token();
  if (t) out.Authorization = `Bearer ${t}`;
  return out;
}

async function toError(response: Response): Promise<IntakeApiError> {
  try {
    const body = (await response.json()) as {
      error?: unknown;
      message?: string;
      detail?: unknown;
    };
    if (body.error && typeof body.error === "object") {
      const e = body.error as {
        code?: string;
        message?: string;
        retryable?: boolean;
        details?: Record<string, unknown>;
      };
      return new IntakeApiError(
        e.message ?? "Request failed",
        response.status,
        e.code ?? "ERROR",
        Boolean(e.retryable),
        e.details ?? {},
      );
    }
    const message =
      typeof body.message === "string"
        ? body.message
        : typeof body.detail === "string"
          ? body.detail
          : "Request failed";
    const code = response.status === 403 ? "FORBIDDEN" : "ERROR";
    return new IntakeApiError(message, response.status, code);
  } catch {
    return new IntakeApiError(`Request failed (${response.status})`, response.status, "ERROR");
  }
}

async function request<T>(
  path: string,
  init: { method?: string; body?: unknown; key?: string } = {},
): Promise<T> {
  const build = (): RequestInit => ({
    method: init.method ?? "GET",
    headers: headers(init.key ? { "Idempotency-Key": init.key } : undefined),
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
  let response = await fetch(`${BASE}${path}`, build());
  if (response.status === 401 && (await refreshAccessTokenIfNeeded())) {
    response = await fetch(`${BASE}${path}`, build());
  }
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

export function listSessions(opts: { archived?: boolean; limit?: number; offset?: number } = {}) {
  const q = new URLSearchParams({
    archived: String(Boolean(opts.archived)),
    limit: String(opts.limit ?? 25),
    offset: String(opts.offset ?? 0),
  });
  return request<SessionPage>(`?${q}`);
}

export function createSession(title: string, key: string) {
  return request<SessionDetail>("", { method: "POST", body: { title }, key });
}

export function getSession(id: string) {
  return request<SessionDetail>(`/${id}`);
}

export function renameSession(id: string, title: string, expectedRowVersion: number) {
  return request<SessionDetail>(`/${id}`, {
    method: "PATCH",
    body: { title, expected_row_version: expectedRowVersion },
  });
}

export function setArchived(id: string, archive: boolean, expectedRowVersion: number) {
  return request<SessionDetail>(`/${id}/${archive ? "archive" : "restore"}`, {
    method: "POST",
    body: { expected_row_version: expectedRowVersion },
  });
}

export function saveTextSource(
  id: string,
  args: {
    role: SourceRole;
    text: string;
    title?: string;
    sourceId?: string;
    expectedRowVersion: number;
    key: string;
  },
) {
  const body = args.sourceId
    ? { text: args.text, title: args.title, expected_row_version: args.expectedRowVersion }
    : {
        role: args.role,
        text: args.text,
        title: args.title,
        expected_row_version: args.expectedRowVersion,
      };
  const path = args.sourceId
    ? `/${id}/sources/${args.sourceId}/revisions/text`
    : `/${id}/sources/text`;
  return request<SourceMutationResult>(path, { method: "POST", body, key: args.key });
}

export function setSourceIncluded(
  id: string,
  sourceId: string,
  included: boolean,
  expectedRowVersion: number,
) {
  return request<SessionDetail>(`/${id}/sources/${sourceId}`, {
    method: "PATCH",
    body: { included, expected_row_version: expectedRowVersion },
  });
}

/** One file per request, with real upload progress. */
export function uploadFileSource(
  id: string,
  args: {
    file: File;
    sourceId?: string;
    expectedRowVersion: number;
    key: string;
    onProgress?: (fraction: number) => void;
  },
): Promise<SourceMutationResult> {
  const path = args.sourceId
    ? `/${id}/sources/${args.sourceId}/revisions/file`
    : `/${id}/sources/files`;
  const send = (): Promise<SourceMutationResult> =>
    new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("expected_row_version", String(args.expectedRowVersion));
      form.append("file", args.file, args.file.name);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}${path}`);
      for (const [k, v] of Object.entries(headers({ "Idempotency-Key": args.key }, false))) {
        xhr.setRequestHeader(k, v);
      }
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) args.onProgress?.(event.loaded / event.total);
      };
      xhr.onerror = () =>
        reject(new IntakeApiError("Network error while uploading", 0, "NETWORK", true));
      xhr.onload = () => {
        let parsed: unknown = null;
        try {
          parsed = JSON.parse(xhr.responseText);
        } catch {
          // fall through
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(parsed as SourceMutationResult);
          return;
        }
        const err = (parsed as { error?: Record<string, unknown> } | null)?.error;
        reject(
          new IntakeApiError(
            (err?.message as string) ?? `Upload failed (${xhr.status})`,
            xhr.status,
            (err?.code as string) ?? (xhr.status === 401 ? "UNAUTHENTICATED" : "ERROR"),
            Boolean(err?.retryable),
            (err?.details as Record<string, unknown>) ?? {},
          ),
        );
      };
      xhr.send(form);
    });
  return send().catch(async (error: unknown) => {
    if (
      error instanceof IntakeApiError &&
      error.status === 401 &&
      (await refreshAccessTokenIfNeeded())
    ) {
      return send();
    }
    throw error;
  });
}

export function prepareSources(
  id: string,
  args: { expectedRowVersion: number; inputRevision: number; key: string },
) {
  return request<RunMutationResult>(`/${id}/prepare`, {
    method: "POST",
    key: args.key,
    body: {
      expected_row_version: args.expectedRowVersion,
      requested_input_revision: args.inputRevision,
    },
  });
}

export function retryRun(
  id: string,
  runId: string,
  args: { expectedRowVersion: number; inputRevision: number; key: string },
) {
  return request<RunMutationResult>(`/${id}/runs/${runId}/retry`, {
    method: "POST",
    key: args.key,
    body: {
      expected_row_version: args.expectedRowVersion,
      requested_input_revision: args.inputRevision,
    },
  });
}

export function cancelRun(id: string, runId: string) {
  return request<RunMutationResult>(`/${id}/runs/${runId}/cancel`, { method: "POST" });
}

export function getRun(id: string, runId: string) {
  return request<RunSummary>(`/${id}/runs/${runId}`);
}

export function listRevisions(id: string, sourceId: string) {
  return request<RevisionHistory>(`/${id}/sources/${sourceId}/revisions`);
}

/** Authorized original bytes. Tokens never appear in the URL. */
export async function fetchRevisionContent(
  id: string,
  sourceId: string,
  revisionId: string,
): Promise<Blob> {
  const url = `${BASE}/${id}/sources/${sourceId}/revisions/${revisionId}/content`;
  let response = await fetch(url, { headers: headers(undefined, false), cache: "no-store" });
  if (response.status === 401 && (await refreshAccessTokenIfNeeded())) {
    response = await fetch(url, { headers: headers(undefined, false), cache: "no-store" });
  }
  if (!response.ok) throw await toError(response);
  return response.blob();
}

/** Exact saved text of a TEXT revision, decoded as UTF-8. */
export async function fetchRevisionText(
  id: string,
  sourceId: string,
  revisionId: string,
): Promise<string> {
  const blob = await fetchRevisionContent(id, sourceId, revisionId);
  return new TextDecoder("utf-8").decode(await blob.arrayBuffer());
}

// ---------------------------------------------------------------- Step 03 types
export interface ParsingOptions {
  encoding?: "utf-8" | "windows-1256";
  delimiter?: "auto" | "," | ";" | "\t" | "|";
  header_mode?: "preserve_all" | "first_row_header";
}

export interface PreparationSummary {
  state: "NOT_PREPARED" | "QUEUED" | "PREPARING" | "PREPARED" | "FAILED" | "CANCELLED";
  run_id: string | null;
  input_revision: number | null;
  snapshot_version: number | null;
  current: boolean;
  requires_new_preparation: boolean;
}

export type SourceOutcome =
  "COMPLETE" | "PARTIAL" | "NEEDS_OCR" | "NEEDS_INPUT" | "UNSUPPORTED" | "FAILED" | "NOT_PROCESSED";

export interface CoverageSummary {
  unit?: string;
  total?: number;
  extracted?: number;
  gaps?: { code: string; detail?: string }[];
}

export interface ExtractionSourceState {
  label: string;
  state: "PENDING" | "FINAL";
  outcome: SourceOutcome | null;
  block_count: number;
  source_id?: string;
  source_revision_id?: string;
  extraction_id?: string | null;
  reason?: string | null;
  coverage?: CoverageSummary | null;
}

export interface ExtractionSummary {
  state: "NOT_STARTED" | "QUEUED" | "EXTRACTING" | "EXTRACTED" | "FAILED" | "CANCELLED";
  run_id: string | null;
  current: boolean;
  sources: ExtractionSourceState[];
  counts: Record<string, number>;
  attention_count: number;
  all_sources_accounted: boolean;
  coverage_acceptable: boolean;
}

export type AIState =
  | "UNCONFIGURED"
  | "DISABLED"
  | "TARGET_UNAVAILABLE"
  | "CONFIGURED_UNTESTED"
  | "TEST_PENDING"
  | "TEST_PASSED"
  | "TEST_FAILED"
  | "TEST_UNKNOWN"
  | "TEST_STALE";

export interface AIStatusSummary {
  state: AIState;
  enabled: boolean;
  last_test_at?: string;
}

export interface ProbeView {
  id: string;
  probe_number: number;
  configuration_version: number;
  target_key: string | null;
  model: string | null;
  structured_mode: string | null;
  credential_version: number;
  target_fingerprint: string;
  state: string;
  outcome: "PASSED" | "FAILED" | "OUTCOME_UNKNOWN" | "CANCELLED" | null;
  error_code: string | null;
  dispatch_started: boolean;
  latency_ms: number | null;
  /** null means the provider returned no usage: unknown, never zero. */
  usage: Record<string, number> | null;
  created_at: string | null;
  ended_at: string | null;
}

export interface AIConfigDetail extends AIStatusSummary {
  configuration_version?: number;
  target_key?: string | null;
  model?: string | null;
  structured_mode?: string | null;
  max_output_tokens?: number | null;
  target_fingerprint?: string | null;
  credential_configured?: boolean;
  destination_label?: string | null;
  last_test?: ProbeView | null;
  available_targets?: number;
}

export interface ProviderTarget {
  key: string;
  label: string;
  protocol: string;
  destination_label: string;
  models: string[];
  structured_modes: string[];
  max_output_tokens: number;
  credential_configured: boolean;
  credential_version: number;
}

export interface ExtractionItem {
  id: string;
  outcome: SourceOutcome;
  block_count: number;
  coverage: CoverageSummary;
  created_at: string | null;
  is_current_input: boolean;
  source_id?: string;
  source_revision_id?: string;
  revision_number?: number;
  display_name?: string;
  reason?: string | null;
  warnings?: string[];
  extractor_build?: string;
  options?: Record<string, unknown>;
}

export interface ExtractedBlock {
  id: string;
  ordinal: number;
  kind: string;
  text: string;
  cell_data: Record<string, unknown> | null;
  locator: Record<string, unknown> & { type: string };
  content_digest: string;
  warnings: string[];
}

export interface BlocksPage {
  extraction: ExtractionItem & {
    original_sha256: string;
    source_id: string;
    source_revision_id: string;
    normalization: string;
  };
  items: ExtractedBlock[];
  next_after: number | null;
}

export interface Citation {
  source_id: string;
  source_revision_id: string;
  revision_number: number;
  source_sha256: string;
  display_name: string;
  extraction_id: string;
  extractor_build: string;
  block_id: string;
  locator: Record<string, unknown> & { type: string };
  cell_data: Record<string, unknown> | null;
  text_range: { start: number; end: number; unit: "UNICODE_CODE_POINT" };
  excerpt: string;
  block_text: string;
  integrity_ok: boolean;
  is_current_input: boolean;
  historical: boolean;
}

export interface Capabilities {
  formats: { family: string; extensions: string[]; outputs: string }[];
  ocr: boolean;
  parsing_options: { encodings: string[]; delimiters: string[]; header_modes: string[] };
  parser_profile: {
    fingerprint: string;
    build: string;
    dependencies: Record<string, string>;
    limits: Record<string, number>;
  };
  ai: { approved_targets: number };
}

// -------------------------------------------------------------- Step 03 calls
const ONBOARDING_ROOT = `${API_BASE_URL}/v1/productization/onboarding`;

async function rootRequest<T>(
  path: string,
  init: { method?: string; body?: unknown; key?: string } = {},
): Promise<T> {
  const build = (): RequestInit => ({
    method: init.method ?? "GET",
    headers: headers(init.key ? { "Idempotency-Key": init.key } : undefined),
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
  let response = await fetch(`${ONBOARDING_ROOT}${path}`, build());
  if (response.status === 401 && (await refreshAccessTokenIfNeeded())) {
    response = await fetch(`${ONBOARDING_ROOT}${path}`, build());
  }
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

export function getCapabilities() {
  return rootRequest<Capabilities>("/capabilities");
}

export function setParsingOptions(
  id: string,
  sourceId: string,
  args: { options: ParsingOptions; expectedRowVersion: number; key: string },
) {
  return request<SessionDetail>(`/${id}/sources/${sourceId}/parsing-options`, {
    method: "PATCH",
    key: args.key,
    body: { options: args.options, expected_row_version: args.expectedRowVersion },
  });
}

export function extractSources(
  id: string,
  args: {
    expectedRowVersion: number;
    inputRevision: number;
    preparationRunId: string;
    parserFingerprint: string;
    key: string;
  },
) {
  return request<RunMutationResult>(`/${id}/extract`, {
    method: "POST",
    key: args.key,
    body: {
      expected_row_version: args.expectedRowVersion,
      requested_input_revision: args.inputRevision,
      preparation_run_id: args.preparationRunId,
      expected_parser_fingerprint: args.parserFingerprint,
    },
  });
}

export function listExtractions(id: string) {
  return request<{ items: ExtractionItem[]; total: number }>(`/${id}/extractions?limit=100`);
}

export function listBlocks(id: string, extractionId: string, after = -1, limit = 50) {
  return request<BlocksPage>(
    `/${id}/extractions/${extractionId}/blocks?after=${after}&limit=${limit}`,
  );
}

export function getCitation(id: string, blockId: string, range?: { start: number; end: number }) {
  const q = range ? `?start=${range.start}&end=${range.end}` : "";
  return request<Citation>(`/${id}/citations/${blockId}${q}`);
}

export function getAIConfiguration() {
  return rootRequest<AIConfigDetail>("/ai-configuration");
}

export function saveAIConfiguration(body: {
  expected_configuration_version: number;
  enabled: boolean;
  target_key: string | null;
  model: string | null;
  structured_mode: string | null;
  max_output_tokens: number | null;
}) {
  return rootRequest<AIConfigDetail>("/ai-configuration", { method: "PUT", body });
}

export function listProviderTargets() {
  return rootRequest<{ items: ProviderTarget[]; invalid_entries: number }>("/provider-targets");
}

export function testAIConnection(args: {
  expectedConfigurationVersion: number;
  expectedTargetFingerprint: string;
  key: string;
}) {
  return rootRequest<{ probe: ProbeView; ai_configuration: AIConfigDetail }>(
    "/ai-configuration/test",
    {
      method: "POST",
      key: args.key,
      body: {
        expected_configuration_version: args.expectedConfigurationVersion,
        expected_target_fingerprint: args.expectedTargetFingerprint,
      },
    },
  );
}

export function listAITests() {
  return rootRequest<{ items: ProbeView[]; total: number }>("/ai-configuration/tests?limit=10");
}

export function cancelAITest(probeId: string) {
  return rootRequest<ProbeView>(`/ai-configuration/tests/${probeId}/cancel`, { method: "POST" });
}

export interface InventoryPreview {
  plan_id: string;
  plan_digest: string;
  status: string;
  partition_count: number;
  fixture_mode?: boolean;
}

export interface ProposalSummary {
  id: string;
  entity_type: string;
  generated: Record<string, unknown>;
  match_state?: string;
  suggested_action?: string;
}

export function previewInventory(
  sessionId: string,
  body: {
    expected_row_version: number;
    requested_input_revision: number;
    requested_clarification_revision: number;
    extraction_run_id: string;
  },
) {
  return request<InventoryPreview>(`/${sessionId}/inventory/preview`, {
    method: "POST",
    body,
  });
}

export function analyzeInventory(
  sessionId: string,
  args: {
    expected_row_version: number;
    requested_input_revision: number;
    requested_clarification_revision: number;
    plan_id: string;
    expected_plan_digest: string;
    key: string;
  },
) {
  return request<RunMutationResult>(`/${sessionId}/inventory/analyze`, {
    method: "POST",
    key: args.key,
    body: {
      expected_row_version: args.expected_row_version,
      requested_input_revision: args.requested_input_revision,
      requested_clarification_revision: args.requested_clarification_revision,
      plan_id: args.plan_id,
      expected_plan_digest: args.expected_plan_digest,
    },
  });
}

export function listProposalSets(sessionId: string) {
  return request<{ items: { id: string; edit_version: number }[]; total: number }>(
    `/${sessionId}/proposal-sets`,
  );
}

export function listInventoryProposals(sessionId: string, setId: string) {
  return request<{ items: ProposalSummary[] }>(`/${sessionId}/proposal-sets/${setId}/proposals`);
}

export interface GovernancePreview {
  plan_id: string;
  plan_digest: string;
  status: string;
  partition_count: number;
  inventory_proposal_set_id: string;
  fixture_mode?: boolean;
}

export interface GovernanceProposalSummary {
  id: string;
  proposal_family: string;
  generated: Record<string, unknown>;
  match_state?: string;
  runtime_verification?: string;
  capability?: Record<string, unknown>;
}

export function previewGovernance(
  sessionId: string,
  body: {
    expected_row_version: number;
    requested_input_revision: number;
    requested_inventory_clarification_revision: number;
    requested_governance_clarification_revision: number;
    inventory_proposal_set_id: string;
    include_suggested_controls?: boolean;
  },
) {
  return request<GovernancePreview>(`/${sessionId}/governance/preview`, {
    method: "POST",
    body,
  });
}

export function generateGovernance(
  sessionId: string,
  args: {
    expected_row_version: number;
    requested_input_revision: number;
    requested_inventory_clarification_revision: number;
    requested_governance_clarification_revision: number;
    plan_id: string;
    expected_plan_digest: string;
    key: string;
  },
) {
  return request<RunMutationResult>(`/${sessionId}/governance/generate`, {
    method: "POST",
    key: args.key,
    body: {
      expected_row_version: args.expected_row_version,
      requested_input_revision: args.requested_input_revision,
      requested_inventory_clarification_revision: args.requested_inventory_clarification_revision,
      requested_governance_clarification_revision: args.requested_governance_clarification_revision,
      plan_id: args.plan_id,
      expected_plan_digest: args.expected_plan_digest,
    },
  });
}

export function listGovernanceSets(sessionId: string) {
  return request<{ items: { id: string; edit_version: number }[]; total: number }>(
    `/${sessionId}/governance-sets`,
  );
}

export function listGovernanceProposals(sessionId: string, setId: string) {
  return request<{ items: GovernanceProposalSummary[] }>(
    `/${sessionId}/governance-sets/${setId}/proposals`,
  );
}

export interface ChangeOperationSummary {
  id: string;
  ordinal: number;
  verb: string;
  target_kind: string;
  source_domain?: string;
  source_proposal_id?: string;
  action?: Record<string, unknown>;
  dependency_ordinals?: number[];
  status?: string;
}

export interface ChangeSetSummary {
  id: string;
  session_id: string;
  status: string;
  contract_version: string;
  payload_digest: string;
  review_digest?: string | null;
  inventory_proposal_set_id?: string | null;
  governance_proposal_set_id?: string | null;
  operations: ChangeOperationSummary[];
}

export interface ApplicationSummary {
  application_id: string;
  change_set_id: string;
  status: string;
  run_id?: string;
}

export function listChangeSets(sessionId: string) {
  return request<{ items: ChangeSetSummary[]; total: number }>(
    `/${sessionId}/change-sets`,
  );
}

export function getChangeSet(sessionId: string, changeSetId: string) {
  return request<ChangeSetSummary>(`/${sessionId}/change-sets/${changeSetId}`);
}

export function prepareChangeSet(
  sessionId: string,
  args: {
    expected_row_version: number;
    requested_input_revision: number;
    selection: {
      inventory: Record<string, "ACCEPT" | "SKIP">;
      governance: Record<string, "ACCEPT" | "SKIP">;
      inventory_proposal_set_id?: string;
      governance_proposal_set_id?: string;
    };
    key: string;
  },
) {
  return request<ChangeSetSummary>(`/${sessionId}/change-sets/prepare`, {
    method: "POST",
    key: args.key,
    body: {
      expected_row_version: args.expected_row_version,
      requested_input_revision: args.requested_input_revision,
      selection: {
        inventory: args.selection.inventory,
        governance: args.selection.governance,
        inventory_proposal_set_id: args.selection.inventory_proposal_set_id,
        governance_proposal_set_id: args.selection.governance_proposal_set_id,
      },
    },
  });
}

export function applyChangeSet(
  sessionId: string,
  changeSetId: string,
  args: {
    expected_row_version: number;
    requested_input_revision: number;
    expected_payload_digest: string;
    key: string;
  },
) {
  return request<ApplicationSummary>(`/${sessionId}/change-sets/${changeSetId}/apply`, {
    method: "POST",
    key: args.key,
    body: {
      expected_row_version: args.expected_row_version,
      requested_input_revision: args.requested_input_revision,
      change_set_id: changeSetId,
      expected_payload_digest: args.expected_payload_digest,
    },
  });
}

export interface RuntimeContextView {
  session_id: string;
  session_row_version: number;
  eligibility: "READY" | "BLOCKED" | "NOT_APPLIED";
  reasons: string[];
  application?: { id: string; change_set_id: string; status: string };
  selectable: {
    ai_systems: { id: string; binding_key: string }[];
    agents: { id: string; binding_key: string }[];
    content_rules: { id: string; binding_key: string }[];
    policy_sources: { id: string; binding_key: string }[];
  };
  context?: {
    id: string;
    row_version: number;
    application_id: string | null;
    delegation_mode: string;
    selection_digest: string;
    selected: Record<string, unknown>;
  } | null;
  readiness?: Record<string, unknown> | null;
}

export interface ActivationPlanSummary {
  id: string;
  status: string;
  payload_digest: string;
}

export function getRuntimeContext(sessionId: string) {
  return request<RuntimeContextView>(`/${sessionId}/runtime-context`);
}

export function saveRuntimeContext(
  sessionId: string,
  args: {
    expected_row_version: number;
    delegation_mode: "NONE" | "EXPLICIT" | "TENANT";
    selected: Record<string, unknown>;
  },
) {
  return request<RuntimeContextView>(`/${sessionId}/runtime-context`, {
    method: "PATCH",
    body: args,
  });
}

export function prepareActivationPlan(
  sessionId: string,
  args: { expected_row_version: number; expected_selection_digest: string },
) {
  return request<ActivationPlanSummary>(`/${sessionId}/activation-plans`, {
    method: "POST",
    body: args,
  });
}

export function prepareVerificationPlan(
  sessionId: string,
  args: {
    activation_plan_id: string;
    expected_row_version: number;
    scenarios?: Record<string, unknown>[];
  },
) {
  return request<{ id: string; payload_digest: string; status: string }>(
    `/${sessionId}/verification-plans`,
    { method: "POST", body: args },
  );
}
