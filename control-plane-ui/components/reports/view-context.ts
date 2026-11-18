"use client";

import {
  buildReportParams,
  reportCacheKey,
  type ReportFilterState,
  type ReportView,
  type ResolvedPeriod,
} from "@/lib/reports/filters";
import type { ReportParams } from "@/lib/reports/client";
import { useReportData, type ReportQuery } from "@/lib/reports/use-report-data";

export interface ViewContext {
  filters: ReportFilterState;
  period: ResolvedPeriod;
  /** AI system id -> display name, for labelling and drill-down. */
  systems: Map<string, string>;
  compare: boolean;
  canSnapshot: boolean;
}

/** Effective request params for a view, optionally for the previous period. */
export function viewParams(
  ctx: ViewContext,
  view: ReportView,
  previous = false,
  only?: readonly string[],
): ReportParams {
  const period = previous
    ? { ...ctx.period, start: ctx.period.previous.start, end: ctx.period.previous.end }
    : ctx.period;
  const params = buildReportParams(ctx.filters, period, view);
  if (!only) return params;
  return Object.fromEntries(Object.entries(params).filter(([key]) => only.includes(key)));
}

/**
 * One independent query for one panel. Params come from the view's supported
 * filter set, so every panel on a view shares the same effective scope/period.
 */
export function useViewQuery<T>(
  ctx: ViewContext,
  view: ReportView,
  endpoint: string,
  call: (params: ReportParams) => Promise<T>,
  options: { previous?: boolean; only?: readonly string[]; enabled?: boolean } = {},
): ReportQuery<T> {
  const { previous = false, only, enabled = true } = options;
  const params = viewParams(ctx, view, previous, only);
  const key = enabled ? reportCacheKey(`${endpoint}${previous ? "#prev" : ""}`, params) : null;
  return useReportData<T>(key, () => call(params));
}

/** Link to the findings workspace, carrying only filters it understands. */
export function findingsHref(
  ctx: ViewContext,
  extra: { severity?: string; status?: string; systemId?: string } = {},
): string {
  const query = new URLSearchParams();
  const system = extra.systemId ?? ctx.filters.aiSystemId;
  if (system) query.set("ai_system_id", system);
  const severity = extra.severity ?? ctx.filters.severity[0];
  if (severity) query.set("severity", severity.toUpperCase());
  if (extra.status) query.set("status", extra.status);
  const text = query.toString();
  return text ? `/risk/findings?${text}` : "/risk/findings";
}

export function systemLabel(ctx: ViewContext, id?: string | null): string {
  if (!id) return "Portfolio-wide";
  return ctx.systems.get(id) ?? `System ${id.slice(0, 8)}`;
}
