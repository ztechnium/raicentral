"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { WidgetBody } from "@/components/dashboard/widget-body";
import { CostStateBadge } from "@/components/finops/cost-state-badge";
import { Panel } from "@/components/registry/panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table } from "@/components/ui/table";
import { getFinOpsBreakdown, getFinOpsTrajectories } from "@/lib/api";
import { useWidget } from "@/lib/dashboard/use-widget";
import {
  DEFAULT_DIMENSION,
  DIMENSIONS,
  PAGE_SIZE,
  SORTS,
  UNIT_DEFINITIONS,
  dimensionById,
  parsePage,
  parseSort,
  type DimensionDef,
} from "@/lib/finops-dimensions";
import { formatAmount, formatCount, formatPct, formatTokens } from "@/lib/finops-format";
import type { FinOpsFilterState } from "@/lib/finops-filters";
import type { FinOpsQuery } from "@/lib/api";
import type { FinOpsBreakdownItem } from "@/lib/types";

function TokenCell({ item }: { item: FinOpsBreakdownItem }) {
  if (item.tokens === null) {
    return <span title="No call in this row reported token usage">—</span>;
  }
  const parts = [
    item.input_tokens !== null ? `in ${formatTokens(item.input_tokens)}` : null,
    item.output_tokens !== null ? `out ${formatTokens(item.output_tokens)}` : null,
    item.cached_tokens !== null ? `cache ${formatTokens(item.cached_tokens)}` : null,
    item.reasoning_tokens !== null ? `reasoning ${formatTokens(item.reasoning_tokens)}` : null,
  ].filter(Boolean);
  return (
    <div>
      <div>{formatTokens(item.tokens)}</div>
      {parts.length ? <div className="text-[10px] text-ink-500">{parts.join(" · ")}</div> : null}
      {item.token_reported_calls < item.calls ? (
        <div className="text-[10px] text-ink-500">
          {formatCount(item.token_reported_calls)} of {formatCount(item.calls)} calls reported tokens
        </div>
      ) : null}
    </div>
  );
}

function CostCell({ item }: { item: FinOpsBreakdownItem }) {
  if (item.cost === null) {
    return (
      <div className="space-y-1">
        <div className="font-medium">{item.currency === "MIXED" ? "Mixed currencies" : "Unavailable"}</div>
        <CostStateBadge state={item.cost_state} />
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <div className="font-mono font-medium">{formatAmount(item.cost, item.currency)}</div>
      <CostStateBadge state={item.cost_state} />
      {item.unpriced_calls > 0 ? (
        <div className="text-[10px] text-warn-700">
          + {formatCount(item.unpriced_calls)} unpriced call{item.unpriced_calls === 1 ? "" : "s"}
        </div>
      ) : null}
    </div>
  );
}

/** Chooses between an explanation and a real drill-down; only the latter issues a request. */
function Drilldown({
  dimension,
  item,
  query,
}: {
  dimension: DimensionDef;
  item: FinOpsBreakdownItem;
  query: FinOpsQuery;
}) {
  if (!dimension.drillParam || !item.key) {
    return (
      <Panel title={`Details: ${item.label || "Unattributed"}`} data-testid="finops-drilldown">
        <p className="text-sm text-ink-500">
          {item.key === null
            ? "Usage with no value for this dimension cannot be narrowed further, because there is nothing to filter on. It is still counted in the totals."
            : `Trajectory drill-down is not available for ${dimension.label.toLowerCase()}.`}
        </p>
      </Panel>
    );
  }
  return <TrajectoryDrilldown scope={{ [dimension.drillParam]: item.key }} dimension={dimension} item={item} query={query} />;
}

function TrajectoryDrilldown({
  scope,
  dimension,
  item,
  query,
}: {
  scope: Record<string, string>;
  dimension: DimensionDef;
  item: FinOpsBreakdownItem;
  query: FinOpsQuery;
}) {
  const [state, retry] = useWidget(`drill:${dimension.id}:${item.key}:${JSON.stringify(query)}`, () =>
    getFinOpsTrajectories({ ...query, ...scope, limit: 10 }),
  );
  const name = item.label || "Unattributed";
  return (
    <Panel
      title={`Top trajectories: ${name}`}
      description="Highest known cost first, within the current period and filters."
      data-testid="finops-drilldown"
    >
      <WidgetBody state={state} label="trajectories" onRetry={retry}>
        {(data) =>
          data.items.length === 0 ? (
            <p className="text-sm text-ink-500">No trajectories recorded for this selection.</p>
          ) : (
            <>
              <ul className="divide-y divide-base-200">
                {data.items.map((row) => (
                  <li key={row.trajectory_id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                    <Link
                      href={`/runtime/trajectories/${row.trajectory_id}`}
                      className="font-mono text-xs text-accent-700"
                    >
                      {row.trajectory_id.slice(0, 8)}…
                    </Link>
                    <span className="text-xs text-ink-600">
                      {row.cost === null ? "No price" : formatAmount(row.cost, item.currency)} ·{" "}
                      {formatTokens(row.tokens)} tokens
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-ink-500">
                Showing {data.items.length} of {formatCount(data.total)} trajectories.
              </p>
            </>
          )
        }
      </WidgetBody>
    </Panel>
  );
}

/**
 * One filterable, sortable, paginated breakdown replacing the separate usage/model/tenant
 * tables. Group-by, sort, search and page live in the URL; filters come from the shared bar.
 */
export function BreakdownExplorer({
  filters,
  query,
  dataKey,
  params,
  setParams,
  setFilters,
}: {
  filters: FinOpsFilterState;
  query: FinOpsQuery;
  dataKey: string;
  params: URLSearchParams;
  setParams: (patch: Record<string, string | undefined>) => void;
  setFilters: (next: FinOpsFilterState) => void;
}) {
  const dimension = dimensionById(params.get("dimension") ?? DEFAULT_DIMENSION);
  const sort = parseSort(params.get("sort"));
  const order = params.get("order") === "asc" ? "asc" : "desc";
  const page = parsePage(params.get("page"));
  const urlSearch = params.get("q") ?? "";
  const search = dimension.searchable ? urlSearch : "";

  const [searchDraft, setSearchDraft] = useState(urlSearch);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => setSearchDraft(urlSearch), [urlSearch]);
  useEffect(() => setSelected(null), [dimension.id, page, dataKey, sort, order, search]);

  // Debounce typing into the URL so each keystroke is not a request.
  useEffect(() => {
    if (searchDraft === urlSearch) return;
    const timer = setTimeout(() => setParams({ q: searchDraft.trim() || undefined, page: undefined }), 350);
    return () => clearTimeout(timer);
  }, [searchDraft, urlSearch, setParams]);

  const [state, retry] = useWidget(
    `explore:${dataKey}:${dimension.id}:${sort}:${order}:${page}:${search}`,
    () =>
      getFinOpsBreakdown({
        ...query,
        dimension: dimension.id,
        sort,
        order,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        search: search || undefined,
      }),
  );

  const chosen =
    state.status === "ready" ? (state.data.items.find((item) => (item.key ?? "∅") === selected) ?? null) : null;

  return (
    <div className="space-y-4" data-testid="finops-explorer">
      <div role="group" aria-label="Group by" className="flex flex-wrap gap-2">
        {DIMENSIONS.map((dim) => (
          <button
            key={dim.id}
            type="button"
            aria-pressed={dim.id === dimension.id}
            onClick={() => setParams({ dimension: dim.id, page: undefined, q: undefined })}
            className={`rounded-md border px-3 py-1 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600 ${
              dim.id === dimension.id
                ? "border-accent-500 bg-accent-50 font-semibold"
                : "border-base-200 hover:bg-base-100"
            }`}
          >
            {dim.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        {dimension.searchable ? (
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-600">
            Search {dimension.label.toLowerCase()}
            <Input
              value={searchDraft}
              onChange={(event) => setSearchDraft(event.target.value)}
              placeholder="Contains…"
              maxLength={100}
              className="w-56"
            />
          </label>
        ) : null}
        <label className="flex flex-col gap-1 text-xs font-medium text-ink-600">
          Sort by
          <select
            value={sort}
            onChange={(event) => setParams({ sort: event.target.value, page: undefined })}
            className="h-9 rounded-md border border-base-200 bg-base-100 px-3 text-sm text-ink-900"
          >
            {SORTS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setParams({ order: order === "desc" ? "asc" : undefined, page: undefined })}
          aria-label={`Sort order: ${order === "desc" ? "highest first" : "lowest first"}. Toggle.`}
        >
          {order === "desc" ? "Highest first ↓" : "Lowest first ↑"}
        </Button>
      </div>

      <details className="rounded-lg border border-base-200 bg-base-50 p-3 text-sm">
        <summary className="cursor-pointer font-medium text-ink-800">What do these units mean?</summary>
        <dl className="mt-2 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
          {UNIT_DEFINITIONS.map(([term, definition]) => (
            <div key={term} className="contents">
              <dt className="font-semibold text-ink-800">{term}</dt>
              <dd className="text-ink-600">{definition}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-ink-500">
          Averages divide known cost by calls (or trajectories) that have a known cost, never by
          all calls. Rows with no value for a dimension are shown as “Unattributed” or “Not
          reported”, never hidden or counted as zero.
        </p>
      </details>

      <WidgetBody state={state} label="usage and costs" onRetry={retry}>
        {(data) => {
          const total = data.total;
          const from = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
          const to = Math.min(page * PAGE_SIZE, total);
          const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
          return (
            <div className="space-y-3">
              {data.currency === "MIXED" ? (
                <div role="status" className="rounded-lg border border-warn-600/30 bg-warn-100 p-3 text-sm text-warn-700">
                  <p className="font-semibold">
                    Cost columns are hidden because this data spans several currencies ({data.currencies.join(", ")}).
                  </p>
                  <p className="mt-1">Amounts are never converted or added across currencies. Choose one:</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {data.currencies.map((code) => (
                      <Button key={code} size="sm" variant="outline" onClick={() => setFilters({ ...filters, currency: code })}>
                        Show {code}
                      </Button>
                    ))}
                  </div>
                </div>
              ) : null}

              {data.items.length === 0 ? (
                <p className="rounded-lg border border-base-200 p-6 text-center text-sm text-ink-500" data-testid="explorer-empty">
                  {search || Object.keys(query).length > 2
                    ? "No usage matches these filters. Try widening the period or clearing a filter."
                    : "No usage was recorded in this period."}
                </p>
              ) : (
                <Table
                  columns={[
                    dimension.label,
                    "Known cost",
                    "Estimated",
                    "Calls",
                    "Tokens",
                    "Trajectories",
                    "Avg / priced call",
                    "No price",
                    "",
                  ]}
                  rows={data.items.map((item) => {
                    const key = item.key ?? "∅";
                    return [
                      <div key="n" className="max-w-[16rem]">
                        <div className="break-words font-medium text-ink-900">{item.label || "Unattributed"}</div>
                        {item.key === null ? (
                          <div className="text-[10px] text-ink-500">Not attributed to a value</div>
                        ) : null}
                      </div>,
                      <CostCell key="c" item={item} />,
                      item.estimated_cost === null ? "—" : `≈ ${formatAmount(item.estimated_cost, item.currency)}`,
                      <div key="k">
                        <div>{formatCount(item.calls)}</div>
                        <div className="text-[10px] text-ink-500">
                          {formatCount(item.model_calls)} model · {formatCount(item.tool_calls)} tool
                        </div>
                      </div>,
                      <TokenCell key="t" item={item} />,
                      formatCount(item.trajectories),
                      item.avg_cost_per_call === null ? "—" : formatAmount(item.avg_cost_per_call, item.currency),
                      <span key="u" className={item.unpriced_calls > 0 ? "font-semibold text-warn-700" : ""}>
                        {formatPct(item.unpriced_share_pct)}
                      </span>,
                      <Button
                        key="a"
                        size="sm"
                        variant="ghost"
                        aria-expanded={selected === key}
                        aria-label={`Details for ${item.label || "Unattributed"}`}
                        onClick={() => setSelected(selected === key ? null : key)}
                      >
                        Details
                      </Button>,
                    ];
                  })}
                />
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-ink-600">
                <p aria-live="polite">
                  {total === 0 ? "0 results" : `Showing ${formatCount(from)}–${formatCount(to)} of ${formatCount(total)}`}
                </p>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setParams({ page: page - 1 > 1 ? String(page - 1) : undefined })}>
                    Previous
                  </Button>
                  <span className="self-center text-xs">
                    Page {page} of {lastPage}
                  </span>
                  <Button size="sm" variant="outline" disabled={page >= lastPage} onClick={() => setParams({ page: String(page + 1) })}>
                    Next
                  </Button>
                </div>
              </div>

              {chosen ? (
                <div className="space-y-3">
                  <Drilldown dimension={dimension} item={chosen} query={query} />
                  <div className="flex flex-wrap gap-3 text-sm">
                    {dimension.filterKey && chosen.key ? (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          setFilters({ ...filters, [dimension.filterKey as string]: chosen.key as string })
                        }
                      >
                        Filter everything to this {dimension.singular}
                      </Button>
                    ) : null}
                    {dimension.registryPath && chosen.key ? (
                      <Link href={dimension.registryPath(chosen.key)} className="self-center font-semibold text-accent-700">
                        Open in registry
                      </Link>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          );
        }}
      </WidgetBody>
    </div>
  );
}
