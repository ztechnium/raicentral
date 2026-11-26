/**
 * The policy step of first-run setup: rule drafts, AI extraction and creation.
 *
 * Rules are written against the draft AI systems and tools, which do not exist yet.
 * Their real ids are filled in once the earlier creation stages have run.
 */

import { apiPost, apiUpload } from "@/lib/api";
import {
  cleanName,
  newId,
  resourceRef,
  splitTools,
  type Draft,
  type FirstRunCatalog,
  type ResourcesResult,
  type SystemsResult,
} from "@/lib/first-run";

const BASE = "/v1/productization/first-run";

export type RuleFamily = "content" | "tool" | "budget";
export type RuleEffect = "DENY" | "REQUIRE_APPROVAL" | "MONITOR";
export type ApproverRole = "business_approver" | "rai_officer" | "admin";
export type AppliesTo = "INPUT" | "OUTPUT" | "BOTH";
export type BudgetMetric = "ACTION_COUNT" | "TOKEN_USAGE" | "COST";
export type ConditionValueType = "string" | "number" | "boolean";

export interface ConditionDraft {
  id: string;
  parameterPath: string;
  operator: string;
  valueType: ConditionValueType;
  value: string;
}

interface RuleBase {
  id: string;
  title: string;
  /** Draft AI system ids. */
  systemIds: string[];
  enabled: boolean;
  /** Things the AI could not settle, for the person to check. */
  warnings: string[];
  documentRef: string | null;
  quote: string | null;
}

export interface ContentRuleDraft extends RuleBase {
  family: "content";
  content: string;
  effect: RuleEffect;
  appliesTo: AppliesTo;
  approverRole: ApproverRole;
}

export interface ToolRuleDraft extends RuleBase {
  family: "tool";
  toolRef: string | null;
  effect: RuleEffect;
  approverRole: ApproverRole;
  conditions: ConditionDraft[];
}

export interface BudgetRuleDraft extends RuleBase {
  family: "budget";
  metric: BudgetMetric;
  /** Kept as typed text until creation. */
  limit: string;
  windowSeconds: number;
  effect: RuleEffect;
}

export type RuleDraft = ContentRuleDraft | ToolRuleDraft | BudgetRuleDraft;

export interface PolicyDocument {
  ref: string;
  title: string;
  text: string;
}

export interface PolicyNote {
  title: string;
  quote: string;
  document: string;
}

interface ExtractedRule {
  family: RuleFamily;
  title: string;
  system_refs: string[];
  enabled: boolean;
  warnings: string[];
  document_ref: string;
  quote: string;
  content?: {
    content: string;
    effect: RuleEffect;
    applies_to: AppliesTo;
    approver_role: ApproverRole | null;
  };
  tool?: {
    tool_ref: string | null;
    effect: RuleEffect;
    approver_role: ApproverRole | null;
  };
  budget?: {
    metric: BudgetMetric;
    limit_value: number;
    window_seconds: number;
    effect: RuleEffect;
  };
}

export interface ExtractionResponse {
  documents: PolicyDocument[];
  rules: ExtractedRule[];
  notes: PolicyNote[];
  warnings: string[];
}

export interface PoliciesRequest {
  documents: PolicyDocument[];
  rules: {
    family: RuleFamily;
    title: string;
    ai_system_ids: string[];
    enabled: boolean;
    document_ref: string | null;
    quote: string | null;
    content?: {
      content: string;
      effect: RuleEffect;
      applies_to: AppliesTo;
      approver_role: ApproverRole | null;
    };
    tool?: {
      tool_id: string;
      effect: RuleEffect;
      approver_role: ApproverRole | null;
      conditions: {
        parameter_path: string;
        operator: string;
        value_type: ConditionValueType;
        value: string | number | boolean | null;
      }[];
    };
    budget?: {
      metric: BudgetMetric;
      limit_value: number;
      window_seconds: number;
      effect: RuleEffect;
    };
  }[];
}

export interface CreatedRule {
  family: RuleFamily;
  title: string;
  rule_id: string;
  ai_system_id: string;
  ai_system_name: string;
  enabled: boolean;
  status: "created" | "existing";
}

export interface PoliciesResult {
  rules: CreatedRule[];
}

export const applyFirstRunPolicies = (body: PoliciesRequest) =>
  apiPost<PoliciesResult>(`${BASE}/apply/policies`, body);

// ------------------------------------------------------------------------ tools

export interface ToolOption {
  ref: string;
  name: string;
  /** The server or resource the tool comes from. */
  group: string;
  /** Draft AI systems that use it. */
  systemIds: string[];
}

/** Every tool the draft will attach, with the systems that will use it. */
export function toolOptions(draft: Draft, catalog: FirstRunCatalog): ToolOption[] {
  return draft.resources.flatMap((resource): ToolOption[] => {
    const systemIds = draft.systems
      .map((s) => s.id)
      .filter((id) => !resource.excludedSystems.includes(id));
    if (resource.type === "seed") {
      const server = catalog.resources.find((r) => r.key === resource.key);
      return (server?.tools ?? [])
        .filter((tool) => !resource.excludedTools.includes(tool.key))
        .map((tool) => ({
          ref: `${resourceRef(resource)}|${tool.key}`,
          name: tool.name,
          group: server?.name ?? resource.key,
          systemIds,
        }));
    }
    const name = cleanName(resource.name);
    if (resource.kind === "tool") {
      return [{ ref: resource.id, name, group: "Your tools", systemIds }];
    }
    if (resource.kind !== "mcp_server") return [];
    return splitTools(resource.tools).map((tool) => ({
      ref: `${resource.id}|${tool.toLowerCase()}`,
      name: tool,
      group: name,
      systemIds,
    }));
  });
}

/** The registered id of a draft tool, once the resource stage has run. */
export function resolveToolId(ref: string, result: ResourcesResult): string | undefined {
  const split = ref.indexOf("|");
  const entryRef = split < 0 ? ref : ref.slice(0, split);
  const key = split < 0 ? null : ref.slice(split + 1);
  const tools = result.resources
    .find((r) => r.ref === entryRef)
    ?.items.filter((item) => item.resource_type === "tool");
  if (key === null) return tools?.[0]?.id;
  return tools?.find((t) =>
    entryRef.startsWith("seed:") ? t.key === key : t.name.toLowerCase() === key,
  )?.id;
}

// ----------------------------------------------------------------------- drafts

const base = (systemIds: string[]) => ({
  id: newId(),
  title: "",
  systemIds,
  enabled: true,
  warnings: [],
  documentRef: null,
  quote: null,
});

export function newRule(family: RuleFamily, systemIds: string[]): RuleDraft {
  if (family === "content") {
    return {
      ...base(systemIds),
      family,
      content: "",
      effect: "DENY",
      appliesTo: "BOTH",
      approverRole: "business_approver",
    };
  }
  if (family === "tool") {
    return {
      ...base([]),
      family,
      toolRef: null,
      effect: "REQUIRE_APPROVAL",
      approverRole: "business_approver",
      conditions: [],
    };
  }
  return {
    ...base(systemIds),
    family,
    metric: "ACTION_COUNT",
    limit: "",
    windowSeconds: 86_400,
    effect: "DENY",
  };
}

export const newCondition = (): ConditionDraft => ({
  id: newId(),
  parameterPath: "",
  operator: "eq",
  valueType: "string",
  value: "",
});

function uniqueTitle(title: string, family: RuleFamily, rules: RuleDraft[]): string {
  const taken = new Set(
    rules.filter((r) => r.family === family).map((r) => cleanName(r.title).toLowerCase()),
  );
  if (!taken.has(title.toLowerCase())) return title;
  let n = 2;
  while (taken.has(`${title} (${n})`.toLowerCase())) n += 1;
  return `${title} (${n})`;
}

/**
 * Append the extracted rules to what is already drafted. A rule quoting the same
 * sentence as an existing one is skipped, so extracting twice does not double them.
 */
export function mergeExtraction(
  rules: RuleDraft[],
  response: ExtractionResponse,
  batch: string,
): { rules: RuleDraft[]; documents: PolicyDocument[]; added: number } {
  const merged = [...rules];
  let added = 0;
  for (const found of response.rules) {
    if (merged.some((r) => r.family === found.family && r.quote === found.quote)) continue;
    const common = {
      id: newId(),
      title: uniqueTitle(cleanName(found.title), found.family, merged),
      systemIds: found.system_refs,
      enabled: found.enabled,
      warnings: found.warnings,
      documentRef: `${batch}-${found.document_ref}`,
      quote: found.quote,
    };
    if (found.content) {
      merged.push({
        ...common,
        family: "content",
        content: found.content.content,
        effect: found.content.effect,
        appliesTo: found.content.applies_to,
        approverRole: found.content.approver_role ?? "business_approver",
      });
    } else if (found.tool) {
      merged.push({
        ...common,
        family: "tool",
        toolRef: found.tool.tool_ref,
        effect: found.tool.effect,
        approverRole: found.tool.approver_role ?? "business_approver",
        conditions: [],
      });
    } else if (found.budget) {
      merged.push({
        ...common,
        family: "budget",
        metric: found.budget.metric,
        limit: String(found.budget.limit_value),
        windowSeconds: found.budget.window_seconds,
        effect: found.budget.effect,
      });
    } else {
      continue;
    }
    added += 1;
  }
  return {
    rules: merged,
    documents: response.documents.map((d) => ({ ...d, ref: `${batch}-${d.ref}` })),
    added,
  };
}

export function extractPolicyRules(
  draft: Draft,
  catalog: FirstRunCatalog,
  files: File[],
  text: string,
) {
  const body = new FormData();
  body.append(
    "context",
    JSON.stringify({
      systems: draft.systems.map((s) => ({ ref: s.id, name: cleanName(s.name) })),
      tools: toolOptions(draft, catalog).map((t) => ({
        ref: t.ref,
        name: t.name,
        system_refs: t.systemIds,
      })),
    }),
  );
  body.append("text", text);
  for (const file of files) body.append("files", file);
  return apiUpload<ExtractionResponse>(`${BASE}/extract-rules`, body);
}

// ------------------------------------------------------------------- validation

const needsValue = (operator: string) => operator !== "exists" && operator !== "not_exists";

/** The draft systems a rule will really apply to. */
export function ruleSystems(rule: RuleDraft, draft: Draft, tools: ToolOption[]): string[] {
  const existing = new Set(draft.systems.map((s) => s.id));
  const allowed =
    rule.family === "tool"
      ? new Set(tools.find((t) => t.ref === rule.toolRef)?.systemIds ?? [])
      : existing;
  return rule.systemIds.filter((id) => existing.has(id) && allowed.has(id));
}

/** The first problem that blocks creation, or null. */
export function policiesProblem(draft: Draft, catalog: FirstRunCatalog): string | null {
  const tools = toolOptions(draft, catalog);
  const titles = new Set<string>();
  for (const [index, rule] of draft.rules.entries()) {
    const title = cleanName(rule.title);
    if (!title) return `Rule ${index + 1} needs a title.`;
    if (rule.family === "tool" && !tools.some((t) => t.ref === rule.toolRef)) {
      return `"${title}": choose a tool, or remove the rule.`;
    }
    const systems = ruleSystems(rule, draft, tools);
    if (!systems.length) return `"${title}": select at least one AI system.`;
    for (const system of systems) {
      const key = `${rule.family}/${title.toLowerCase()}/${system}`;
      if (titles.has(key)) return `Two ${rule.family} rules are named "${title}". Rename one.`;
      titles.add(key);
    }
    if (rule.family === "content" && !rule.content.trim()) {
      return `"${title}": describe what the rule is about.`;
    }
    if (rule.family === "budget" && !(Number(rule.limit) > 0)) {
      return `"${title}": enter a limit above zero.`;
    }
    if (rule.family === "tool") {
      for (const condition of rule.conditions) {
        if (!condition.parameterPath.trim()) {
          return `"${title}": every condition needs a parameter name.`;
        }
        if (!needsValue(condition.operator)) continue;
        if (!condition.value.trim()) return `"${title}": every condition needs a value.`;
        if (condition.valueType === "number" && Number.isNaN(Number(condition.value))) {
          return `"${title}": "${condition.value}" is not a number.`;
        }
      }
    }
  }
  return null;
}

function conditionValue(condition: ConditionDraft): string | number | boolean | null {
  if (!needsValue(condition.operator)) return null;
  if (condition.valueType === "number") return Number(condition.value);
  if (condition.valueType === "boolean") return condition.value === "true";
  return condition.value;
}

export function buildPoliciesRequest(
  draft: Draft,
  catalog: FirstRunCatalog,
  resources: ResourcesResult,
  systems: SystemsResult,
  documents: PolicyDocument[],
): PoliciesRequest {
  const tools = toolOptions(draft, catalog);
  const created = new Map(systems.systems.map((s) => [s.name.toLowerCase(), s.ai_system_id]));
  const systemId = (draftId: string) => {
    const name = cleanName(draft.systems.find((s) => s.id === draftId)?.name ?? "");
    const id = created.get(name.toLowerCase());
    if (!id) throw new Error(`AI system "${name}" was not created.`);
    return id;
  };

  const rules = draft.rules.map((rule): PoliciesRequest["rules"][number] => {
    const common = {
      family: rule.family,
      title: cleanName(rule.title),
      ai_system_ids: ruleSystems(rule, draft, tools).map(systemId),
      enabled: rule.enabled,
      document_ref: documents.some((d) => d.ref === rule.documentRef) ? rule.documentRef : null,
      quote: rule.quote,
    };
    if (rule.family === "content") {
      return {
        ...common,
        content: {
          content: rule.content.trim(),
          effect: rule.effect,
          applies_to: rule.appliesTo,
          approver_role: rule.effect === "REQUIRE_APPROVAL" ? rule.approverRole : null,
        },
      };
    }
    if (rule.family === "budget") {
      return {
        ...common,
        budget: {
          metric: rule.metric,
          limit_value: Number(rule.limit),
          window_seconds: rule.windowSeconds,
          effect: rule.effect,
        },
      };
    }
    const toolId = rule.toolRef ? resolveToolId(rule.toolRef, resources) : undefined;
    if (!toolId) throw new Error(`The tool for "${common.title}" was not registered.`);
    return {
      ...common,
      tool: {
        tool_id: toolId,
        effect: rule.effect,
        approver_role: rule.effect === "REQUIRE_APPROVAL" ? rule.approverRole : null,
        conditions: rule.conditions.map((c) => ({
          parameter_path: c.parameterPath.trim(),
          operator: c.operator,
          value_type: c.valueType,
          value: conditionValue(c),
        })),
      },
    };
  });

  const used = new Set(rules.map((r) => r.document_ref));
  return { rules, documents: documents.filter((d) => used.has(d.ref)) };
}
