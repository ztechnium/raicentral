import type { FilterKey } from "@/lib/finops-filters";

/**
 * Breakdown dimensions the backend accepts (`GET /v1/finops/breakdown?dimension=`).
 * Verified against FinOpsReportingService._dimension_column; keep in sync with it.
 *
 * `filterKey`  - the FinOps filter this row can be narrowed to (undefined: not filterable).
 * `drillParam` - the backend query param that scopes the trajectory drill-down.
 * `searchable` - whether the server supports `search` for this dimension.
 */
export interface DimensionDef {
  id: string;
  label: string;
  singular: string;
  filterKey?: FilterKey;
  drillParam?: string;
  searchable: boolean;
  registryPath?: (key: string) => string;
}

export const DIMENSIONS: readonly DimensionDef[] = [
  {
    id: "ai_system",
    label: "AI system",
    singular: "AI system",
    filterKey: "ai_system_id",
    drillParam: "ai_system_id",
    searchable: false,
    registryPath: (key) => `/governance/registry/${key}`,
  },
  {
    id: "agent",
    label: "Agent",
    singular: "agent",
    filterKey: "agent_id",
    drillParam: "agent_id",
    searchable: false,
  },
  {
    id: "agent_version",
    label: "Agent version",
    singular: "agent version",
    drillParam: "agent_version_id",
    searchable: false,
  },
  {
    id: "runtime_identity",
    label: "Runtime identity",
    singular: "runtime identity",
    drillParam: "runtime_identity_id",
    searchable: false,
  },
  {
    id: "delegation",
    label: "Delegation / tenant",
    singular: "delegation",
    filterKey: "delegation_id",
    drillParam: "delegation_id",
    searchable: false,
  },
  {
    id: "model",
    label: "Model",
    singular: "model",
    filterKey: "model",
    drillParam: "model",
    searchable: true,
  },
  {
    id: "provider",
    label: "Provider",
    singular: "provider",
    filterKey: "provider",
    drillParam: "provider",
    searchable: true,
  },
  {
    id: "environment",
    label: "Environment",
    singular: "environment",
    filterKey: "environment",
    drillParam: "environment",
    searchable: true,
  },
  { id: "tool", label: "Tool / operation", singular: "operation", searchable: true },
  { id: "integration", label: "Integration", singular: "integration", searchable: false },
];

export const DEFAULT_DIMENSION = "ai_system";
export const PAGE_SIZE = 25;

export const SORTS = [
  { id: "cost", label: "Known cost" },
  { id: "calls", label: "Calls" },
  { id: "tokens", label: "Tokens" },
  { id: "trajectories", label: "Trajectories" },
] as const;

export type SortId = (typeof SORTS)[number]["id"];

export function dimensionById(id: string | null | undefined): DimensionDef {
  return DIMENSIONS.find((dim) => dim.id === id) ?? DIMENSIONS[0];
}

export function parseSort(value: string | null | undefined): SortId {
  return (SORTS.find((sort) => sort.id === value)?.id ?? "cost") as SortId;
}

export function parsePage(value: string | null | undefined): number {
  const page = Number.parseInt(value ?? "", 10);
  return Number.isFinite(page) && page >= 1 ? page : 1;
}

/** Plain-language definitions so "calls" is never mistaken for another unit. */
export const UNIT_DEFINITIONS: ReadonlyArray<[string, string]> = [
  ["Call (this table)", "One metering span: a model call, tool call or other metered operation."],
  ["Provider request", "One HTTP request to a model provider. A model call can involve several."],
  ["Model call", "One model invocation recorded as a MODEL_CALL span."],
  ["Tool call", "One tool invocation recorded as a TOOL_CALL span."],
  ["Governed action", "A policy-evaluated action; it may or may not have produced metered usage."],
  ["Trajectory", "One end-to-end agent run containing many calls."],
  ["Conversation", "A group of trajectories that share a conversation."],
];
