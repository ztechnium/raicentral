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
  view: SessionView;
  row_version: number;
  input_revision: number;
  source_count: number;
  total_source_count: number;
  sources: SourceSummary[];
  current_run: RunSummary | null;
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
