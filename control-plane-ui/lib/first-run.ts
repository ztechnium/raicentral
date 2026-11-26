/**
 * First-run setup for a new organization: API calls and the wizard draft.
 *
 * The draft lives in this browser until the user creates it. It never holds
 * secrets; credentials exist only in memory after creation.
 */

import { apiGet, apiPost } from "@/lib/api";
import type { RuleDraft } from "@/lib/first-run-policies";

const BASE = "/v1/productization/first-run";
const DRAFT_KEY = "rai_first_run_draft_v1";

export type FirstRunState = "REQUIRED" | "SKIPPED" | "COMPLETED" | "NOT_NEEDED";
export type Provider = "google_ai" | "anthropic" | "openai";
export type CustomKind = "mcp_server" | "tool" | "external_service" | "data_source";

export interface FirstRunStatus {
  state: FirstRunState;
  is_new_organization: boolean;
  can_setup: boolean;
  missing_permissions: string[];
  should_redirect: boolean;
  email_verified: boolean;
  can_add_policies: boolean;
  /** False when no model is configured to judge content rules. */
  content_rules_enforced: boolean;
  ai: {
    configured: boolean;
    provider: string | null;
    model: string | null;
    problem: string | null;
  };
}

export interface CatalogModel {
  provider: Provider;
  model_name: string;
  label: string;
}

export interface CatalogResource {
  key: string;
  name: string;
  description?: string | null;
  category?: string | null;
  tools: { key: string; name: string; risk: string }[];
}

export interface FirstRunCatalog {
  providers: { value: Provider; label: string }[];
  models: CatalogModel[];
  resources: CatalogResource[];
}

export interface ModelRef {
  provider: Provider;
  model_name: string;
}

export interface SystemDraft {
  id: string;
  name: string;
  providers: Provider[];
  models: ModelRef[];
}

interface ResourceBase {
  id: string;
  /** Stored as exclusions so systems added later use the resource by default. */
  excludedSystems: string[];
}

export interface SeedResourceDraft extends ResourceBase {
  type: "seed";
  key: string;
  excludedTools: string[];
}

export interface CustomResourceDraft extends ResourceBase {
  type: "custom";
  kind: CustomKind;
  name: string;
  description: string;
  endpoint: string;
  /** Comma or line separated tool names (MCP server only). */
  tools: string;
}

export type ResourceDraft = SeedResourceDraft | CustomResourceDraft;

export interface Draft {
  requestId: string;
  systems: SystemDraft[];
  resources: ResourceDraft[];
  rules: RuleDraft[];
}

export interface ResourcesRequest {
  models: ModelRef[];
  seed_servers: { key: string; tool_keys: string[] | null }[];
  custom: {
    ref: string;
    kind: CustomKind;
    name: string;
    description: string | null;
    endpoint: string | null;
    tools: string[];
  }[];
}

export interface ResourcesResult {
  models: (ModelRef & { id: string })[];
  resources: {
    ref: string;
    items: { resource_type: CustomKind; id: string; key: string; name: string }[];
  }[];
}

export interface SystemsRequest {
  request_id: string;
  systems: {
    name: string;
    model_ids: string[];
    dependencies: { resource_type: CustomKind; resource_id: string }[];
  }[];
}

export interface CreatedSystem {
  ai_system_id: string;
  name: string;
  system_key: string;
  agent: { id: string; name: string };
  version: { id: string; label: string };
  runtime_identity: { id: string; identity_key: string; name: string };
  integration_id: string;
  credential: { id: string; credential_key: string; secret: string };
  models: ModelRef[];
}

export interface SystemsResult {
  environment: string;
  systems: CreatedSystem[];
}

export const getFirstRunStatus = () => apiGet<FirstRunStatus>(BASE);
export const getFirstRunCatalog = () => apiGet<FirstRunCatalog>(`${BASE}/catalog`);
export const skipFirstRun = () => apiPost<FirstRunStatus>(`${BASE}/skip`, {});
export const applyFirstRunResources = (body: ResourcesRequest) =>
  apiPost<ResourcesResult>(`${BASE}/apply/resources`, body);
export const applyFirstRunSystems = (body: SystemsRequest) =>
  apiPost<SystemsResult>(`${BASE}/apply/systems`, body);

// ------------------------------------------------------------------------ names

export const cleanName = (value: string) => value.replace(/\s+/g, " ").trim();
export const agentName = (systemName: string) => `${cleanName(systemName)} - agent 01`;
export const versionName = (systemName: string) => `${agentName(systemName)} - V1.0.0`;

export const MODEL_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

export const modelKey = (model: ModelRef) => `${model.provider}/${model.model_name.toLowerCase()}`;

/** UUID v4. `crypto.randomUUID` is missing on plain-HTTP, non-localhost origins. */
export function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

// ------------------------------------------------------------------------ draft

export const newSystem = (): SystemDraft => ({
  id: newId(),
  name: "",
  providers: [],
  models: [],
});

export const newDraft = (): Draft => ({
  requestId: newId(),
  systems: [newSystem()],
  resources: [],
  rules: [],
});

export const splitTools = (value: string) => value.split(/[,\n]/).map(cleanName).filter(Boolean);

/** Problems that block leaving step 1, or null. */
export function systemsProblem(systems: SystemDraft[]): string | null {
  if (!systems.length) return "Add at least one AI system.";
  const names = new Set<string>();
  for (const [index, system] of systems.entries()) {
    const name = cleanName(system.name);
    if (!name) return `AI system ${index + 1} needs a name.`;
    if (names.has(name.toLowerCase())) return `Two AI systems are named "${name}".`;
    names.add(name.toLowerCase());
    if (!system.models.length) return `Select at least one model for "${name}".`;
  }
  return null;
}

/** Problems that block leaving step 2, or null. Resources are optional. */
export function resourcesProblem(resources: ResourceDraft[]): string | null {
  const names = new Set<string>();
  for (const resource of resources) {
    if (resource.type !== "custom") continue;
    const name = cleanName(resource.name);
    if (!name) return "Every resource you add needs a name, or remove it.";
    const key = `${resource.kind}/${name.toLowerCase()}`;
    if (names.has(key)) return `The resource "${name}" is listed twice.`;
    names.add(key);
    if (resource.kind !== "mcp_server") continue;
    const tools = splitTools(resource.tools).map((t) => t.toLowerCase());
    if (new Set(tools).size !== tools.length) return `"${name}" lists the same tool twice.`;
  }
  return null;
}

export const resourceRef = (resource: ResourceDraft) =>
  resource.type === "seed" ? `seed:${resource.key}` : resource.id;

export function buildResourcesRequest(draft: Draft, catalog: FirstRunCatalog): ResourcesRequest {
  const models = new Map<string, ModelRef>();
  for (const system of draft.systems) {
    for (const model of system.models) models.set(modelKey(model), model);
  }
  const request: ResourcesRequest = {
    models: [...models.values()],
    seed_servers: [],
    custom: [],
  };
  for (const resource of draft.resources) {
    if (resource.type === "seed") {
      const tools = catalog.resources.find((r) => r.key === resource.key)?.tools ?? [];
      request.seed_servers.push({
        key: resource.key,
        tool_keys: resource.excludedTools.length
          ? tools.map((t) => t.key).filter((k) => !resource.excludedTools.includes(k))
          : null,
      });
    } else {
      request.custom.push({
        ref: resource.id,
        kind: resource.kind,
        name: cleanName(resource.name),
        description: resource.description.trim() || null,
        endpoint: resource.endpoint.trim() || null,
        tools: resource.kind === "mcp_server" ? splitTools(resource.tools) : [],
      });
    }
  }
  return request;
}

export function buildSystemsRequest(draft: Draft, result: ResourcesResult): SystemsRequest {
  const modelIds = new Map(result.models.map((m) => [modelKey(m), m.id]));
  const items = new Map(result.resources.map((r) => [r.ref, r.items]));
  return {
    request_id: draft.requestId,
    systems: draft.systems.map((system) => ({
      name: cleanName(system.name),
      model_ids: system.models.map((model) => {
        const id = modelIds.get(modelKey(model));
        if (!id) throw new Error(`Model ${model.model_name} was not registered.`);
        return id;
      }),
      dependencies: draft.resources
        .filter((resource) => !resource.excludedSystems.includes(system.id))
        .flatMap((resource) => items.get(resourceRef(resource)) ?? [])
        .map((item) => ({ resource_type: item.resource_type, resource_id: item.id })),
    })),
  };
}

export function loadDraft(): Draft {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Draft;
      if (
        typeof parsed.requestId === "string" &&
        Array.isArray(parsed.systems) &&
        parsed.systems.length > 0 &&
        parsed.systems.every((s) => Array.isArray(s.models) && Array.isArray(s.providers)) &&
        Array.isArray(parsed.resources)
      ) {
        // Drafts saved before the policy step have no rules.
        return { ...parsed, rules: Array.isArray(parsed.rules) ? parsed.rules : [] };
      }
    }
  } catch {
    // unreadable or unavailable storage: start fresh
  }
  return newDraft();
}

export function saveDraft(draft: Draft) {
  try {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // storage is a convenience only
  }
}

export function clearDraft() {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // storage is a convenience only
  }
}
