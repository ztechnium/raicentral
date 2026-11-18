"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AgentNameCell } from "@/components/agent-avatar";
import { FilterToolbar, MetricStrip, PaginationBar } from "@/components/console/page-parts";
import { KpiCard } from "@/components/kpi-card";
import { SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Table } from "@/components/ui/table";
import { Panel } from "@/components/registry/panel";
import { StatusBadge } from "@/components/registry/status-badge";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  getAgents,
  getAISystems,
  getTrajectories,
  listOrganizationDepartments,
  listOrganizationTeam,
} from "@/lib/api";
import { formatCost, formatTokens, sourceLabel } from "@/lib/finops-format";
import type {
  AgentSummary,
  AISystemSummary,
  Department,
  OrganizationMembership,
  TrajectoryListResponse,
  TrajectorySummary,
} from "@/lib/types";

const METRICS = [
  { key: "tokens", label: "Tokens" },
  { key: "cost", label: "Cost" },
  { key: "completeness", label: "Completeness %" },
  { key: "findings", label: "Findings" },
  { key: "events", label: "Events" },
] as const;
type MetricKey = (typeof METRICS)[number]["key"];

const OPERATORS = [
  { value: "eq", label: "= equals" },
  { value: "ne", label: "≠ not equal" },
  { value: "gt", label: "> greater than" },
  { value: "gte", label: "≥ at least" },
  { value: "lt", label: "< less than" },
  { value: "lte", label: "≤ at most" },
];

type Condition = { id: number; metric: MetricKey; op: string; value: string };

const EMPTY_FILTERS = {
  systemId: "",
  agentId: "",
  model: "",
  departmentId: "",
  memberId: "",
  dateFrom: "",
  dateTo: "",
};

const STATUS_OPTIONS = ["", "ACTIVE", "COMPLETED", "INCOMPLETE", "FAILED"];
const PAGE_SIZE = 10;

function formatTime(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function shortId(value?: string | null) {
  if (!value) return "—";
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function formatDuration(started?: string | null, ended?: string | null) {
  if (!started || !ended) return "—";
  const ms = new Date(ended).getTime() - new Date(started).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

export default function TrajectoriesPage() {
  const router = useRouter();
  const [items, setItems] = useState<TrajectorySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [metrics, setMetrics] = useState<TrajectoryListResponse["metrics"]>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [draft, setDraft] = useState(EMPTY_FILTERS);
  const [applied, setApplied] = useState(EMPTY_FILTERS);
  const [draftConditions, setDraftConditions] = useState<Condition[]>([]);
  const [appliedConditions, setAppliedConditions] = useState<Condition[]>([]);
  const [systems, setSystems] = useState<AISystemSummary[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [members, setMembers] = useState<OrganizationMembership[]>([]);

  useEffect(() => {
    getAISystems()
      .then((res) => setSystems(res.data))
      .catch(() => {});
    listOrganizationDepartments()
      .then((res) => setDepartments(res.items))
      .catch(() => {});
    listOrganizationTeam()
      .then((res) => setMembers(res.items))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!draft.systemId) {
      setAgents([]);
      return;
    }
    getAgents(draft.systemId)
      .then((res) => setAgents(res.data))
      .catch(() => setAgents([]));
  }, [draft.systemId]);

  const memberOptions = useMemo(
    () => members.filter((m) => !draft.departmentId || m.department_id === draft.departmentId),
    [members, draft.departmentId],
  );

  const load = useCallback(
    async (pageIndex: number) => {
      setLoading(true);
      setError(null);
      try {
        const res = await getTrajectories({
          status: statusFilter || undefined,
          ai_system_id: applied.systemId || undefined,
          agent_id: applied.agentId || undefined,
          model: applied.model.trim() || undefined,
          department_id: applied.departmentId || undefined,
          delegator_id: applied.memberId || undefined,
          // date inputs are local calendar days; "to" includes the whole day
          started_after: applied.dateFrom
            ? new Date(`${applied.dateFrom}T00:00:00`).toISOString()
            : undefined,
          started_before: applied.dateTo
            ? new Date(`${applied.dateTo}T23:59:59.999`).toISOString()
            : undefined,
          ...Object.fromEntries(appliedConditions.map((c) => [c.metric, `${c.op}:${c.value}`])),
          limit: PAGE_SIZE,
          offset: pageIndex * PAGE_SIZE,
        });
        setItems(res.data.items ?? []);
        setTotal(res.data.total ?? res.data.items?.length ?? 0);
        if (res.data.metrics) setMetrics(res.data.metrics);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load trajectories");
      } finally {
        setLoading(false);
      }
    },
    [statusFilter, applied, appliedConditions],
  );

  useEffect(() => {
    void load(page);
  }, [load, page]);

  const filtered = useMemo(() => {
    if (!search.trim()) return items;
    const q = search.toLowerCase();
    return items.filter(
      (t) =>
        t.ai_system_name?.toLowerCase().includes(q) ||
        t.agent_name?.toLowerCase().includes(q) ||
        t.runtime_identity_name?.toLowerCase().includes(q) ||
        t.conversation_id?.toLowerCase().includes(q) ||
        t.client_session_id?.toLowerCase().includes(q) ||
        t.correlation_id?.toLowerCase().includes(q) ||
        t.client_type?.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q),
    );
  }, [items, search]);

  const stats = useMemo(
    () => ({
      total: metrics?.total ?? total,
      active: metrics?.active ?? 0,
      incomplete: metrics?.incomplete ?? 0,
      withFindings: metrics?.with_findings ?? 0,
    }),
    [metrics, total],
  );

  return (
    <div className="space-y-6" data-testid="trajectories-page">
      <SectionHeader
        eyebrow="Runtime"
        title="Trajectories"
        subtitle="Correlated runtime evidence across prompts, tools, policy, approval, and execution"
        actions={
          <Button variant="outline" onClick={() => void load(page)}>
            Refresh
          </Button>
        }
      />

      <MetricStrip>
        <KpiCard title="Total" value={String(stats.total)} />
        <KpiCard title="Active" value={String(stats.active)} />
        <KpiCard title="Incomplete" value={String(stats.incomplete)} tone="warn" />
        <KpiCard title="With Findings" value={String(stats.withFindings)} tone="danger" />
      </MetricStrip>

      <Panel title="Filters">
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          <Select
            aria-label="AI System"
            value={draft.systemId}
            onChange={(e) => setDraft({ ...draft, systemId: e.target.value, agentId: "" })}
          >
            <option value="">AI System: All</option>
            {systems.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Agent"
            value={draft.agentId}
            disabled={!draft.systemId}
            onChange={(e) => setDraft({ ...draft, agentId: e.target.value })}
          >
            <option value="">Agent: All</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>
          <Input
            aria-label="Model"
            placeholder="Model (e.g. gpt-4o)"
            value={draft.model}
            onChange={(e) => setDraft({ ...draft, model: e.target.value })}
          />
          <Select
            aria-label="Department"
            value={draft.departmentId}
            onChange={(e) => setDraft({ ...draft, departmentId: e.target.value, memberId: "" })}
          >
            <option value="">Department: All</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Member"
            value={draft.memberId}
            onChange={(e) => setDraft({ ...draft, memberId: e.target.value })}
          >
            <option value="">Member: All</option>
            {memberOptions.map((m) => (
              <option key={m.id} value={m.user_id}>
                {m.user_name || m.user_email || m.user_id}
              </option>
            ))}
          </Select>
          <label className="flex items-center gap-2 text-sm text-ink-500">
            From
            <Input
              type="date"
              aria-label="Date from"
              value={draft.dateFrom}
              max={draft.dateTo || undefined}
              onChange={(e) => setDraft({ ...draft, dateFrom: e.target.value })}
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-ink-500">
            To
            <Input
              type="date"
              aria-label="Date to"
              value={draft.dateTo}
              min={draft.dateFrom || undefined}
              onChange={(e) => setDraft({ ...draft, dateTo: e.target.value })}
            />
          </label>
        </div>

        <div className="mt-4 space-y-2">
          <p className="text-sm font-medium text-ink-900">Query</p>
          {draftConditions.length === 0 && (
            <p className="text-xs text-ink-400">
              Compare Tokens, Cost, Completeness, Findings or Events with a number.
            </p>
          )}
          {draftConditions.map((c) => (
            <div key={c.id} className="flex flex-wrap items-center gap-2">
              <Select
                aria-label="Metric"
                className="w-44"
                value={c.metric}
                onChange={(e) =>
                  setDraftConditions(
                    draftConditions.map((x) =>
                      x.id === c.id ? { ...x, metric: e.target.value as MetricKey } : x,
                    ),
                  )
                }
              >
                {METRICS.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </Select>
              <Select
                aria-label="Operator"
                className="w-44"
                value={c.op}
                onChange={(e) =>
                  setDraftConditions(
                    draftConditions.map((x) => (x.id === c.id ? { ...x, op: e.target.value } : x)),
                  )
                }
              >
                {OPERATORS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
              <Input
                type="number"
                aria-label="Value"
                className="w-32"
                value={c.value}
                onChange={(e) =>
                  setDraftConditions(
                    draftConditions.map((x) =>
                      x.id === c.id ? { ...x, value: e.target.value } : x,
                    ),
                  )
                }
              />
              <Button
                variant="outline"
                onClick={() => setDraftConditions(draftConditions.filter((x) => x.id !== c.id))}
              >
                Remove
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            onClick={() =>
              setDraftConditions([
                ...draftConditions,
                { id: Date.now(), metric: "tokens", op: "gt", value: "" },
              ])
            }
          >
            + Add condition
          </Button>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button
            variant="outline"
            onClick={() => {
              setDraft(EMPTY_FILTERS);
              setApplied(EMPTY_FILTERS);
              setDraftConditions([]);
              setAppliedConditions([]);
              setPage(0);
            }}
          >
            Clear
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              setApplied(draft);
              // skip rows where no valid number has been entered yet
              setAppliedConditions(
                draftConditions.filter(
                  (c) => c.value.trim() !== "" && Number.isFinite(Number(c.value)),
                ),
              );
              setPage(0);
            }}
          >
            Apply filters
          </Button>
        </div>
      </Panel>

      <Panel title="Recent Trajectories">
        <FilterToolbar
          value={search}
          onChange={(value) => {
            setSearch(value);
          }}
          placeholder="Search system, agent, session, correlation..."
          filters={[
            {
              label: "Status",
              value: statusFilter,
              onChange: (value) => {
                setStatusFilter(value);
                setPage(0);
              },
              options: STATUS_OPTIONS.filter(Boolean).map((opt) => ({ value: opt, label: opt })),
            },
          ]}
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setPage(0);
                void load(0);
              }}
            >
              Apply
            </Button>
          }
        />
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading trajectories...</p>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : filtered.length === 0 ? (
          <p className="text-sm text-muted-foreground">No trajectories yet.</p>
        ) : (
          <Table
            columns={[
              "Started",
              "AI System",
              "Agent",
              "Version",
              "Runtime Identity",
              "Client",
              "Session",
              "Events",
              "Findings",
              "Completeness",
              "Status",
              "Duration",
              "Tokens",
              "Cost",
            ]}
            rows={filtered.map((t) => [
              formatTime(t.started_at),
              <Link
                key={`${t.id}-sys`}
                href={`/governance/registry/${t.ai_system_id}`}
                className="text-primary hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {t.ai_system_name ?? shortId(t.ai_system_id)}
              </Link>,
              <AgentNameCell
                key={`${t.id}-agent`}
                name={t.agent_name}
                avatar={t.agent_avatar}
                fallback={shortId(t.agent_id)}
                size={32}
              />,
              <span key={`${t.id}-ver`} className="font-mono text-xs">
                {t.agent_version_number != null
                  ? `v${t.agent_version_number}`
                  : shortId(t.agent_version_id)}
              </span>,
              <Link
                key={`${t.id}-ri`}
                href={`/governance/runtime-identities/${t.runtime_identity_id}`}
                className="text-primary hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {t.runtime_identity_name ?? shortId(t.runtime_identity_id)}
              </Link>,
              t.client_type ?? "—",
              <span key={`${t.id}-sess`} className="font-mono text-xs">
                {t.conversation_id ??
                  t.client_session_id ??
                  (t.session_key?.startsWith("corr:")
                    ? `corr:${shortId(t.correlation_id)}`
                    : shortId(t.session_key))}
              </span>,
              String(t.event_count ?? 0),
              String(t.finding_count ?? 0),
              t.completeness_pct != null ? `${t.completeness_pct}%` : "—",
              <StatusBadge key={`${t.id}-status`} status={t.status} />,
              formatDuration(t.started_at, t.ended_at ?? t.last_event_at),
              formatTokens(t.economics?.total_tokens),
              <span key={`${t.id}-cost`} title={sourceLabel(t.economics?.metering_source)}>
                {formatCost(
                  t.economics?.total_cost,
                  t.economics?.cost_state,
                  t.economics?.currency,
                )}
              </span>,
            ])}
            rowTestIds={filtered.map((t) => `trajectory-row-${t.id}`)}
            onRowClick={(idx) => {
              const row = filtered[idx];
              if (row) router.push(`/runtime/trajectories/${row.id}`);
            }}
          />
        )}
        <PaginationBar
          total={search.trim() ? filtered.length : total}
          page={page}
          pageSize={PAGE_SIZE}
          onPage={setPage}
        />
      </Panel>
    </div>
  );
}
