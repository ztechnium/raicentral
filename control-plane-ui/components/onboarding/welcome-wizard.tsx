"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { CatalogUpload } from "@/components/onboarding/catalog-upload";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { createAgent, createAgentVersion, createAISystem } from "@/lib/api";
import {
  completeWelcome,
  fetchWelcome,
  patchOnboarding,
  saveWelcomeProfile,
  seedWelcomeTemplate,
  type WelcomeState,
  type WelcomeTemplate,
} from "@/lib/public-api";
import { markWelcomeDoneLocally, welcomeDestination } from "@/lib/welcome-gate";
import { cn } from "@/lib/utils";

const INDUSTRIES = [
  "Technology",
  "Financial Services",
  "Healthcare",
  "Government",
  "Retail",
  "Manufacturing",
  "Education",
  "Other",
];

const STEPS = ["Organization", "Catalog", "Implement"] as const;
const MAX_LOGO_BYTES = 512 * 1024;

function slugify(value: string) {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

function apiBaseUrl() {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";
}

function typescriptExample(baseUrl: string, integrationId: string, tool: string) {
  return `import { RAIClient } from "@ztechnium/rai-sdk";

const client = new RAIClient({
  baseUrl: ${JSON.stringify(baseUrl)},
  apiKey: process.env.RAI_AGENT_KEY!, // paste the credential from Connect
  integrationId: ${JSON.stringify(integrationId)},
});

await client.heartbeat();

const session = await client.startSession({
  channel: "application",
});

const decision = await session.authorizeAction({
  tool: ${JSON.stringify(tool)},
  parameters: {},
});

if (decision.allowed) {
  // Run the side effect only after ALLOW, then report it.
  await decision.reportExecution(client, { status: "SUCCEEDED" });
}

await session.end();
`;
}

function pythonExample(baseUrl: string, integrationId: string, tool: string) {
  return `from rai_sdk import RAIClient

client = RAIClient(
    base_url=${JSON.stringify(baseUrl)},
    api_key="rai_...",  # paste the credential from Connect
    integration_id=${JSON.stringify(integrationId)},
)

client.heartbeat()
session = client.start_session(channel="application")
decision = session.authorize_action(
    tool=${JSON.stringify(tool)},
    parameters={},
)

if decision.allowed:
    # Run the side effect only after ALLOW, then report it.
    decision.report_execution(client, status="SUCCEEDED")

session.end()
client.close()
`;
}

/** Records created by manual setup so a failed step can resume without duplicates. */
type CreatedDraft = {
  systemId?: string;
  systemName?: string;
  agentId?: string;
  versionId?: string;
};

export function WelcomeWizard() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<CreatedDraft>({});
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [industry, setIndustry] = useState("Technology");
  const [logo, setLogo] = useState<string | null>(null);
  const [templates, setTemplates] = useState<WelcomeTemplate[]>([]);
  const [mode, setMode] = useState<"choose" | "manual">("choose");
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [systemName, setSystemName] = useState("");
  const [agentName, setAgentName] = useState("");
  const [modelName, setModelName] = useState("");
  const [tools, setTools] = useState("");
  const [integrationId, setIntegrationId] = useState("YOUR_INTEGRATION_ID");
  const [toolName, setToolName] = useState("your_tool");
  const [copied, setCopied] = useState<"ts" | "py" | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    void fetchWelcome()
      .then((state) => {
        if (cancelled) return;
        const destination = welcomeDestination({
          welcomeCompleted: state.welcome_completed,
          pathname: "/welcome",
        });
        if (destination) {
          router.replace(destination);
          return;
        }
        applyState(state);
      })
      .catch((err: unknown) => {
        // A failed fetch is "unknown", never "incomplete".
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Unable to load welcome");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => load(), [load]);

  function applyState(state: WelcomeState) {
    setName(state.organization.name);
    setIndustry(state.organization.industry || "Technology");
    setLogo(state.organization.logo ?? null);
    setTemplates(state.templates);
    if (state.setup?.integration_id) setIntegrationId(state.setup.integration_id);
    if (state.setup?.tool_name) setToolName(state.setup.tool_name);
    if (state.setup?.template_key) setSelectedTemplate(state.setup.template_key);
  }

  const examples = useMemo(
    () => ({
      typescript: typescriptExample(apiBaseUrl(), integrationId, toolName),
      python: pythonExample(apiBaseUrl(), integrationId, toolName),
    }),
    [integrationId, toolName],
  );

  async function onLogo(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("Logo must be an image.");
      return;
    }
    if (file.size > MAX_LOGO_BYTES) {
      setError("Logo must be 512 KB or smaller.");
      return;
    }
    const dataUrl = await fileToDataUrl(file);
    setLogo(dataUrl);
    setError(null);
  }

  async function saveProfile() {
    setBusy(true);
    setError(null);
    try {
      const state = await saveWelcomeProfile({
        name,
        industry,
        logo,
      });
      applyState(state);
      setStep(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to save the organization");
    } finally {
      setBusy(false);
    }
  }

  async function seedTemplate(key: string) {
    setBusy(true);
    setError(null);
    setSelectedTemplate(key);
    try {
      const state = await seedWelcomeTemplate(key);
      applyState(state);
      if (state.seeded?.tool_name) setToolName(state.seeded.tool_name);
      if (state.seeded?.integration_id) setIntegrationId(state.seeded.integration_id);
      setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to seed the example");
    } finally {
      setBusy(false);
    }
  }

  async function saveManual() {
    setBusy(true);
    setError(null);
    // Resume from what already exists instead of creating a second system.
    const created: CreatedDraft = { ...draft };
    const remember = (patch: CreatedDraft) => {
      Object.assign(created, patch);
      setDraft({ ...created });
    };
    try {
      const systemKey = slugify(systemName);
      const agentKey = slugify(agentName);
      if (!created.systemId && (!systemKey || !agentKey)) {
        throw new Error("Enter an AI system name and an agent name.");
      }
      if (!created.systemId) {
        const system = await createAISystem({
          system_key: systemKey,
          name: systemName.trim(),
          business_purpose: "First governed AI system",
          risk_tier: "MEDIUM",
        });
        remember({ systemId: system.data.id, systemName: system.data.name });
      }
      if (!created.agentId) {
        const agent = await createAgent(created.systemId!, {
          agent_key: agentKey,
          name: agentName.trim(),
        });
        remember({ agentId: agent.data.id });
      }
      const toolList = tools
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      if (!created.versionId) {
        const version = await createAgentVersion(created.agentId!, {
          change_summary: "Initial manual onboarding version",
          configuration_snapshot: {
            model: modelName.trim() || undefined,
            tools: toolList,
          },
        });
        remember({ versionId: version.data.id });
      }
      // Drafts stay drafts: no review or approval is attempted here.
      await patchOnboarding({
        complete_step: "agent_version",
        first_ai_system_id: created.systemId!,
      });
      setToolName(toolList[0] || "your_tool");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to create the AI system";
      const remaining = created.systemId
        ? created.agentId
          ? "The AI system and agent were created; the version still needs to be created."
          : "The AI system was created; the agent and version still need to be created."
        : "";
      setError(
        remaining
          ? `${message} ${remaining} Retry to continue with the existing records.`
          : message,
      );
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      await completeWelcome();
      markWelcomeDoneLocally();
      router.replace("/app/onboarding");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to finish welcome");
      setBusy(false);
    }
  }

  async function copy(kind: "ts" | "py", value: string) {
    await navigator.clipboard.writeText(value);
    setCopied(kind);
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-ink-500">
        Loading your organization…
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-md space-y-3" data-testid="welcome-load-error">
          <h1 className="text-base font-semibold text-ink-900">Welcome is unavailable right now</h1>
          <p className="text-sm text-ink-500">
            {loadError}. Your setup status is unknown, not incomplete.
          </p>
          <div className="flex gap-2">
            <Button onClick={() => load()}>Retry</Button>
            <Link href="/dashboard">
              <Button variant="ghost">Back to dashboard</Button>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-base-100 text-ink-900" data-testid="welcome-wizard">
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col px-4 py-8 sm:px-8">
        <div className="mb-8">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">
            Developer setup
          </p>
          <h1 className="mt-1 text-2xl font-semibold">Welcome to RaiCentral</h1>
          <p className="mt-2 max-w-2xl text-sm text-ink-500">
            Set up the organization, load sample data or register your own AI system, then connect
            with the TypeScript or Python SDK.
          </p>
        </div>

        <ol className="mb-6 grid grid-cols-3 overflow-hidden rounded-lg border border-base-200 bg-base-50">
          {STEPS.map((label, index) => (
            <li key={label}>
              <button
                type="button"
                onClick={() => setStep(index)}
                className={cn(
                  "flex w-full items-center gap-2 px-4 py-3 text-left text-sm",
                  step === index ? "bg-accent-50 text-ink-900" : "text-ink-500",
                )}
              >
                <span className="font-mono text-xs">{index + 1}</span>
                {label}
              </button>
            </li>
          ))}
        </ol>

        {error ? (
          <div className="mb-4 rounded-lg border border-danger-200 bg-danger-50 p-3 text-sm text-danger-600">
            {error}
          </div>
        ) : null}

        {step === 0 ? (
          <section className="max-w-xl space-y-5">
            <div className="flex items-center gap-4">
              <div className="flex size-16 items-center justify-center overflow-hidden rounded-lg border border-base-200 bg-base-50">
                {logo ? (
                  // eslint-disable-next-line @next/next/no-img-element -- organization logo is a data URL
                  <img src={logo} alt="" className="size-full object-cover" />
                ) : (
                  <span className="text-xs text-ink-400">Logo</span>
                )}
              </div>
              <label className="text-sm font-semibold text-accent-700">
                Upload logo
                <input
                  type="file"
                  accept="image/*"
                  className="sr-only"
                  onChange={(event) => void onLogo(event.target.files?.[0])}
                />
              </label>
            </div>
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-ink-700">Organization name</span>
              <Input value={name} onChange={(event) => setName(event.target.value)} required />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-ink-700">Industry</span>
              <Select value={industry} onChange={(event) => setIndustry(event.target.value)}>
                {INDUSTRIES.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </Select>
            </label>
            <Button disabled={busy || !name.trim()} onClick={() => void saveProfile()}>
              {busy ? "Saving…" : "Continue"}
            </Button>
          </section>
        ) : null}

        {step === 1 && mode === "choose" ? (
          <section className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-ink-500">
                Choose a sample. Seeding loads sample/demo data: that template&apos;s system, agent,
                model, MCP server, tools, and policies.
              </p>
              <Button variant="secondary" onClick={() => setMode("manual")}>
                Manual input
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {templates.map((template) => (
                <button
                  key={template.key}
                  type="button"
                  disabled={busy}
                  onClick={() => void seedTemplate(template.key)}
                  className={cn(
                    "rounded-lg border p-4 text-left",
                    selectedTemplate === template.key
                      ? "border-accent-600 bg-accent-50"
                      : "border-base-200 bg-base-50",
                  )}
                >
                  <div className="text-sm font-semibold text-ink-900">{template.name}</div>
                  <p className="mt-1 text-xs text-ink-500">{template.description}</p>
                  <p className="mt-2 text-[11px] text-ink-400">
                    {template.model_name} · {(template.tools || []).join(", ")}
                  </p>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        {step === 1 && mode === "manual" ? (
          <section className="max-w-2xl space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">First AI system</h2>
              <Button variant="ghost" size="sm" onClick={() => setMode("choose")}>
                Use an example
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-ink-700">AI system</span>
                <Input value={systemName} onChange={(event) => setSystemName(event.target.value)} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-ink-700">Agent</span>
                <Input value={agentName} onChange={(event) => setAgentName(event.target.value)} />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-ink-700">Model</span>
                <Input
                  value={modelName}
                  placeholder="gpt-4.1"
                  onChange={(event) => setModelName(event.target.value)}
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-ink-700">Version tools</span>
                <Input
                  value={tools}
                  placeholder="create_ticket, lookup_order"
                  onChange={(event) => setTools(event.target.value)}
                />
              </label>
            </div>
            <CatalogUpload />
            {draft.versionId ? (
              <div
                className="space-y-2 rounded-md border border-accent-200 bg-accent-50 p-3 text-sm"
                data-testid="draft-created"
              >
                <p className="font-medium text-accent-700">
                  Draft created — review required before runtime setup
                </p>
                <p className="text-xs text-ink-600">
                  {draft.systemName ?? "The AI system"}, its agent and version were saved as drafts.
                  Nothing was approved on your behalf.
                </p>
                <div className="flex flex-wrap gap-2">
                  <Link href={`/governance/registry/versions/${draft.versionId}`}>
                    <Button variant="secondary" size="sm">
                      Review the version
                    </Button>
                  </Link>
                  <Button size="sm" onClick={() => setStep(2)}>
                    Continue
                  </Button>
                </div>
              </div>
            ) : (
              <Button disabled={busy} onClick={() => void saveManual()}>
                {busy
                  ? "Creating…"
                  : draft.systemId
                    ? "Retry — resume with existing records"
                    : "Create drafts"}
              </Button>
            )}
          </section>
        ) : null}

        {step === 2 ? (
          <section className="space-y-4">
            <ol className="list-decimal space-y-1 pl-5 text-sm text-ink-600">
              <li>Review the agent version. A draft version cannot be bound to a runtime.</li>
              <li>
                Once the version is approved, create its runtime identity. Runtime creation is
                blocked until that approval has happened.
              </li>
              <li>Explicitly issue a credential. The key is shown once, when you issue it.</li>
            </ol>
            <p className="text-sm text-ink-500">
              The examples below show the SDK call sequence: authenticate with the runtime
              credential, send a heartbeat, ask for a decision, and execute only when the decision
              is allow.
            </p>
            <div className="grid gap-4 lg:grid-cols-2">
              <ExamplePane
                title="TypeScript"
                code={examples.typescript}
                copied={copied === "ts"}
                onCopy={() => void copy("ts", examples.typescript)}
              />
              <ExamplePane
                title="Python"
                code={examples.python}
                copied={copied === "py"}
                onCopy={() => void copy("py", examples.python)}
              />
            </div>
            <Button disabled={busy} onClick={() => void finish()}>
              {busy ? "Finishing…" : "Finish setup"}
            </Button>
          </section>
        ) : null}
      </div>
    </div>
  );
}

function ExamplePane({
  title,
  code,
  copied,
  onCopy,
}: {
  title: string;
  code: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-base-200 bg-base-50">
      <div className="flex items-center justify-between border-b border-base-200 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">{title}</span>
        <Button type="button" variant="ghost" size="sm" onClick={onCopy}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-[11px] leading-relaxed text-ink-700">
        {code}
      </pre>
    </div>
  );
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("Unable to read the logo"));
    reader.readAsDataURL(file);
  });
}
