"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { MetricStrip, PaginationBar } from "@/components/console/page-parts";
import { KpiCard } from "@/components/kpi-card";
import { SectionHeader } from "@/components/section-header";
import { Panel } from "@/components/registry/panel";
import { StatusBadge } from "@/components/registry/status-badge";
import { Table } from "@/components/ui/table";
import {
  getAISystems,
  getAgents,
  getModels,
  getRiskFindings,
  getRuntimeIdentities,
  listOrganizationDepartments,
  listOrganizationTeam,
} from "@/lib/api";
import type { RuntimeRiskFinding } from "@/lib/types";

const PAGE_SIZE = 10;

const POLICY_NAMES: Record<string, string> = {
  POLICY_DENIAL: "Policy denial",
  AUTHORITY_DENIAL: "Authority denial",
  APPROVAL_MISMATCH: "Approval mismatch",
  UNUSUAL_TOOL_FREQUENCY: "Unusual tool frequency",
  UNUSUAL_TOOL_COMBINATION: "Unusual tool combination",
  REPEATED_DENIED_ACTION: "Repeated denied actions",
  REPEATED_APPROVAL_REQUIRED: "Repeated approval required",
  HIGH_VALUE_BURST: "High-value burst",
  RISK_SCORE_SPIKE: "Risk score spike",
  REPEATED_MODEL_MISMATCH: "Repeated model mismatch",
  CONTENT_POLICY_VIOLATION: "Content policy",
  FREQUENCY_THRESHOLD: "Frequency threshold",
  AMOUNT_THRESHOLD: "Amount threshold",
  BUDGET_WARNING: "Budget warning",
  BUDGET_EXCEEDED: "Budget exceeded",
};

function shortId(value?: string | null) {
  if (!value) return "—";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function humanizeCode(value: string) {
  return value
    .toLowerCase()
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function explanationField(finding: RuntimeRiskFinding, key: string) {
  const value = finding.explanation?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Split a finding into the rule that fired and the action that triggered it. */
function policyAndAction(finding: RuntimeRiskFinding) {
  const policy =
    POLICY_NAMES[finding.finding_type] ?? humanizeCode(finding.finding_type || "Policy");
  const toolName = explanationField(finding, "tool_name");
  const ruleName = explanationField(finding, "rule");
  if (ruleName && toolName) return { policy: ruleName, action: toolName };
  if (toolName) return { policy, action: toolName };
  const subjectTypes = new Set([
    "CONTENT_POLICY_VIOLATION",
    "FREQUENCY_THRESHOLD",
    "AMOUNT_THRESHOLD",
  ]);
  if (subjectTypes.has(finding.finding_type) && ruleName) {
    return { policy: ruleName, action: null as string | null };
  }

  const title = finding.title?.trim() ?? "";
  const splitAt = title.indexOf(": ");
  if (splitAt > 0) {
    const subject = title.slice(splitAt + 2).trim();
    if (subjectTypes.has(finding.finding_type)) {
      return { policy: subject || policy, action: null as string | null };
    }
    if (subject) return { policy, action: subject };
  }

  return { policy, action: null as string | null };
}

type Opt = { value: string; label: string };

const FILTER_KEYS = [
  "status",
  "severity",
  "finding_type",
  "ai_system_id",
  "agent_id",
  "model_id",
  "runtime_identity_id",
  "department_id",
  "member_id",
  "date_from",
  "date_to",
] as const;
type FilterKey = (typeof FILTER_KEYS)[number];
type Filters = Partial<Record<FilterKey, string>>;

const SEVERITY_OPTIONS: Opt[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"].map((v) => ({
  value: v,
  label: humanizeCode(v),
}));
const STATUS_OPTIONS: Opt[] = ["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"].map((v) => ({
  value: v,
  label: humanizeCode(v),
}));
const POLICY_OPTIONS: Opt[] = Object.entries(POLICY_NAMES).map(([value, label]) => ({
  value,
  label,
}));

async function safe<T>(fn: () => Promise<T>): Promise<T | null> {
  try {
    return await fn();
  } catch {
    return null;
  }
}

/** Date inputs are plain dates; the end of the range is made inclusive. */
function toApiParams(f: Filters): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = { ...f };
  if (f.date_from) out.date_from = new Date(`${f.date_from}T00:00:00`).toISOString();
  if (f.date_to) out.date_to = new Date(`${f.date_to}T23:59:59.999`).toISOString();
  return out;
}

export default function RiskFindingsPage() {
  const [items, setItems] = useState<RuntimeRiskFinding[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [openCount, setOpenCount] = useState(0);
  const [highCount, setHighCount] = useState(0);
  const [systems, setSystems] = useState<Opt[]>([]);
  const [agents, setAgents] = useState<Opt[]>([]);
  const [models, setModels] = useState<Opt[]>([]);
  const [identities, setIdentities] = useState<Opt[]>([]);
  const [departments, setDepartments] = useState<Opt[]>([]);
  const [members, setMembers] = useState<Opt[]>([]);

  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() ?? "/risk/findings";

  const searchKey = searchParams?.toString() ?? "";
  const filters = useMemo<Filters>(() => {
    const params = new URLSearchParams(searchKey);
    const out: Filters = {};
    for (const key of FILTER_KEYS) {
      const v = params.get(key);
      if (v) out[key] = v;
    }
    return out;
  }, [searchKey]);
  const filterSig = JSON.stringify(filters);

  const setFilter = useCallback(
    (key: FilterKey, value: string) => {
      const next = new URLSearchParams(searchKey);
      if (value) next.set(key, value);
      else next.delete(key);
      // Agent options depend on the system, so a stale agent must not linger.
      if (key === "ai_system_id") next.delete("agent_id");
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname);
    },
    [pathname, router, searchKey],
  );
  const clearFilters = useCallback(() => router.replace(pathname), [pathname, router]);
  const activeCount = Object.keys(filters).length;

  // Back to the first page whenever any filter changes.
  useEffect(() => {
    setPage(0);
  }, [filterSig]);

  // Dropdown options. A failed list just leaves that filter empty.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [sys, mdl, ids, depts, team] = await Promise.all([
        safe(() => getAISystems()),
        safe(() => getModels()),
        safe(() => getRuntimeIdentities()),
        safe(() => listOrganizationDepartments()),
        safe(() => listOrganizationTeam({ limit: 200 })),
      ]);
      if (cancelled) return;
      setSystems((sys?.data ?? []).map((x) => ({ value: x.id, label: x.name })));
      setModels(
        (mdl?.data ?? []).map((x) => ({ value: x.id, label: `${x.provider}/${x.model_name}` })),
      );
      setIdentities((ids?.data ?? []).map((x) => ({ value: x.id, label: x.name })));
      setDepartments((depts?.data.items ?? []).map((x) => ({ value: x.id, label: x.name })));
      setMembers(
        (team?.data.items ?? []).map((x) => ({
          value: x.id,
          label: `${x.user_name || x.user_email || x.user_id} · ${humanizeCode(x.role)}`,
        })),
      );
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Agents follow the selected AI system (or every system when none is chosen).
  const systemFilter = filters.ai_system_id;
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ids = systemFilter ? [systemFilter] : systems.map((s) => s.value);
      const lists = await Promise.all(
        ids.map(async (id) => (await safe(() => getAgents(id)))?.data ?? []),
      );
      if (cancelled) return;
      setAgents(lists.flat().map((a) => ({ value: a.id, label: a.name })));
    })();
    return () => {
      cancelled = true;
    };
  }, [systemFilter, systems]);

  const load = useCallback(
    async (pageIndex: number) => {
      setLoading(true);
      try {
        const res = await getRiskFindings({
          ...toApiParams(JSON.parse(filterSig) as Filters),
          limit: PAGE_SIZE,
          offset: pageIndex * PAGE_SIZE,
        });
        setItems(res.data.items ?? []);
        setTotal(res.data.total ?? res.data.items?.length ?? 0);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load findings");
      } finally {
        setLoading(false);
      }
    },
    [filterSig],
  );

  useEffect(() => {
    void load(page);
  }, [load, page]);

  // Stat cards cover the whole filtered result set, not just the visible page.
  useEffect(() => {
    let cancelled = false;
    const base = JSON.parse(filterSig) as Filters;
    const count = async (extra: Filters) => {
      const res = await getRiskFindings({ ...toApiParams({ ...base, ...extra }), limit: 1 });
      return res.data.total ?? 0;
    };
    (async () => {
      try {
        const sev = base.severity?.toUpperCase();
        const highSeverities = sev
          ? sev === "HIGH" || sev === "CRITICAL"
            ? [sev]
            : []
          : ["HIGH", "CRITICAL"];
        const openBlocked = base.status && base.status.toUpperCase() !== "OPEN";
        const [open, highs] = await Promise.all([
          openBlocked ? Promise.resolve(0) : count({ status: "OPEN" }),
          Promise.all(highSeverities.map((severity) => count({ severity }))),
        ]);
        if (cancelled) return;
        setOpenCount(open);
        setHighCount(highs.reduce((a, b) => a + b, 0));
      } catch {
        /* cards are best-effort; the table surfaces load errors */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [filterSig]);

  const filterDefs: { key: FilterKey; label: string; options: Opt[] }[] = [
    { key: "ai_system_id", label: "AI system", options: systems },
    { key: "agent_id", label: "Agent", options: agents },
    { key: "model_id", label: "Model", options: models },
    { key: "runtime_identity_id", label: "Runtime", options: identities },
    { key: "department_id", label: "Department", options: departments },
    { key: "member_id", label: "Member", options: members },
    { key: "severity", label: "Severity", options: SEVERITY_OPTIONS },
    { key: "finding_type", label: "Policy", options: POLICY_OPTIONS },
    { key: "status", label: "Status", options: STATUS_OPTIONS },
  ];

  const rows = useMemo(
    () =>
      items.map((f) => [
        f.created_at ? new Date(f.created_at).toLocaleString() : "—",
        <PolicyActionCell key={`${f.id}-policy`} finding={f} />,
        <StatusBadge key={`${f.id}-kind`} status={f.policy_kind ?? "governance"} />,
        <StatusBadge key={`${f.id}-sev`} status={f.severity} />,
        `${(f.confidence * 100).toFixed(0)}%`,
        f.status,
        <div key={`${f.id}-affected`} className="space-y-0.5 text-xs">
          <div>{f.ai_system_name ?? shortId(f.ai_system_id)}</div>
          <div className="text-muted-foreground">
            {f.agent_name ?? shortId(f.agent_id)}
            {f.agent_version_number != null ? ` · v${f.agent_version_number}` : ""}
          </div>
          <div className="text-muted-foreground">{f.model_name ?? "—"}</div>
        </div>,
        f.trajectory_id ? (
          <Link
            key={`${f.id}-traj`}
            className="font-mono text-xs text-primary underline"
            href={`/runtime/trajectories/${f.trajectory_id}`}
          >
            {shortId(f.trajectory_id)}
          </Link>
        ) : (
          "—"
        ),
      ]),
    [items],
  );

  return (
    <div className="space-y-6">
      <SectionHeader
        eyebrow="Risk"
        title="Active Risk Findings"
        subtitle="Explainable runtime risk findings with severity, confidence, and intervention."
      />
      <MetricStrip>
        <KpiCard title="Findings" value={String(total)} />
        <KpiCard title="Open" value={String(openCount)} tone="warn" />
        <KpiCard title="Critical / High" value={String(highCount)} tone="danger" />
      </MetricStrip>
      <Panel title="Findings">
        <div className="mb-3 flex flex-wrap items-end gap-2">
          {filterDefs.map((def) => (
            <label
              key={def.key}
              className="flex min-w-36 flex-col gap-1 text-xs text-muted-foreground"
            >
              {def.label}
              <select
                aria-label={def.label}
                value={filters[def.key] ?? ""}
                onChange={(e) => setFilter(def.key, e.target.value)}
                className="h-9 rounded-md border border-base-200 bg-base-100 px-3 text-sm text-ink-700"
              >
                <option value="">All</option>
                {def.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {(["date_from", "date_to"] as const).map((key) => (
            <label key={key} className="flex flex-col gap-1 text-xs text-muted-foreground">
              {key === "date_from" ? "From" : "To"}
              <input
                type="date"
                aria-label={key === "date_from" ? "From date" : "To date"}
                value={filters[key] ?? ""}
                onChange={(e) => setFilter(key, e.target.value)}
                className="h-9 rounded-md border border-base-200 bg-base-100 px-3 text-sm text-ink-700"
              />
            </label>
          ))}
          {activeCount > 0 ? (
            <button
              type="button"
              onClick={clearFilters}
              className="h-9 rounded-md px-3 text-sm text-primary underline"
            >
              Clear filters ({activeCount})
            </button>
          ) : null}
        </div>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <p className="text-sm text-red-600">{error}</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{activeCount > 0 ? "No findings match the selected filters." : "No risk findings yet."}</p>
        ) : (
          <Table
            columns={[
              "Time",
              "Policy and action",
              "Kind",
              "Severity",
              "Confidence",
              "Status",
              "AI System / Agent / Model",
              "Trajectory",
            ]}
            rows={rows}
          />
        )}
        <PaginationBar total={total} page={page} pageSize={PAGE_SIZE} onPage={setPage} />
      </Panel>
    </div>
  );
}

function PolicyActionCell({ finding }: { finding: RuntimeRiskFinding }) {
  const { policy, action } = policyAndAction(finding);
  return (
    <div className="min-w-[220px] space-y-1.5">
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">Policy</div>
        <div className="font-medium text-ink-900">{policy}</div>
      </div>
      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">Action</div>
        <div className="text-ink-800">{action ?? "—"}</div>
      </div>
      {finding.description ? (
        <p className="text-xs text-muted-foreground">{finding.description}</p>
      ) : null}
    </div>
  );
}
