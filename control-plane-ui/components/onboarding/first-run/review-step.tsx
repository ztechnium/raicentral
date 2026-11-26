"use client";

import {
  agentName,
  cleanName,
  versionName,
  type FirstRunCatalog,
  type ResourceDraft,
  type SystemDraft,
} from "@/lib/first-run";

/** Step 3. Policy authoring arrives later; for now this is the final review. */
export function ReviewStep({
  catalog,
  systems,
  resources,
}: {
  catalog: FirstRunCatalog;
  systems: SystemDraft[];
  resources: ResourceDraft[];
}) {
  const label = (resource: ResourceDraft) =>
    resource.type === "seed"
      ? (catalog.resources.find((r) => r.key === resource.key)?.name ?? resource.key)
      : cleanName(resource.name);

  return (
    <div className="space-y-4">
      <p className="rounded-lg border border-base-200 bg-base-50 p-4 text-sm text-ink-700">
        Policy setup is not part of this wizard yet. After setup, add content, tool and budget rules
        under Governance. Until then, these systems have no rules of their own.
      </p>
      {systems.map((system) => {
        const used = resources.filter((r) => !r.excludedSystems.includes(system.id));
        return (
          <section
            key={system.id}
            aria-label={cleanName(system.name)}
            className="space-y-2 rounded-xl border border-base-200 bg-base-50 p-4"
          >
            <h2 className="text-sm font-semibold text-ink-900">{cleanName(system.name)}</h2>
            <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
              <dt className="text-ink-400">Agent</dt>
              <dd className="text-ink-700">{agentName(system.name)}</dd>
              <dt className="text-ink-400">Version</dt>
              <dd className="text-ink-700">{versionName(system.name)}</dd>
              <dt className="text-ink-400">Allowed models</dt>
              <dd className="font-mono text-ink-700">
                {system.models.map((m) => m.model_name).join(", ")}
              </dd>
              <dt className="text-ink-400">Resources</dt>
              <dd className="text-ink-700">{used.map(label).join(", ") || "None"}</dd>
            </dl>
          </section>
        );
      })}
      <p className="text-xs text-ink-500">
        Creating also approves each AI system, agent and version in your name, creates a{" "}
        <strong>development</strong> runtime for it, and issues its credential. These actions are
        written to the audit log.
      </p>
    </div>
  );
}
