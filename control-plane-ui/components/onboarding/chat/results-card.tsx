"use client";

import type { ChangeSetSummary } from "@/lib/onboarding-api";
import type { OnboardingResults, RuleFinding } from "@/lib/onboarding-chat";

const RULE_HEADINGS: { family: RuleFinding["family"]; label: string }[] = [
  { family: "content_rule", label: "Content rules" },
  { family: "tool_rule", label: "Tool rules" },
  { family: "runtime_budget", label: "Budgets" },
];

const RESOURCE_LABELS = {
  tool: "Tools",
  mcp_server: "MCP servers",
  external_service: "External services",
  data_source: "Data sources",
} as const;

export function ResultsCard({
  results,
  changeSet,
}: {
  results: OnboardingResults;
  changeSet: ChangeSetSummary | null;
}) {
  const empty =
    !results.systems.length &&
    !results.unattachedAgents.length &&
    !results.models.length &&
    !results.resources.length &&
    !results.rules.length;
  if (empty) return null;

  const ops = changeSet?.operations ?? [];
  const create = ops.filter((o) => o.verb === "CREATE").length;
  const reuse = ops.filter((o) => o.verb === "REUSE").length;
  const update = ops.filter((o) => o.verb === "UPDATE").length;

  return (
    <div className="space-y-4 pt-1" data-testid="onboarding-results">
      {(results.systems.length > 0 || results.unattachedAgents.length > 0) && (
        <Section title="AI systems and agents">
          <ul className="space-y-2">
            {results.systems.map((s) => (
              <li key={s.key} className="rounded-lg border border-base-200 p-2">
                <p className="font-medium text-ink-900">
                  {s.name} <Tag>{s.match}</Tag>
                </p>
                {s.description && <p className="text-xs text-ink-600">{s.description}</p>}
                {(s.owner || s.status) && (
                  <p className="text-xs text-ink-500">
                    {[s.owner && `Owner: ${s.owner}`, s.status && `Status: ${s.status}`]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                )}
                {s.agents.length > 0 && (
                  <ul className="mt-1 space-y-1 border-l border-base-200 pl-3">
                    {s.agents.map((a) => (
                      <li key={a.key} className="text-xs text-ink-800">
                        Agent: {a.name} <Tag>{a.match}</Tag>
                        {a.description && <span className="text-ink-500"> — {a.description}</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
            {results.unattachedAgents.map((a) => (
              <li key={a.key} className="rounded-lg border border-base-200 p-2 text-xs text-ink-800">
                Agent: {a.name} <Tag>{a.match}</Tag>
                <span className="text-ink-500"> — no AI system named for it</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {results.models.length > 0 && (
        <Section title="Models">
          <ul className="space-y-1 text-xs text-ink-800">
            {results.models.map((m) => (
              <li key={m.name}>
                {m.name}
                {m.provider && <span className="text-ink-500"> · {m.provider}</span>} <Tag>{m.match}</Tag>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(Object.keys(RESOURCE_LABELS) as (keyof typeof RESOURCE_LABELS)[]).map((kind) => {
        const items = results.resources.filter((r) => r.kind === kind);
        if (!items.length) return null;
        return (
          <Section key={kind} title={RESOURCE_LABELS[kind]}>
            <ul className="space-y-1 text-xs text-ink-800">
              {items.map((r) => (
                <li key={`${kind}-${r.key}`}>
                  {r.name} <Tag>{r.match}</Tag>
                </li>
              ))}
            </ul>
          </Section>
        );
      })}

      {RULE_HEADINGS.map(({ family, label }) => {
        const items = results.rules.filter((r) => r.family === family);
        if (!items.length) return null;
        return (
          <Section key={family} title={`${label} (${items.length})`}>
            <ul className="space-y-1.5 text-xs text-ink-800">
              {items.map((r) => (
                <li key={r.id}>
                  <span className="font-medium">{r.title}</span> <Tag>{r.match}</Tag>
                  <p className="text-ink-500">
                    {[
                      r.system && `System: ${r.system}`,
                      r.effect && r.effect.replace(/_/g, " ").toLowerCase(),
                      r.tool && `tool: ${r.tool}`,
                      r.budget,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "Applies to no specific system yet"}
                  </p>
                </li>
              ))}
            </ul>
          </Section>
        );
      })}

      {results.otherGovernance > 0 && (
        <p className="text-xs text-ink-500">
          Also recorded for reference: {results.otherGovernance} policy item
          {results.otherGovernance === 1 ? "" : "s"} (obligations, risks, controls).
        </p>
      )}

      {changeSet && (
        <p className="rounded-lg border border-base-200 bg-base-100 px-3 py-2 text-xs text-ink-800">
          Pending change: {create} to create · {reuse} already exist · {update} to update
          {(changeSet.skipped?.length ?? 0) > 0 &&
            ` · ${changeSet.skipped?.length} cannot be applied yet`}
          . New rules and budgets are created disabled.
          {changeSet.skipped?.map((s) => (
            <span key={s.proposal_id} className="mt-1 block text-ink-600">
              Not applied: {s.title}. {s.reason}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-500">{title}</h3>
      {children}
    </section>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="ml-1 rounded border border-base-200 bg-base-100 px-1.5 py-0.5 text-[10px] font-normal text-ink-600">
      {children}
    </span>
  );
}
