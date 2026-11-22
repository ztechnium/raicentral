"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SectionHeader } from "@/components/section-header";
import { CatalogUpload } from "@/components/onboarding/catalog-upload";
import { Panel } from "@/components/registry/panel";
import { StatusBadge } from "@/components/registry/status-badge";
import { FormField } from "@/components/registry/form-field";
import { TechnicalValue } from "@/components/console/page-parts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  createAgent,
  createAgentVersion,
  createAISystem,
  createRuntimeIdentity,
  getAISystems,
  getAgentVersions,
  getAgents,
  getRuntimeCredentials,
  getRuntimeIdentities,
  getTrajectories,
  issueRuntimeCredential,
  transitionAgentVersion,
} from "@/lib/api";
import { BUSINESS_UNITS, RISK_TIERS } from "@/lib/registry-constants";
import {
  completeWelcome,
  fetchOnboarding,
  listSdkIntegrations,
  patchOnboarding,
  type OnboardingPatchBody,
  type OnboardingResponse,
  type SdkIntegrationSummary,
} from "@/lib/public-api";
import {
  canMarkManually,
  completionLabel,
  findMatchingTrajectory,
  isInformationalStep,
  manualActionLabel,
  matchesSelection,
  scopedConnectionDisplay,
  stepperState,
  verificationLabel,
  welcomeLabel,
  type ConnectionDisplay,
} from "@/lib/onboarding-summary";
import { markWelcomeDoneLocally } from "@/lib/welcome-gate";
import { cn } from "@/lib/utils";
import type { AISystemSummary, RuntimeIdentitySummary, TrajectorySummary } from "@/lib/types";

const STEPS = ["System", "Agent", "Version", "Runtime", "Credential", "Connect", "Verify"] as const;

const CHECKLIST_WIZARD: Record<string, number> = {
  first_ai_system: 0,
  agent_version: 1,
  runtime_identity: 3,
  choose_integration: 4,
  connect_application: 5,
  test_connection: 5,
  first_governed_action: 5,
  inspect_trajectory: 6,
};

const CHECKLIST_GUIDE: Record<string, { detail: string; href?: string; hrefLabel?: string }> = {
  organization_profile: {
    detail: "Organization name, industry, and logo are saved from the welcome wizard.",
  },
  team_roles: {
    detail: "Confirm an organization admin and a RAI officer are active.",
    href: "/organization/team",
    hrefLabel: "Open team",
  },
  first_ai_system: {
    detail: "Register the AI system this runtime will govern. New systems start as drafts.",
  },
  agent_version: {
    detail:
      "Add the agent and its first version. The version must be reviewed and approved before a runtime identity can be created.",
  },
  resources: {
    detail:
      "Add models, MCP servers, tools, and services. A spreadsheet can load all of them at once.",
  },
  first_policies: {
    detail:
      "Add at least one rule to the selected AI system. A rule that exists may still be disabled; this item does not say it is enforced.",
    href: "/governance/policies",
    hrefLabel: "Open policies",
  },
  choose_integration: {
    detail: "Create the SDK integration the application will call.",
  },
  runtime_identity: {
    detail: "Create the runtime identity that holds the agent credential.",
  },
  connect_application: {
    detail:
      "Select the integration for this runtime. It shows as runtime activity observed once a heartbeat has been recorded for it. A heartbeat is historical activity, not a live connection.",
  },
  test_connection: {
    detail: "Verification has not been run. It cannot be completed from here yet.",
  },
  first_governed_action: {
    detail:
      "Shows as a governed decision observed when the selected integration has a recorded decision. It does not prove the action ran or that controls work.",
  },
  inspect_trajectory: {
    detail: "Open a trajectory from the selected runtime and mark it reviewed.",
  },
  production_readiness: {
    detail:
      "Not available yet. Production readiness needs evidence-backed verification, which is not part of this release.",
  },
};

/** Session navigation state only: never evidence, permission, or approval. */
type WizardProgress = {
  systemId?: string;
  systemName?: string;
  systemKey?: string;
  systemStatus?: string;
  agentId?: string;
  agentName?: string;
  agentKey?: string;
  versionId?: string;
  versionNumber?: number;
  versionLifecycle?: string;
  runtimeId?: string;
  runtimeName?: string;
  credentialId?: string;
  credentialPrefix?: string;
  integrationId?: string;
  integrationName?: string;
  trajectoryId?: string;
  /** Legacy name; only ever means "the user deferred connecting". */
  connectSkipped?: boolean;
};

/** Cleared whenever the AI system changes. */
const SYSTEM_DEPENDENTS: WizardProgress = {
  agentId: undefined,
  agentName: undefined,
  agentKey: undefined,
  versionId: undefined,
  versionNumber: undefined,
  versionLifecycle: undefined,
  runtimeId: undefined,
  runtimeName: undefined,
  credentialId: undefined,
  credentialPrefix: undefined,
  integrationId: undefined,
  integrationName: undefined,
  trajectoryId: undefined,
};

/** Cleared whenever the runtime identity changes within a system. */
const RUNTIME_DEPENDENTS: WizardProgress = {
  credentialId: undefined,
  credentialPrefix: undefined,
  integrationId: undefined,
  integrationName: undefined,
  trajectoryId: undefined,
};

// Same key earlier builds wrote, so existing session progress is retained.
const STORAGE_KEY = "rai_onboarding_wizard_instance";

function slugify(value: string) {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

function loadProgress(): WizardProgress {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as WizardProgress) : {};
  } catch {
    return {};
  }
}

function saveProgress(progress: WizardProgress) {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(progress));
  } catch {
    // navigation aid only
  }
}

function derivedStep(progress: WizardProgress): number {
  if (!progress.systemId) return 0;
  if (!progress.agentId) return 1;
  if (!progress.versionId || progress.versionLifecycle !== "APPROVED") return 2;
  if (!progress.runtimeId) return 3;
  if (!progress.credentialId && !progress.credentialPrefix) return 4;
  if (!progress.integrationId && !progress.connectSkipped) return 5;
  return 6;
}

type Hydrated = {
  progress: WizardProgress;
  runtimes: RuntimeIdentitySummary[];
  trajectory: TrajectorySummary | null;
};

/**
 * Rebuild navigation state from the server record plus live registry reads.
 * Read-only: it never PATCHes. Evidence (integration, trajectory) is taken only
 * from what the server resolved or an exact runtime match, never `items[0]`.
 */
async function hydrateProgressFromOrg(
  onboarding: OnboardingResponse,
  systems: AISystemSummary[],
  stored: WizardProgress,
): Promise<Hydrated> {
  const serverSystemId = onboarding.onboarding.first_ai_system_id;
  const preferredSystemId = serverSystemId ?? stored.systemId ?? systems[0]?.id;
  // Local state cannot carry another system's selections forward.
  const next: WizardProgress =
    stored.systemId && stored.systemId === preferredSystemId
      ? { ...stored }
      : { connectSkipped: stored.connectSkipped };

  const resolved = onboarding.summary?.connection;
  const resolvedIntegrationId =
    resolved && resolved.state !== "UNSCOPED" ? resolved.integration_id : null;
  const resolvedRuntimeId =
    resolved && resolved.state !== "UNSCOPED" ? resolved.runtime_identity_id : null;
  // The server decides the integration; a stored one is never trusted.
  next.integrationId = resolvedIntegrationId ?? undefined;
  if (!resolvedIntegrationId) next.integrationName = undefined;
  next.trajectoryId = onboarding.onboarding.first_trajectory_id ?? undefined;

  const system = preferredSystemId ? systems.find((item) => item.id === preferredSystemId) : null;
  if (system) {
    next.systemId = system.id;
    next.systemName = system.name;
    next.systemKey = system.system_key;
    next.systemStatus = system.status;
  }
  if (!next.systemId) return { progress: next, runtimes: [], trajectory: null };

  const agents = await getAgents(next.systemId);
  let agentRuntimes: RuntimeIdentitySummary[] = [];
  let chosenAgent = agents.data.find((item) => item.id === next.agentId) ?? agents.data[0];

  // Follow the runtime the server resolved to its own agent.
  if (resolvedRuntimeId) {
    for (const candidate of agents.data) {
      const list = await getRuntimeIdentities({ agent_id: candidate.id });
      if (list.data.some((item) => item.id === resolvedRuntimeId)) {
        chosenAgent = candidate;
        agentRuntimes = list.data;
        break;
      }
    }
  }
  if (!chosenAgent) return { progress: next, runtimes: [], trajectory: null };
  next.agentId = chosenAgent.id;
  next.agentName = chosenAgent.name;
  next.agentKey = chosenAgent.agent_key;

  const runtimeRes = agentRuntimes.length
    ? { data: agentRuntimes }
    : await getRuntimeIdentities({ agent_id: next.agentId });
  const runtimes = runtimeRes.data;
  const desiredRuntimeId = resolvedRuntimeId ?? next.runtimeId;
  const runtime = runtimes.find((item) => item.id === desiredRuntimeId) ?? runtimes[0];
  if (runtime) {
    if (next.runtimeId && next.runtimeId !== runtime.id) {
      Object.assign(next, RUNTIME_DEPENDENTS, {
        integrationId: resolvedIntegrationId ?? undefined,
      });
    }
    next.runtimeId = runtime.id;
    next.runtimeName = runtime.name;
  } else {
    next.runtimeId = undefined;
    next.runtimeName = undefined;
  }

  const versions = await getAgentVersions(next.agentId);
  const preferredVersion =
    versions.data.find((item) => item.id === runtime?.agent_version_id) ??
    versions.data.find((item) => item.id === next.versionId) ??
    versions.data.find((item) => item.lifecycle_status === "APPROVED") ??
    versions.data[0];
  if (preferredVersion) {
    next.versionId = preferredVersion.id;
    next.versionNumber = preferredVersion.version_number;
    next.versionLifecycle = preferredVersion.lifecycle_status;
  }

  if (!next.runtimeId) return { progress: next, runtimes, trajectory: null };

  const creds = await getRuntimeCredentials(next.runtimeId);
  const preferredCred =
    creds.data.find((item) => item.id === next.credentialId) ??
    creds.data.find((item) => item.status === "ACTIVE") ??
    creds.data[0];
  if (preferredCred) {
    next.credentialId = preferredCred.id;
    next.credentialPrefix = preferredCred.credential_key;
  }

  let trajectory: TrajectorySummary | null = null;
  try {
    const trajRes = await getTrajectories({ ai_system_id: next.systemId, limit: 20 });
    trajectory = findMatchingTrajectory(trajRes.data.items, {
      runtimeId: next.runtimeId,
      trajectoryId: next.trajectoryId,
    });
  } catch {
    // trajectory evidence is optional
  }
  // A saved trajectory that does not match this runtime is not carried over.
  if (next.trajectoryId && !trajectory) next.trajectoryId = undefined;

  return { progress: next, runtimes, trajectory };
}

function ContextChain({ items }: { items: [string, string][] }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {items.map(([label, value], index) => (
        <span key={label} className="inline-flex items-center gap-2">
          {index > 0 ? <span className="text-ink-300">→</span> : null}
          <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">
            {label}
          </span>
          <TechnicalValue>{value || "—"}</TechnicalValue>
        </span>
      ))}
    </div>
  );
}

function TechnicalCopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2 rounded-md border border-base-200 bg-base-100 px-3 py-2 font-mono text-xs text-ink-800">
      <span className="min-w-0 flex-1 break-all">{value}</span>
      <button
        type="button"
        className="shrink-0 text-ink-400 hover:text-ink-800"
        aria-label="Copy value"
        onClick={() => {
          void navigator.clipboard?.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        }}
      >
        {copied ? "✓" : "Copy"}
      </button>
    </div>
  );
}

function StepShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel title={title}>
      <div className="space-y-4">{children}</div>
    </Panel>
  );
}

function DoneBanner({ label, onNext }: { label: string; onNext: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-accent-200 bg-accent-50 p-3 text-xs">
      <span className="font-medium text-accent-700">✓ {label}</span>
      <Button size="sm" onClick={onNext}>
        Continue
      </Button>
    </div>
  );
}

function SecretRevealModal({
  secret,
  prefix,
  onDone,
}: {
  secret: string;
  prefix: string;
  onDone: () => void;
}) {
  const [ack, setAck] = useState(false);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/50 p-4">
      <div className="w-full max-w-lg rounded-xl border border-base-200 bg-base-50 p-5 shadow-panel">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-accent-600">
          Credential created
        </div>
        <h2 className="mt-2 text-sm font-semibold text-ink-900">
          Copy this secret now. It will not be shown again.
        </h2>
        <p className="mt-1 text-xs text-ink-500">
          Only the prefix <TechnicalValue>{prefix}</TechnicalValue> is retained.
        </p>
        <div className="mt-4">
          <TechnicalCopyField value={secret} />
        </div>
        <label className="mt-4 flex cursor-pointer items-center gap-2 text-xs text-ink-700">
          <input
            type="checkbox"
            checked={ack}
            onChange={(event) => setAck(event.target.checked)}
            className="size-4"
          />
          I have saved this credential in an approved secret store
        </label>
        <div className="mt-4 flex justify-end">
          <Button disabled={!ack} onClick={onDone}>
            I Have Saved It
          </Button>
        </div>
      </div>
    </div>
  );
}

export function GuidedOnboarding({ canManage }: { canManage: boolean }) {
  const router = useRouter();
  const [checklist, setChecklist] = useState<OnboardingResponse | null>(null);
  const [progress, setProgress] = useState<WizardProgress>({});
  const [step, setStep] = useState(0);
  const [systems, setSystems] = useState<AISystemSummary[]>([]);
  const [runtimes, setRuntimes] = useState<RuntimeIdentitySummary[]>([]);
  const [integrations, setIntegrations] = useState<SdkIntegrationSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [secretReveal, setSecretReveal] = useState<{ secret: string; prefix: string } | null>(null);
  const [credentialSecret, setCredentialSecret] = useState("");
  const [trajectory, setTrajectory] = useState<TrajectorySummary | null>(null);
  const [booting, setBooting] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [selectedChecklist, setSelectedChecklist] = useState("organization_profile");
  const checklistInitialized = useRef(false);
  // Latest displayed runtime, so a slow integration list for a previous runtime
  // can never be applied to the current one.
  const runtimeRef = useRef<string | undefined>(undefined);
  runtimeRef.current = progress.runtimeId;

  const updateProgress = useCallback((patch: WizardProgress) => {
    setProgress((prev) => {
      const next = { ...prev, ...patch };
      saveProgress(next);
      return next;
    });
  }, []);

  const refreshChecklist = useCallback(async () => {
    const data = await fetchOnboarding();
    setChecklist(data);
    return data;
  }, []);

  /** Every setup write goes through here; readers can never reach the network. */
  const saveSetup = useCallback(
    async (body: OnboardingPatchBody) => {
      if (!canManage) throw new Error("Changing setup needs setup-management access.");
      const data = await patchOnboarding(body);
      setChecklist(data);
      return data;
    },
    [canManage],
  );

  const markStep = useCallback(
    (completeStep: string, extras?: OnboardingPatchBody) =>
      saveSetup({ complete_step: completeStep, ...extras }),
    [saveSetup],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setBooting(true);
      setLoadError(null);
      try {
        const stored = loadProgress();
        const [onboarding, systemsRes] = await Promise.all([
          fetchOnboarding(),
          // A reader may not be able to list systems; setup status still renders.
          getAISystems().catch(() => ({ data: [] as AISystemSummary[] })),
        ]);
        if (cancelled) return;
        setChecklist(onboarding);
        setSystems(systemsRes.data);

        let hydrated: Hydrated;
        try {
          hydrated = await hydrateProgressFromOrg(onboarding, systemsRes.data, stored);
        } catch {
          hydrated = {
            progress: { connectSkipped: stored.connectSkipped },
            runtimes: [],
            trajectory: null,
          };
        }
        if (cancelled) return;

        // No PATCH here: opening this page never changes setup state.
        setProgress(hydrated.progress);
        saveProgress(hydrated.progress);
        setRuntimes(hydrated.runtimes);
        setTrajectory(hydrated.trajectory);
        setStep(derivedStep(hydrated.progress));
      } catch (err) {
        // Unknown, not "incomplete": show a finite error with Retry.
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "Unable to load onboarding");
        }
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Read-only observation refresh. It never marks a step.
  useEffect(() => {
    if (step < 5 || !progress.systemId) return;
    let cancelled = false;
    const selection = { integrationId: progress.integrationId, runtimeId: progress.runtimeId };
    const poll = async () => {
      try {
        const data = await fetchOnboarding();
        if (cancelled) return;
        // Ignore a response for anything but the selection on screen.
        if (!selection.integrationId || matchesSelection(data.summary, selection)) {
          setChecklist(data);
        }
        setStatusError(null);
      } catch (err) {
        if (!cancelled) {
          setStatusError(err instanceof Error ? err.message : "Unable to refresh status");
        }
      }
      if (!selection.runtimeId) return;
      try {
        const res = await getTrajectories({ ai_system_id: progress.systemId, limit: 20 });
        if (cancelled) return;
        setTrajectory(
          findMatchingTrajectory(res.data.items, {
            runtimeId: selection.runtimeId,
            trajectoryId: progress.trajectoryId,
          }),
        );
      } catch {
        // trajectory evidence is optional
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 8000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [step, progress.systemId, progress.runtimeId, progress.integrationId, progress.trajectoryId]);

  const loadIntegrations = useCallback(async () => {
    const runtimeId = progress.runtimeId;
    if (!runtimeId) {
      setIntegrations([]);
      return;
    }
    try {
      const res = await listSdkIntegrations();
      if (runtimeRef.current !== runtimeId) return;
      setIntegrations((res.items ?? []).filter((item) => item.runtime_identity_id === runtimeId));
    } catch (err) {
      if (runtimeRef.current === runtimeId) {
        setError(err instanceof Error ? err.message : "Unable to list integrations");
      }
    }
  }, [progress.runtimeId]);

  useEffect(() => {
    if (step === 5) void loadIntegrations();
  }, [step, loadIntegrations]);

  const chain = useMemo(
    (): [string, string][] => [
      ["AI System", progress.systemName ?? "—"],
      ["Agent", progress.agentName ?? "—"],
      ["Version", progress.versionNumber != null ? `v${progress.versionNumber}` : "—"],
      ["Runtime Identity", progress.runtimeName ?? "—"],
    ],
    [progress],
  );

  const summary = checklist?.summary;
  const connection: ConnectionDisplay = scopedConnectionDisplay(summary, {
    integrationId: progress.integrationId,
    runtimeId: progress.runtimeId,
  });

  useEffect(() => {
    if (!checklist || checklistInitialized.current) return;
    const next = checklist.steps.find((item) => !item.done && item.actionable !== false);
    if (next) setSelectedChecklist(next.id);
    checklistInitialized.current = true;
  }, [checklist]);

  const selectedStep = checklist?.steps.find((item) => item.id === selectedChecklist) ?? null;

  async function finishChecklist(stepId: string) {
    // Evidence-only steps can never be completed by a click.
    if (!canMarkManually(stepId)) return;
    setBusy(true);
    setError(null);
    try {
      await markStep(stepId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save this step");
    } finally {
      setBusy(false);
    }
  }

  /** Select (or create) the AI system; incompatible dependents are cleared. */
  async function chooseSystem(system: AISystemSummary) {
    const changed =
      progress.systemId !== system.id || checklist?.onboarding.first_ai_system_id !== system.id;
    updateProgress({
      systemId: system.id,
      systemName: system.name,
      systemKey: system.system_key,
      systemStatus: system.status,
      ...(changed ? SYSTEM_DEPENDENTS : {}),
    });
    if (changed) {
      setTrajectory(null);
      setRuntimes([]);
      setIntegrations([]);
      setCredentialSecret("");
    }
    await markStep("first_ai_system", {
      first_ai_system_id: system.id,
      ...(changed ? { first_integration_id: null, first_trajectory_id: null } : {}),
    });
  }

  /** Switch runtime identity; the old integration/trajectory selections go with it. */
  async function chooseRuntime(runtime: RuntimeIdentitySummary) {
    const changed = progress.runtimeId !== runtime.id;
    updateProgress({
      runtimeId: runtime.id,
      runtimeName: runtime.name,
      ...(changed ? RUNTIME_DEPENDENTS : {}),
    });
    if (changed) {
      setTrajectory(null);
      setIntegrations([]);
      setCredentialSecret("");
    }
    const saved = checklist?.onboarding;
    const hasSavedEvidenceSelection = Boolean(
      saved?.first_integration_id || saved?.first_trajectory_id,
    );
    await markStep("runtime_identity", {
      ...(changed || hasSavedEvidenceSelection
        ? { first_integration_id: null, first_trajectory_id: null }
        : {}),
    });
    if (changed) {
      try {
        const creds = await getRuntimeCredentials(runtime.id);
        const active = creds.data.find((item) => item.status === "ACTIVE") ?? creds.data[0];
        if (active) {
          updateProgress({ credentialId: active.id, credentialPrefix: active.credential_key });
        }
      } catch {
        // credential state is re-read on the credential step
      }
    }
  }

  /** Explicit, authorized selection of an existing integration for this runtime. */
  async function chooseIntegration(integration: SdkIntegrationSummary) {
    if (!progress.systemId) return;
    setBusy(true);
    setError(null);
    try {
      // The PATCH response carries the recomputed server summary.
      await saveSetup({
        first_ai_system_id: progress.systemId,
        first_integration_id: integration.id,
      });
      updateProgress({
        integrationId: integration.id,
        integrationName: integration.name,
        connectSkipped: false,
      });
    } catch (err) {
      // The previous valid selection stays in place.
      setError(err instanceof Error ? err.message : "Unable to select the integration");
    } finally {
      setBusy(false);
    }
  }

  async function markTrajectoryReviewed() {
    if (!trajectory) return;
    setBusy(true);
    setError(null);
    try {
      await markStep("inspect_trajectory", { first_trajectory_id: trajectory.id });
      updateProgress({ trajectoryId: trajectory.id });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save this review");
    } finally {
      setBusy(false);
    }
  }

  async function finishSetup() {
    setBusy(true);
    setError(null);
    try {
      if (canManage && summary && !summary.welcome_complete) {
        await completeWelcome();
        markWelcomeDoneLocally();
        await refreshChecklist();
      }
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to finish setup");
      setBusy(false);
    }
  }

  async function retryStatus() {
    setStatusError(null);
    try {
      await refreshChecklist();
    } catch (err) {
      setStatusError(err instanceof Error ? err.message : "Unable to refresh status");
    }
  }

  if (booting) {
    return (
      <div className="rounded-xl border border-base-200 bg-base-50 p-6 text-sm text-ink-500">
        Loading onboarding…
      </div>
    );
  }

  if (loadError) {
    return (
      <div
        className="space-y-3 rounded-xl border border-base-200 bg-base-50 p-6"
        data-testid="onboarding-load-error"
      >
        <div className="text-sm font-semibold text-ink-900">Setup status unavailable</div>
        <p className="text-sm text-ink-500">{loadError}. This does not mean setup is incomplete.</p>
        <div className="flex gap-2">
          <Button onClick={() => setAttempt((value) => value + 1)}>Retry</Button>
          <Link href="/dashboard">
            <Button variant="ghost">Back to Control Posture</Button>
          </Link>
        </div>
      </div>
    );
  }

  const stepCompleted = [
    Boolean(progress.systemId),
    Boolean(progress.agentId),
    progress.versionLifecycle === "APPROVED",
    Boolean(progress.runtimeId),
    Boolean(progress.credentialId || progress.credentialPrefix),
    connection.kind === "observed",
    false, // control verification has not been run; this screen never turns green
  ];
  const deferredConnect = Boolean(progress.connectSkipped);
  const deferredChecklistIds = deferredConnect ? ["connect_application"] : [];

  return (
    <div className="space-y-5" data-testid="guided-onboarding">
      <SectionHeader
        title="Connect Your First AI System"
        subtitle="Register an AI workload, establish its runtime identity, and begin capturing governed execution evidence."
        actions={
          <Link href="/dashboard">
            <Button variant="ghost">Back to Control Posture</Button>
          </Link>
        }
      />

      {error ? (
        <div className="rounded-lg border border-danger-200 bg-danger-50 p-3 text-sm text-danger-600">
          {error}
        </div>
      ) : null}

      {!canManage ? (
        <div
          className="rounded-lg border border-base-200 bg-base-50 p-3 text-sm text-ink-600"
          data-testid="read-only-notice"
        >
          You have read-only access to setup. Status is shown below; changes need setup-management
          access.
        </div>
      ) : null}

      <ol className="grid grid-cols-2 overflow-hidden rounded-lg border border-base-200 bg-base-50 sm:grid-cols-7">
        {STEPS.map((label, index) => {
          const state = stepperState({
            completed: stepCompleted[index],
            deferred: index === 5 && deferredConnect,
            current: index === step,
          });
          const current = index === step;
          return (
            <li key={label}>
              <button
                type="button"
                onClick={() => setStep(index)}
                data-state={state}
                className={cn(
                  "flex w-full items-center gap-2 border-b-2 px-3 py-3 text-left text-xs",
                  current ? "border-accent-600 bg-accent-50" : "border-transparent",
                )}
              >
                <span
                  className={cn(
                    "flex size-5 items-center justify-center rounded border font-mono text-[10px]",
                    state === "done"
                      ? "border-accent-300 bg-accent-50 text-accent-700"
                      : state === "deferred"
                        ? "border-warn-100 bg-warn-50 text-warn-700"
                        : current
                          ? "border-accent-600 text-accent-600"
                          : "border-base-200 text-ink-400",
                  )}
                >
                  {state === "done" ? "✓" : state === "deferred" ? "–" : index + 1}
                </span>
                <span className={current ? "font-semibold text-ink-900" : "text-ink-500"}>
                  {label}
                  {state === "deferred" ? (
                    <span className="ml-1 text-[10px] font-normal text-warn-700">Deferred</span>
                  ) : null}
                </span>
              </button>
            </li>
          );
        })}
      </ol>

      {progress.systemId ? (
        <div className="rounded-lg border border-base-200 bg-base-50 px-4 py-3">
          <ContextChain items={chain} />
        </div>
      ) : null}

      {checklist ? (
        <div className="rounded-lg border border-base-200 bg-base-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">
                Organization onboarding
              </div>
              <div className="mt-1 text-sm font-semibold text-ink-900">
                {checklist.progress.completed}/{checklist.progress.total} checklist items recorded
              </div>
              <ul
                className="mt-2 flex flex-wrap gap-2 text-[11px] text-ink-600"
                data-testid="evidence-summary"
              >
                <li className="rounded border border-base-200 bg-base-100 px-2 py-0.5">
                  {welcomeLabel(summary)}
                </li>
                <li className="rounded border border-base-200 bg-base-100 px-2 py-0.5">
                  {connection.label}
                  {connection.kind === "observed" && connection.observedAt
                    ? ` · ${new Date(connection.observedAt).toLocaleString()}`
                    : ""}
                </li>
                <li className="rounded border border-base-200 bg-base-100 px-2 py-0.5">
                  {verificationLabel(summary)}
                </li>
              </ul>
              {!summary ? (
                <p className="mt-2 text-xs text-warn-700" data-testid="status-unavailable">
                  Status unavailable.{" "}
                  <button
                    type="button"
                    className="font-semibold underline"
                    onClick={() => void retryStatus()}
                  >
                    Retry
                  </button>
                </p>
              ) : null}
              {statusError ? (
                <p className="mt-2 text-xs text-warn-700">
                  Could not refresh status: {statusError}{" "}
                  <button
                    type="button"
                    className="font-semibold underline"
                    onClick={() => void retryStatus()}
                  >
                    Retry
                  </button>
                </p>
              ) : null}
            </div>
            {checklist.next_recommended_action ? (
              <p className="text-xs text-ink-500">Next: {checklist.next_recommended_action}</p>
            ) : null}
          </div>
          <ul className="mt-3 flex flex-wrap gap-2">
            {checklist.steps.map((item) => {
              const informational = isInformationalStep(item);
              const deferred = !item.done && deferredChecklistIds.includes(item.id);
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedChecklist(item.id);
                      const target = CHECKLIST_WIZARD[item.id];
                      if (target !== undefined) setStep(target);
                    }}
                    className={cn(
                      "rounded-md border px-2 py-1 text-left text-[11px]",
                      selectedChecklist === item.id && "ring-1 ring-accent-600",
                      item.done
                        ? "border-accent-200 bg-accent-50 text-accent-700"
                        : deferred
                          ? "border-warn-100 bg-warn-50 text-warn-700"
                          : "border-base-200 bg-base-100 text-ink-500",
                    )}
                  >
                    {item.done ? "✓ " : deferred ? "– " : informational ? "· " : "○ "}
                    {item.label}
                    {deferred ? " (Deferred)" : ""}
                  </button>
                </li>
              );
            })}
          </ul>
          {selectedStep ? (
            <div className="mt-4 space-y-3 border-t border-base-200 pt-4">
              <div className="text-sm font-semibold text-ink-900">
                {selectedStep.label}
                {completionLabel(selectedStep) ? (
                  <span className="ml-2 text-[11px] font-normal text-accent-700">
                    {completionLabel(selectedStep)}
                  </span>
                ) : null}
              </div>
              <p className="text-xs text-ink-500">{CHECKLIST_GUIDE[selectedStep.id]?.detail}</p>
              {selectedStep.id === "resources" && canManage ? <CatalogUpload compact /> : null}
              <div className="flex flex-wrap gap-2">
                {CHECKLIST_GUIDE[selectedStep.id]?.href ? (
                  <Link href={CHECKLIST_GUIDE[selectedStep.id].href!}>
                    <Button variant="secondary" size="sm">
                      {CHECKLIST_GUIDE[selectedStep.id].hrefLabel}
                    </Button>
                  </Link>
                ) : null}
                {canManage && canMarkManually(selectedStep.id) && !selectedStep.done ? (
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() => void finishChecklist(selectedStep.id)}
                  >
                    {busy ? "Saving…" : manualActionLabel(selectedStep.id)}
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      {canManage ? (
        <>
          {step > 0 && !progress.systemId ? (
            <p className="text-sm text-ink-500">Create an AI system before this connect step.</p>
          ) : null}
          {step > 1 && progress.systemId && !progress.agentId ? (
            <p className="text-sm text-ink-500">Add an agent before this connect step.</p>
          ) : null}
          {step > 2 && progress.agentId && !progress.versionId ? (
            <p className="text-sm text-ink-500">Create a version before this connect step.</p>
          ) : null}

          <div className="max-w-3xl">
            {step === 0 ? (
              <SystemStep
                systems={systems}
                existing={progress.systemName}
                status={progress.systemStatus}
                busy={busy}
                onSelectExisting={async (system) => {
                  setBusy(true);
                  setError(null);
                  try {
                    await chooseSystem(system);
                    setStep(1);
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Unable to select system");
                  } finally {
                    setBusy(false);
                  }
                }}
                onCreate={async (form) => {
                  setBusy(true);
                  setError(null);
                  let created: AISystemSummary | null = null;
                  try {
                    // Systems stay in their domain-default draft state.
                    const res = await createAISystem({
                      system_key: form.key,
                      name: form.name,
                      business_purpose: form.purpose || undefined,
                      business_unit: form.unit || undefined,
                      owner_id: form.owner || undefined,
                      risk_tier: form.risk,
                    });
                    created = res.data;
                    setSystems((prev) => [res.data, ...prev]);
                    await chooseSystem(res.data);
                    setStep(1);
                  } catch (err) {
                    const message =
                      err instanceof Error ? err.message : "Unable to create AI system";
                    setError(
                      created
                        ? `${message} The AI system "${created.name}" was created; select it to continue.`
                        : message,
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
                onDone={() => setStep(1)}
              />
            ) : null}

            {step === 1 && progress.systemId ? (
              <AgentStep
                systemName={progress.systemName ?? ""}
                existing={progress.agentName}
                busy={busy}
                onCreate={async (form) => {
                  setBusy(true);
                  setError(null);
                  try {
                    const res = await createAgent(progress.systemId!, {
                      agent_key: form.key,
                      name: form.name,
                      business_purpose: form.description || undefined,
                    });
                    updateProgress({
                      agentId: res.data.id,
                      agentName: res.data.name,
                      agentKey: res.data.agent_key,
                    });
                    setStep(2);
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Unable to create agent");
                  } finally {
                    setBusy(false);
                  }
                }}
                onDone={() => setStep(2)}
              />
            ) : null}

            {step === 2 && progress.systemId && progress.agentId ? (
              <VersionStep
                systemName={progress.systemName ?? ""}
                agentName={progress.agentName ?? ""}
                versionId={progress.versionId}
                versionNumber={progress.versionNumber}
                lifecycle={progress.versionLifecycle}
                busy={busy}
                onCreate={async (form) => {
                  setBusy(true);
                  setError(null);
                  try {
                    const res = await createAgentVersion(progress.agentId!, {
                      change_summary: "Initial onboarding version",
                      configuration_snapshot: {
                        model: form.model,
                        tools: form.tools
                          .split(",")
                          .map((item) => item.trim())
                          .filter(Boolean),
                      },
                    });
                    updateProgress({
                      versionId: res.data.id,
                      versionNumber: res.data.version_number,
                      versionLifecycle: res.data.lifecycle_status,
                    });
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Unable to create version");
                  } finally {
                    setBusy(false);
                  }
                }}
                onTransition={async (status) => {
                  if (!progress.versionId) return;
                  setBusy(true);
                  setError(null);
                  try {
                    const res = await transitionAgentVersion(progress.versionId, status);
                    // Render the authoritative state the server returned.
                    updateProgress({ versionLifecycle: res.data.lifecycle_status });
                    if (res.data.lifecycle_status === "APPROVED") {
                      await markStep("agent_version");
                    }
                  } catch (err) {
                    // The previous lifecycle state stays on screen.
                    setError(err instanceof Error ? err.message : "Lifecycle transition failed");
                  } finally {
                    setBusy(false);
                  }
                }}
                onContinue={() => setStep(3)}
              />
            ) : null}

            {step === 3 && progress.systemId && progress.agentId && progress.versionId ? (
              <RuntimeStep
                chain={chain}
                systemKey={progress.systemKey ?? "runtime"}
                existing={progress.runtimeName}
                currentRuntimeId={progress.runtimeId}
                runtimes={runtimes}
                versionLabel={progress.versionNumber != null ? `v${progress.versionNumber}` : "v1"}
                versionLifecycle={progress.versionLifecycle}
                busy={busy}
                onBackToVersion={() => setStep(2)}
                onSelect={async (runtime) => {
                  setBusy(true);
                  setError(null);
                  try {
                    await chooseRuntime(runtime);
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Unable to switch runtime");
                  } finally {
                    setBusy(false);
                  }
                }}
                onCreate={async (form) => {
                  setBusy(true);
                  setError(null);
                  try {
                    const res = await createRuntimeIdentity({
                      agent_id: progress.agentId!,
                      agent_version_id: progress.versionId!,
                      name: form.name,
                      environment: form.environment,
                      identity_type: "SERVICE",
                    });
                    setRuntimes((prev) => [res.data, ...prev]);
                    await chooseRuntime(res.data);
                    setStep(4);
                  } catch (err) {
                    setError(
                      err instanceof Error ? err.message : "Unable to create runtime identity",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
                onDone={() => setStep(4)}
              />
            ) : null}

            {step === 4 && progress.runtimeId ? (
              <CredentialStep
                existing={progress.credentialPrefix}
                busy={busy}
                onIssue={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    const res = await issueRuntimeCredential(progress.runtimeId!);
                    const secret = res.data.plaintext_secret ?? "";
                    const prefix = res.data.credential_key;
                    // Only the prefix is kept in wizard progress, never the secret.
                    updateProgress({ credentialId: res.data.id, credentialPrefix: prefix });
                    if (secret) {
                      setCredentialSecret(secret);
                      setSecretReveal({ secret, prefix });
                    } else {
                      setStep(5);
                    }
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "Unable to issue credential");
                  } finally {
                    setBusy(false);
                  }
                }}
                onDone={() => setStep(5)}
              />
            ) : null}

            {step === 5 ? (
              <ConnectStep
                systemKey={progress.systemKey ?? "—"}
                agentKey={progress.agentKey ?? "—"}
                versionLabel={progress.versionNumber != null ? `v${progress.versionNumber}` : "—"}
                runtimeName={progress.runtimeName ?? "—"}
                runtimeId={progress.runtimeId}
                secret={credentialSecret}
                connection={connection}
                summaryAvailable={Boolean(summary)}
                integrations={integrations}
                selectedIntegrationId={progress.integrationId}
                deferred={deferredConnect}
                busy={busy}
                onSelectIntegration={(integration) => void chooseIntegration(integration)}
                onRefreshIntegrations={() => void loadIntegrations()}
                onRetryStatus={() => void retryStatus()}
                onContinue={() => setStep(6)}
                onDefer={() => {
                  // Navigation preference only: nothing is saved as complete.
                  updateProgress({ connectSkipped: true });
                  setStep(6);
                }}
              />
            ) : null}

            {step === 6 ? (
              <VerifyStep
                summary={summary}
                connection={connection}
                trajectory={trajectory}
                trajectoryReviewed={Boolean(
                  checklist?.steps.find((item) => item.id === "inspect_trajectory")?.done,
                )}
                canManage={canManage}
                busy={busy}
                onMarkReviewed={() => void markTrajectoryReviewed()}
                onFinish={() => void finishSetup()}
                onRetryStatus={() => void retryStatus()}
              />
            ) : null}
          </div>
        </>
      ) : (
        <div className="max-w-3xl">
          <VerifyStep
            summary={summary}
            connection={connection}
            trajectory={trajectory}
            trajectoryReviewed={Boolean(
              checklist?.steps.find((item) => item.id === "inspect_trajectory")?.done,
            )}
            canManage={false}
            busy={busy}
            onMarkReviewed={() => undefined}
            onFinish={() => router.push("/dashboard")}
            onRetryStatus={() => void retryStatus()}
          />
        </div>
      )}

      {secretReveal ? (
        <SecretRevealModal
          secret={secretReveal.secret}
          prefix={secretReveal.prefix}
          onDone={() => {
            setSecretReveal(null);
            setStep(5);
          }}
        />
      ) : null}
    </div>
  );
}

function SystemStep({
  systems,
  existing,
  status,
  busy,
  onSelectExisting,
  onCreate,
  onDone,
}: {
  systems: AISystemSummary[];
  existing?: string;
  status?: string;
  busy: boolean;
  onSelectExisting: (system: AISystemSummary) => Promise<void>;
  onCreate: (form: {
    name: string;
    key: string;
    purpose: string;
    unit: string;
    owner: string;
    risk: string;
  }) => Promise<void>;
  onDone: () => void;
}) {
  const [pick, setPick] = useState("");
  const [touched, setTouched] = useState(false);
  const [form, setForm] = useState({
    name: "",
    key: "",
    purpose: "",
    unit: "",
    owner: "",
    risk: "MEDIUM",
  });

  if (existing) {
    return (
      <StepShell title="Step 1 — AI System">
        <DoneBanner label={`${existing} registered`} onNext={onDone} />
        {status ? (
          <div className="flex items-center gap-2 text-xs text-ink-500">
            Lifecycle state <StatusBadge status={status} />
            <span>New systems stay in their draft state until reviewed.</span>
          </div>
        ) : null}
      </StepShell>
    );
  }

  return (
    <StepShell title="Step 1 — AI System">
      {systems.length > 0 ? (
        <FormField label="Use an existing AI system">
          <div className="flex gap-2">
            <Select value={pick} onChange={(event) => setPick(event.target.value)}>
              <option value="">Select…</option>
              {systems.map((system) => (
                <option key={system.id} value={system.id}>
                  {system.name}
                </option>
              ))}
            </Select>
            <Button
              variant="secondary"
              disabled={!pick || busy}
              onClick={() => {
                const system = systems.find((item) => item.id === pick);
                if (system) void onSelectExisting(system);
              }}
            >
              Select
            </Button>
          </div>
        </FormField>
      ) : null}

      <FormField label="Display name" required>
        <Input
          autoFocus
          value={form.name}
          disabled={busy}
          placeholder="Lapeep"
          onChange={(event) => {
            const name = event.target.value;
            setForm((prev) => ({
              ...prev,
              name,
              ...(!touched ? { key: slugify(name) } : {}),
            }));
          }}
        />
      </FormField>
      <FormField label="System key" hint="Auto-generated; editable until created." required>
        <Input
          className="font-mono"
          value={form.key}
          disabled={busy}
          onChange={(event) => {
            setTouched(true);
            setForm((prev) => ({ ...prev, key: slugify(event.target.value) }));
          }}
        />
      </FormField>
      <FormField label="Business purpose">
        <Textarea
          rows={3}
          value={form.purpose}
          disabled={busy}
          onChange={(event) => setForm((prev) => ({ ...prev, purpose: event.target.value }))}
        />
      </FormField>
      <div className="grid gap-4 sm:grid-cols-3">
        <FormField label="Business unit" required>
          <Input
            list="onboarding-bu"
            value={form.unit}
            disabled={busy}
            onChange={(event) => setForm((prev) => ({ ...prev, unit: event.target.value }))}
          />
          <datalist id="onboarding-bu">
            {BUSINESS_UNITS.map((unit) => (
              <option key={unit} value={unit} />
            ))}
          </datalist>
        </FormField>
        <FormField label="Owner" required>
          <Input
            value={form.owner}
            disabled={busy}
            onChange={(event) => setForm((prev) => ({ ...prev, owner: event.target.value }))}
          />
        </FormField>
        <FormField label="Risk tier" required>
          <Select
            value={form.risk}
            disabled={busy}
            onChange={(event) => setForm((prev) => ({ ...prev, risk: event.target.value }))}
          >
            {RISK_TIERS.map((tier) => (
              <option key={tier} value={tier}>
                {tier}
              </option>
            ))}
          </Select>
        </FormField>
      </div>
      <Button
        disabled={busy || !form.name.trim() || !form.key || !form.unit.trim() || !form.owner.trim()}
        onClick={() => void onCreate(form)}
      >
        {busy ? "Creating…" : "Create AI System"}
      </Button>
    </StepShell>
  );
}

function AgentStep({
  systemName,
  existing,
  busy,
  onCreate,
  onDone,
}: {
  systemName: string;
  existing?: string;
  busy: boolean;
  onCreate: (form: { name: string; key: string; description: string }) => Promise<void>;
  onDone: () => void;
}) {
  const [touched, setTouched] = useState(false);
  const [form, setForm] = useState({ name: "", key: "", description: "" });

  if (existing) {
    return (
      <StepShell title="Step 2 — Agent">
        <DoneBanner label={`${existing} created`} onNext={onDone} />
      </StepShell>
    );
  }

  return (
    <StepShell title="Step 2 — Agent">
      <ContextChain items={[["AI System", systemName]]} />
      <FormField label="Agent name" required>
        <Input
          autoFocus
          value={form.name}
          disabled={busy}
          placeholder="Customer Support Agent"
          onChange={(event) => {
            const name = event.target.value;
            setForm((prev) => ({
              ...prev,
              name,
              ...(!touched ? { key: slugify(name) } : {}),
            }));
          }}
        />
      </FormField>
      <FormField label="Agent key" required>
        <Input
          className="font-mono"
          value={form.key}
          disabled={busy}
          onChange={(event) => {
            setTouched(true);
            setForm((prev) => ({ ...prev, key: slugify(event.target.value) }));
          }}
        />
      </FormField>
      <FormField label="Description / purpose (optional)">
        <Textarea
          rows={3}
          value={form.description}
          disabled={busy}
          onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
        />
      </FormField>
      <Button disabled={busy || !form.name.trim() || !form.key} onClick={() => void onCreate(form)}>
        {busy ? "Creating…" : "Create Agent"}
      </Button>
    </StepShell>
  );
}

function VersionStep({
  systemName,
  agentName,
  versionId,
  versionNumber,
  lifecycle,
  busy,
  onCreate,
  onTransition,
  onContinue,
}: {
  systemName: string;
  agentName: string;
  versionId?: string;
  versionNumber?: number;
  lifecycle?: string;
  busy: boolean;
  onCreate: (form: { model: string; tools: string }) => Promise<void>;
  onTransition: (status: string) => Promise<void>;
  onContinue: () => void;
}) {
  const [model, setModel] = useState("gpt-4o-mini");
  const [tools, setTools] = useState("lookup_order");
  const label = versionNumber != null ? `v${versionNumber}` : "v1";

  return (
    <StepShell title="Step 3 — Agent Version">
      <ContextChain
        items={[
          ["System", systemName],
          ["Agent", agentName],
          ["Version", label],
        ]}
      />
      {!versionId ? (
        <>
          <FormField label="Model" hint="Required before review.">
            <Input
              className="font-mono"
              value={model}
              disabled={busy}
              onChange={(event) => setModel(event.target.value)}
            />
          </FormField>
          <FormField label="Tools (optional)">
            <Input
              className="font-mono"
              value={tools}
              disabled={busy}
              onChange={(event) => setTools(event.target.value)}
            />
          </FormField>
          <Button disabled={busy || !model.trim()} onClick={() => void onCreate({ model, tools })}>
            {busy ? "Creating…" : "Create Version"}
          </Button>
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-base-200 bg-base-100 p-3 text-xs">
            <span className="font-mono font-semibold">{label}</span>
            {lifecycle ? <StatusBadge status={lifecycle} /> : null}
            <span className="text-ink-500">
              DRAFT → UNDER REVIEW → APPROVED. Only approved versions can be bound to a runtime
              identity.
            </span>
          </div>
          {lifecycle === "DRAFT" ? (
            <Button disabled={busy} onClick={() => void onTransition("UNDER_REVIEW")}>
              {busy ? "Submitting…" : "Submit for Review"}
            </Button>
          ) : null}
          {lifecycle === "UNDER_REVIEW" ? (
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => void onTransition("DRAFT")}
              >
                Return to Draft
              </Button>
              <Button disabled={busy} onClick={() => void onTransition("APPROVED")}>
                {busy ? "Approving…" : "Approve Version"}
              </Button>
            </div>
          ) : null}
          {lifecycle === "APPROVED" ? (
            <DoneBanner label={`${label} approved`} onNext={onContinue} />
          ) : null}
        </>
      )}
    </StepShell>
  );
}

function RuntimeStep({
  chain,
  systemKey,
  existing,
  currentRuntimeId,
  runtimes,
  versionLabel,
  versionLifecycle,
  busy,
  onCreate,
  onSelect,
  onBackToVersion,
  onDone,
}: {
  chain: [string, string][];
  systemKey: string;
  existing?: string;
  currentRuntimeId?: string;
  runtimes: RuntimeIdentitySummary[];
  versionLabel: string;
  versionLifecycle?: string;
  busy: boolean;
  onCreate: (form: { name: string; environment: string }) => Promise<void>;
  onSelect: (runtime: RuntimeIdentitySummary) => Promise<void>;
  onBackToVersion: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(`${systemKey}-production`.slice(0, 63));
  const [environment, setEnvironment] = useState("production");
  const [pick, setPick] = useState("");
  const approved = versionLifecycle === "APPROVED";

  if (existing) {
    const others = runtimes.filter((item) => item.id !== currentRuntimeId);
    return (
      <StepShell title="Step 4 — Runtime Identity">
        <DoneBanner label={`${existing} created`} onNext={onDone} />
        {others.length > 0 ? (
          <FormField label="Use a different runtime identity">
            <div className="flex gap-2">
              <Select value={pick} onChange={(event) => setPick(event.target.value)}>
                <option value="">Select…</option>
                {others.map((runtime) => (
                  <option key={runtime.id} value={runtime.id}>
                    {runtime.name}
                  </option>
                ))}
              </Select>
              <Button
                variant="secondary"
                disabled={!pick || busy}
                onClick={() => {
                  const runtime = runtimes.find((item) => item.id === pick);
                  if (runtime) void onSelect(runtime).then(() => setPick(""));
                }}
              >
                Switch
              </Button>
            </div>
            <p className="mt-1 text-[11px] text-ink-500">
              Switching clears the saved integration and trajectory selection for the previous
              runtime identity.
            </p>
          </FormField>
        ) : null}
      </StepShell>
    );
  }

  return (
    <StepShell title="Step 4 — Runtime Identity">
      <dl className="grid grid-cols-2 gap-3 rounded-md border border-base-200 bg-base-100 p-3 text-xs sm:grid-cols-4">
        {chain.slice(0, 3).map(([label, value]) => (
          <div key={label}>
            <dt className="text-[10px] uppercase text-ink-400">{label}</dt>
            <dd className="font-mono text-ink-800">{value}</dd>
          </div>
        ))}
        <div>
          <dt className="text-[10px] uppercase text-ink-400">Environment</dt>
          <dd>{environment}</dd>
        </div>
      </dl>
      {!approved ? (
        <div
          className="space-y-2 rounded-md border border-warn-100 bg-warn-50 p-3 text-xs text-warn-700"
          data-testid="runtime-blocked"
        >
          <p>
            Runtime creation is blocked until {versionLabel} is approved
            {versionLifecycle
              ? ` (currently ${versionLifecycle.replace(/_/g, " ").toLowerCase()})`
              : ""}
            . Approval is an explicit review step and is never done for you.
          </p>
          <Button variant="secondary" size="sm" onClick={onBackToVersion}>
            Go to version review
          </Button>
        </div>
      ) : null}
      <FormField label="Runtime identity name" required>
        <Input
          className="font-mono"
          value={name}
          disabled={busy || !approved}
          onChange={(event) => setName(slugify(event.target.value))}
        />
      </FormField>
      <FormField label="Environment">
        <Select
          value={environment}
          disabled={busy || !approved}
          onChange={(event) => setEnvironment(event.target.value)}
        >
          <option value="production">Production</option>
          <option value="staging">Staging</option>
          <option value="development">Development</option>
        </Select>
      </FormField>
      <p className="text-xs text-ink-500">
        After creation, explicitly issue a credential, then link an integration to see recorded
        runtime activity.
      </p>
      <Button
        disabled={busy || !name || !approved}
        onClick={() => void onCreate({ name, environment })}
      >
        {busy ? "Creating…" : "Create Runtime Identity"}
      </Button>
    </StepShell>
  );
}

function CredentialStep({
  existing,
  busy,
  onIssue,
  onDone,
}: {
  existing?: string;
  busy: boolean;
  onIssue: () => Promise<void>;
  onDone: () => void;
}) {
  return (
    <StepShell title="Step 5 — Runtime Credential">
      {existing ? (
        <DoneBanner label={`Credential ${existing}… active`} onNext={onDone} />
      ) : (
        <>
          <p className="text-xs text-ink-500">
            The credential authenticates your application as this runtime identity. The secret is
            shown once.
          </p>
          <Button disabled={busy} onClick={() => void onIssue()}>
            {busy ? "Issuing…" : "Issue Runtime Credential"}
          </Button>
        </>
      )}
    </StepShell>
  );
}

function ConnectStep({
  systemKey,
  agentKey,
  versionLabel,
  runtimeName,
  runtimeId,
  secret,
  connection,
  summaryAvailable,
  integrations,
  selectedIntegrationId,
  deferred,
  busy,
  onSelectIntegration,
  onRefreshIntegrations,
  onRetryStatus,
  onContinue,
  onDefer,
}: {
  systemKey: string;
  agentKey: string;
  versionLabel: string;
  runtimeName: string;
  runtimeId?: string;
  secret: string;
  connection: ConnectionDisplay;
  summaryAvailable: boolean;
  integrations: SdkIntegrationSummary[];
  selectedIntegrationId?: string;
  deferred: boolean;
  busy: boolean;
  onSelectIntegration: (integration: SdkIntegrationSummary) => void;
  onRefreshIntegrations: () => void;
  onRetryStatus: () => void;
  onContinue: () => void;
  onDefer: () => void;
}) {
  const [pick, setPick] = useState("");
  const snippet = `// Authenticate with your runtime credential (server-side only)
// Never place secrets in browser JavaScript.

const response = await fetch("https://api.example.com/v1/runtime/...", {
  headers: {
    Authorization: \`Bearer \${process.env.RAI_RUNTIME_CREDENTIAL}\`,
    "X-RAI-Client": "my-app",
  },
});`;

  return (
    <StepShell title="Step 6 — Connect Application">
      <div
        className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-base-200 bg-base-100 p-3"
        data-testid="connection-status"
      >
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-[10px] font-semibold uppercase text-ink-400">
            Recorded activity
          </span>
          <span className="font-medium text-ink-800">{connection.label}</span>
          {connection.kind === "observed" && connection.observedAt ? (
            <span className="text-ink-500">
              Last recorded {new Date(connection.observedAt).toLocaleString()}
            </span>
          ) : null}
          {deferred ? <span className="text-warn-700">Deferred</span> : null}
        </div>
        {!summaryAvailable ? (
          <Button size="sm" variant="secondary" onClick={onRetryStatus}>
            Retry
          </Button>
        ) : null}
      </div>
      <p className="text-xs text-ink-500">
        A recorded heartbeat is historical activity. It does not show a live connection or that
        controls work.
      </p>

      <div className="space-y-2 rounded-md border border-base-200 p-3">
        <div className="text-[10px] font-semibold uppercase text-ink-400">
          Integration for this runtime identity
        </div>
        {integrations.length === 0 ? (
          <div className="space-y-2 text-xs text-ink-500" data-testid="no-integration">
            <p>
              No integration is linked to this runtime identity yet. An integration must be linked
              before recorded activity can be shown.
            </p>
            <div className="flex flex-wrap gap-2">
              {runtimeId ? (
                <Link href={`/governance/runtime-identities/${runtimeId}`}>
                  <Button variant="secondary" size="sm">
                    Open runtime identity
                  </Button>
                </Link>
              ) : null}
              <Button variant="ghost" size="sm" onClick={onRefreshIntegrations}>
                Refresh
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            <Select
              aria-label="Integration"
              value={pick || selectedIntegrationId || ""}
              disabled={busy}
              onChange={(event) => setPick(event.target.value)}
            >
              <option value="">Select…</option>
              {integrations.map((integration) => (
                <option key={integration.id} value={integration.id}>
                  {integration.name}
                </option>
              ))}
            </Select>
            <Button
              variant="secondary"
              disabled={busy || !pick || pick === selectedIntegrationId}
              onClick={() => {
                const integration = integrations.find((item) => item.id === pick);
                if (integration) onSelectIntegration(integration);
              }}
            >
              {busy ? "Saving…" : "Use this integration"}
            </Button>
          </div>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {(
          [
            ["AI System", systemKey],
            ["Agent", agentKey],
            ["Version", versionLabel],
            ["Runtime Identity", runtimeName],
          ] as const
        ).map(([label, value]) => (
          <div key={label}>
            <div className="mb-1 text-[10px] font-semibold uppercase text-ink-400">{label}</div>
            <TechnicalCopyField value={value} />
          </div>
        ))}
      </div>

      {secret ? (
        <div>
          <div className="mb-1 text-[10px] font-semibold uppercase text-ink-400">
            Credential (this session)
          </div>
          <TechnicalCopyField value={secret} />
        </div>
      ) : null}

      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase text-ink-400">
          Example integration
        </div>
        <pre className="overflow-x-auto rounded-md border border-base-200 bg-base-100 p-3 font-mono text-[11px] text-ink-700">
          {snippet}
        </pre>
      </div>

      <div className="flex flex-wrap gap-2">
        <Link href="/docs/quickstart">
          <Button variant="ghost" size="sm">
            Developer quickstart
          </Button>
        </Link>
        <Button size="sm" variant="secondary" disabled={busy} onClick={onContinue}>
          Continue
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onDefer}>
          Connect later
        </Button>
      </div>
      <p className="text-[11px] text-ink-500">
        Connect later only defers this step. Connection and verification stay incomplete.
      </p>
    </StepShell>
  );
}

function VerifyStep({
  summary,
  connection,
  trajectory,
  trajectoryReviewed,
  canManage,
  busy,
  onMarkReviewed,
  onFinish,
  onRetryStatus,
}: {
  summary: OnboardingResponse["summary"];
  connection: ConnectionDisplay;
  trajectory: TrajectorySummary | null;
  trajectoryReviewed: boolean;
  canManage: boolean;
  busy: boolean;
  onMarkReviewed: () => void;
  onFinish: () => void;
  onRetryStatus: () => void;
}) {
  const welcomeDone = Boolean(summary?.welcome_complete);
  return (
    <StepShell title="Step 7 — Review and finish">
      <dl
        className="grid gap-3 rounded-md border border-base-200 bg-base-100 p-3 text-xs sm:grid-cols-3"
        data-testid="verify-summary"
      >
        <div>
          <dt className="text-[10px] uppercase text-ink-400">Welcome</dt>
          <dd className="font-medium text-ink-800">{welcomeLabel(summary)}</dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-ink-400">Runtime activity</dt>
          <dd className="font-medium text-ink-800">
            {connection.label}
            {connection.kind === "observed" && connection.observedAt
              ? ` · ${new Date(connection.observedAt).toLocaleString()}`
              : ""}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] uppercase text-ink-400">Controls</dt>
          <dd className="font-medium text-ink-800">{verificationLabel(summary)}</dd>
        </div>
      </dl>
      <p className="text-xs text-ink-500">
        Finishing setup closes the introduction. It does not verify that controls work and is not a
        production-readiness or compliance result.
      </p>

      {!summary ? (
        <div
          className="flex items-center gap-2 text-xs text-warn-700"
          data-testid="verify-unavailable"
        >
          Status unavailable.
          <Button size="sm" variant="secondary" onClick={onRetryStatus}>
            Retry
          </Button>
        </div>
      ) : null}

      {trajectory ? (
        <div className="grid gap-3 rounded-md border border-base-200 bg-base-100 p-3 text-xs sm:grid-cols-4">
          <div>
            <div className="text-[10px] uppercase text-ink-400">Trajectory</div>
            <TechnicalValue>{trajectory.id}</TechnicalValue>
          </div>
          <div>
            <div className="text-[10px] uppercase text-ink-400">Status</div>
            <StatusBadge status={trajectory.status} />
          </div>
          <div>
            <div className="text-[10px] uppercase text-ink-400">Risk</div>
            {trajectory.assurance_summary?.highest ? (
              <StatusBadge status={trajectory.assurance_summary.highest} />
            ) : (
              "—"
            )}
          </div>
          <div>
            <div className="text-[10px] uppercase text-ink-400">Events</div>
            <span className="font-mono">{trajectory.event_count}</span>
          </div>
        </div>
      ) : (
        <p className="text-xs text-ink-500">
          No trajectory from the selected runtime identity has been recorded yet.
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {trajectory ? (
          <Link href={`/runtime/trajectories/${trajectory.id}`}>
            <Button variant="secondary">Inspect Trajectory</Button>
          </Link>
        ) : null}
        {trajectory && canManage && !trajectoryReviewed ? (
          <Button variant="secondary" disabled={busy} onClick={onMarkReviewed}>
            Mark reviewed
          </Button>
        ) : null}
        {trajectoryReviewed ? (
          <span className="self-center text-xs text-accent-700">Marked complete</span>
        ) : null}
        <Button disabled={busy || !summary} onClick={onFinish}>
          {busy ? "Finishing…" : welcomeDone || !canManage ? "Return to dashboard" : "Finish setup"}
        </Button>
      </div>
    </StepShell>
  );
}
