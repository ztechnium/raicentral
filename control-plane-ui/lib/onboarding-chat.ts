import {
  IntakeApiError,
  analyzeInventory,
  applyChangeSet,
  extractSources,
  fetchRevisionText,
  generateGovernance,
  getCapabilities,
  getRun,
  getSession,
  listGovernanceProposals,
  listGovernanceSets,
  listInventoryProposals,
  listProposalSets,
  newIdempotencyKey,
  prepareChangeSet,
  prepareSources,
  previewGovernance,
  previewInventory,
  saveTextSource,
  setSourceIncluded,
  uploadFileSource,
  type ChangeSetSummary,
  type GovernanceProposalSummary,
  type ProposalSummary,
  type RunSummary,
  type SessionDetail,
} from "@/lib/onboarding-api";
import { runStatus } from "@/lib/onboarding-intake";

/**
 * Chat-driven onboarding. Every step here is an existing server endpoint; the chat only
 * sequences them (save inputs → prepare → extract → inventory → governance → review) so the
 * user never has to click through the stages. Applying changes is a separate, explicit step.
 */

export type StepId = "save" | "prepare" | "extract" | "inventory" | "governance" | "review";

export const STEP_LABELS: Record<StepId, string> = {
  save: "Saving your message and files",
  prepare: "Checking saved sources",
  extract: "Reading text and tables",
  inventory: "Finding AI systems, agents, models and tools",
  governance: "Extracting content, tool and budget rules",
  review: "Preparing the change review",
};

export type StepStatus = "pending" | "running" | "done" | "failed";
export type StepState = Record<StepId, StepStatus>;

export function initialSteps(): StepState {
  return {
    save: "pending",
    prepare: "pending",
    extract: "pending",
    inventory: "pending",
    governance: "pending",
    review: "pending",
  };
}

export class ChatPipelineError extends Error {
  constructor(
    message: string,
    readonly step: StepId,
  ) {
    super(message);
  }
}

// ----------------------------------------------------------------------- results

export interface SystemFinding {
  key: string;
  name: string;
  description?: string;
  owner?: string;
  status?: string;
  match: string;
  agents: { key: string; name: string; description?: string; match: string }[];
}

export interface ModelFinding {
  name: string;
  provider?: string;
  match: string;
}

export interface ResourceFinding {
  kind: "tool" | "mcp_server" | "external_service" | "data_source";
  key: string;
  name: string;
  match: string;
}

export interface RuleFinding {
  id: string;
  family: "content_rule" | "tool_rule" | "runtime_budget";
  title: string;
  system?: string;
  effect?: string;
  tool?: string;
  budget?: string;
  match: string;
}

export interface OnboardingResults {
  systems: SystemFinding[];
  unattachedAgents: SystemFinding["agents"];
  models: ModelFinding[];
  resources: ResourceFinding[];
  rules: RuleFinding[];
  otherGovernance: number;
}

type Rec = Record<string, unknown>;
const str = (v: unknown): string | undefined =>
  typeof v === "string" && v.trim() ? v.trim() : undefined;

export function buildResults(
  inventory: ProposalSummary[],
  governance: GovernanceProposalSummary[],
): OnboardingResults {
  const systems = new Map<string, SystemFinding>();
  const agents: { parent?: string; agent: SystemFinding["agents"][number] }[] = [];
  const models: ModelFinding[] = [];
  const resources: ResourceFinding[] = [];

  for (const p of inventory) {
    const gen = p.generated as Rec;
    const claims = (gen.claims ?? {}) as Rec;
    const rel = (gen.relationships ?? {}) as Rec;
    const match = p.match_state ?? "Proposed";
    const keyOf = (field: string) => str(claims[field]) ?? str(claims.key) ?? str(claims.name) ?? "";
    switch (p.entity_type) {
      case "ai_system": {
        const key = keyOf("system_key");
        systems.set(key, {
          key,
          name: str(claims.name) ?? key,
          description: str(claims.description) ?? str(claims.business_purpose),
          owner: str(claims.owner_name),
          status: str(claims.status),
          match,
          agents: [],
        });
        break;
      }
      case "agent": {
        const key = keyOf("agent_key");
        agents.push({
          parent: str((rel.ai_system as Rec | undefined)?.claimed_key),
          agent: {
            key,
            name: str(claims.name) ?? key,
            description: str(claims.description) ?? str(claims.business_purpose),
            match,
          },
        });
        break;
      }
      case "model":
        models.push({
          name: keyOf("model_name"),
          provider: str(claims.provider),
          match,
        });
        break;
      case "tool":
      case "mcp_server":
      case "external_service":
      case "data_source": {
        const field = {
          tool: "tool_key",
          mcp_server: "server_key",
          external_service: "service_key",
          data_source: "data_source_key",
        }[p.entity_type];
        const key = keyOf(field);
        resources.push({ kind: p.entity_type, key, name: str(claims.name) ?? key, match });
        break;
      }
    }
  }

  const unattachedAgents: SystemFinding["agents"] = [];
  for (const { parent, agent } of agents) {
    const owner = parent ? systems.get(parent) : undefined;
    if (owner) owner.agents.push(agent);
    else unattachedAgents.push(agent);
  }

  const rules: RuleFinding[] = [];
  let otherGovernance = 0;
  for (const p of governance) {
    const gen = p.generated as Rec;
    const family = p.proposal_family;
    if (family !== "content_rule" && family !== "tool_rule" && family !== "runtime_budget") {
      otherGovernance += 1;
      continue;
    }
    const metric = str(gen.metric);
    const limit = gen.limit_value;
    const window = typeof gen.window_seconds === "number" ? gen.window_seconds : undefined;
    rules.push({
      id: p.id,
      family,
      title: str(gen.title) ?? str(gen.text) ?? family,
      system: str(gen.system_key),
      effect: str(gen.effect),
      tool: str(gen.tool_selector),
      budget:
        metric && typeof limit === "number"
          ? `${limit} ${metric.toLowerCase().replace(/_/g, " ")}${window ? ` per ${formatWindow(window)}` : ""}`
          : undefined,
      match: p.match_state ?? "Proposed",
    });
  }

  return {
    systems: [...systems.values()],
    unattachedAgents,
    models,
    resources,
    rules,
    otherGovernance,
  };
}

function formatWindow(seconds: number): string {
  if (seconds % 86400 === 0) return seconds === 86400 ? "day" : `${seconds / 86400} days`;
  if (seconds % 3600 === 0) return seconds === 3600 ? "hour" : `${seconds / 3600} hours`;
  if (seconds % 60 === 0) return seconds === 60 ? "minute" : `${seconds / 60} minutes`;
  return `${seconds}s`;
}

// ---------------------------------------------------------------------- intents

const APPLY_RE =
  /^\s*(yes|y|ok|okay|apply|apply it|apply changes|apply all|confirm|confirmed|approve|go ahead|do it|looks good|lgtm)[\s.!]*$/i;

/** A bare confirmation applies the pending review; anything else is new instructions. */
export function isApplyConfirmation(text: string): boolean {
  return APPLY_RE.test(text);
}

const APPLY_VERBS =
  "apply|approve|approved|create|register|add|confirm|accept|save|submit|proceed|go ahead|do|run|implement|activate";
// Words that may follow the verb without adding a new instruction: "apply all these rules now".
const APPLY_FILLER =
  "all|this|these|those|that|them|it|the|rules?|polic(?:y|ies)|budgets?|changes?|proposals?|proposed|reviewed|review|above|everything|now|please|for me|and|then|to|my|system|registry|onboarding";
const APPLY_INTENT_RE = new RegExp(
  String.raw`^\s*(?:(?:yes|ok|okay|sure|please)[\s,.!]*)?(?:(?:can|could|would|will)(?:\s+(?:you|we|i))?\s+)?(?:please\s+)?(?:let'?s\s+)?(?:${APPLY_VERBS})(?:\s+(?:${APPLY_FILLER}|${APPLY_VERBS}))*[\s?.!]*$`,
  "i",
);

/**
 * While a reviewed change is waiting, "can apply all this rules?" or "approve and create the
 * policies" mean apply it. Anything that names something specific is still new instructions.
 */
export function isApplyIntent(text: string): boolean {
  return isApplyConfirmation(text) || APPLY_INTENT_RE.test(text);
}

const REANALYZE_RE = /\b(re-?analy[sz]e|analy[sz]e (?:it |them |the files )?again|re-?run|redo|start over)\b/i;

/** An explicit request to run the document analysis again. */
export function wantsReanalysis(text: string): boolean {
  return REANALYZE_RE.test(text);
}

// --------------------------------------------------------------------- pipeline

const POLL_MS = 1200;
const RUN_TIMEOUT_MS = 10 * 60 * 1000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForRun(sessionId: string, runId: string, step: StepId): Promise<RunSummary> {
  const started = Date.now();
  let current = await getRun(sessionId, runId);
  while (current.active) {
    if (Date.now() - started > RUN_TIMEOUT_MS) {
      throw new ChatPipelineError("This is taking longer than expected. Send a message to retry.", step);
    }
    await sleep(POLL_MS);
    current = await getRun(sessionId, runId);
  }
  if (current.state !== "SUCCEEDED") {
    const status = runStatus(current);
    throw new ChatPipelineError(status?.detail ?? "A step did not complete.", step);
  }
  return current;
}

export interface SendInput {
  sessionId: string;
  text: string;
  files: File[];
  onStep: (step: StepId, status: StepStatus) => void;
}

export interface AnalysisOutcome {
  session: SessionDetail;
  results: OnboardingResults;
  changeSet: ChangeSetSummary | null;
  /** Sources that could not be read, with the reason; analysis still ran on the rest. */
  attention: { label: string; detail: string }[];
}

/** Save the message and files, then run every analysis stage. Throws ChatPipelineError. */
export async function analyzeMessage(input: SendInput): Promise<AnalysisOutcome> {
  const { sessionId, onStep } = input;
  const track = async <T>(step: StepId, fn: () => Promise<T>): Promise<T> => {
    onStep(step, "running");
    try {
      const value = await fn();
      onStep(step, "done");
      return value;
    } catch (error) {
      onStep(step, "failed");
      if (error instanceof ChatPipelineError) throw error;
      if (error instanceof IntakeApiError && /source limit/i.test(error.message)) {
        throw new ChatPipelineError(
          "This conversation has reached its limit of 20 files. Start a new conversation for more.",
          step,
        );
      }
      throw new ChatPipelineError(
        error instanceof Error ? error.message : "Something went wrong.",
        step,
      );
    }
  };

  let session = await track("save", async () => {
    let current = await getSession(sessionId);
    const text = input.text.trim();
    if (text) {
      const existing = current.sources.find((s) => s.role === "USER_PROMPT" && s.included);
      let body = text;
      if (existing?.current_revision) {
        const previous = await fetchRevisionText(sessionId, existing.id, existing.current_revision.id);
        body = `${previous.trimEnd()}\n\n---\n\n${text}`;
      }
      const saved = await saveTextSource(sessionId, {
        role: "USER_PROMPT",
        text: body,
        title: "Onboarding conversation",
        sourceId: existing?.id,
        expectedRowVersion: current.row_version,
        key: newIdempotencyKey(),
      });
      current = saved.session;
    }
    for (const file of input.files) {
      // Same file name = a newer version of that source, not another source. This keeps
      // re-uploads from exhausting the per-session source limit.
      const existing = current.sources.find(
        (s) => s.kind === "FILE" && (s.current_revision?.display_name ?? s.label) === file.name,
      );
      const uploaded = await uploadFileSource(sessionId, {
        file,
        sourceId: existing?.id,
        expectedRowVersion: current.row_version,
        key: newIdempotencyKey(),
      });
      current = uploaded.session;
      if (existing && !existing.included) {
        current = await setSourceIncluded(sessionId, existing.id, true, current.row_version);
      }
    }
    if (current.sources.filter((s) => s.included).length === 0) {
      throw new ChatPipelineError("Tell me about your AI systems, or attach a file to start.", "save");
    }
    return current;
  });

  session = await track("prepare", async () => {
    const current = await getSession(sessionId);
    const result = await prepareSources(sessionId, {
      expectedRowVersion: current.row_version,
      inputRevision: current.input_revision,
      key: newIdempotencyKey(),
    });
    await waitForRun(sessionId, result.run.id, "prepare");
    return getSession(sessionId);
  });

  session = await track("extract", async () => {
    const [current, caps] = await Promise.all([getSession(sessionId), getCapabilities()]);
    const prepRunId = current.preparation?.run_id;
    if (!prepRunId) throw new ChatPipelineError("Sources were not prepared.", "extract");
    const result = await extractSources(sessionId, {
      expectedRowVersion: current.row_version,
      inputRevision: current.input_revision,
      preparationRunId: prepRunId,
      parserFingerprint: caps.parser_profile.fingerprint,
      key: newIdempotencyKey(),
    });
    await waitForRun(sessionId, result.run.id, "extract");
    const done = await getSession(sessionId);
    if (done.extraction?.state !== "EXTRACTED") {
      throw new ChatPipelineError("I could not read your files.", "extract");
    }
    return done;
  });

  const extractionRunId = session.extraction?.run_id;
  if (!extractionRunId) throw new ChatPipelineError("No extraction was recorded.", "extract");
  const attention = (session.extraction?.sources ?? [])
    .filter((s) => s.outcome && s.outcome !== "COMPLETE")
    .map((s) => ({ label: s.label, detail: s.reason ?? s.outcome ?? "needs attention" }));
  if (!session.extraction?.coverage_acceptable) {
    const names = attention.map((a) => a.label).join(", ");
    throw new ChatPipelineError(
      `I could not read ${names || "some files"} well enough to analyze them. Send a text or searchable copy and I will continue.`,
      "extract",
    );
  }

  const inventorySetId = await track("inventory", async () => {
    const current = await getSession(sessionId);
    const preview = await previewInventory(sessionId, {
      expected_row_version: current.row_version,
      requested_input_revision: current.input_revision,
      requested_clarification_revision: 0,
      extraction_run_id: extractionRunId,
    });
    const result = await analyzeInventory(sessionId, {
      expected_row_version: current.row_version,
      requested_input_revision: current.input_revision,
      requested_clarification_revision: 0,
      plan_id: preview.plan_id,
      expected_plan_digest: preview.plan_digest,
      key: newIdempotencyKey(),
    });
    await waitForRun(sessionId, result.run.id, "inventory");
    const sets = await listProposalSets(sessionId);
    if (!sets.items.length) throw new ChatPipelineError("No AI systems were found.", "inventory");
    return sets.items[0].id;
  });

  const governanceSetId = await track("governance", async () => {
    const current = await getSession(sessionId);
    const preview = await previewGovernance(sessionId, {
      expected_row_version: current.row_version,
      requested_input_revision: current.input_revision,
      requested_inventory_clarification_revision: 0,
      requested_governance_clarification_revision: 0,
      inventory_proposal_set_id: inventorySetId,
    });
    const result = await generateGovernance(sessionId, {
      expected_row_version: current.row_version,
      requested_input_revision: current.input_revision,
      requested_inventory_clarification_revision: 0,
      requested_governance_clarification_revision: 0,
      plan_id: preview.plan_id,
      expected_plan_digest: preview.plan_digest,
      key: newIdempotencyKey(),
    });
    await waitForRun(sessionId, result.run.id, "governance");
    const sets = await listGovernanceSets(sessionId);
    return sets.items[0]?.id;
  });

  const [inventory, governance] = await Promise.all([
    listInventoryProposals(sessionId, inventorySetId).then((b) => b.items),
    governanceSetId
      ? listGovernanceProposals(sessionId, governanceSetId).then((b) => b.items)
      : Promise.resolve([] as GovernanceProposalSummary[]),
  ]);

  const changeSet = await track("review", async () => {
    const current = await getSession(sessionId);
    const selection = {
      inventory: Object.fromEntries(inventory.map((p) => [p.id, "ACCEPT" as const])),
      governance: Object.fromEntries(governance.map((p) => [p.id, "ACCEPT" as const])),
      inventory_proposal_set_id: inventorySetId,
      governance_proposal_set_id: governanceSetId,
    };
    try {
      return await prepareChangeSet(sessionId, {
        expected_row_version: current.row_version,
        requested_input_revision: current.input_revision,
        selection,
        key: newIdempotencyKey(),
      });
    } catch (error) {
      // Nothing registrable (for example only recorded-only governance): still show findings.
      if (error instanceof Error && /NO_OPERATIONS|no apply operations/i.test(error.message)) {
        return null;
      }
      throw error;
    }
  });

  return {
    session: await getSession(sessionId),
    results: buildResults(inventory, governance),
    changeSet,
    attention,
  };
}

/** Apply a prepared review. Returns when the server finished (or the run is still queued). */
export async function applyReview(
  sessionId: string,
  changeSet: ChangeSetSummary,
): Promise<{ status: string }> {
  const current = await getSession(sessionId);
  const result = await applyChangeSet(sessionId, changeSet.id, {
    expected_row_version: current.row_version,
    requested_input_revision: current.input_revision,
    expected_payload_digest: changeSet.payload_digest,
    key: newIdempotencyKey(),
  });
  if (result.run_id) {
    const final = await waitForRun(sessionId, result.run_id, "review");
    return { status: final.state };
  }
  return { status: result.status };
}
