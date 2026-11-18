"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART, chartTooltipStyle } from "@/components/reporting/chart-colors";
import { formatAmount } from "@/lib/finops-format";
import type { FinOpsTimeseriesPoint } from "@/lib/types";

export type SpendRow = {
  day: string;
  billed: number | null;
  metered: number | null;
  estimated: number | null;
  unpriced: number;
};

/** Per-day rows; a day with no priced spans keeps null (a gap), it is never plotted as 0. */
export function buildSpendRows(points: FinOpsTimeseriesPoint[]): SpendRow[] {
  return points
    .filter((point) => point.timestamp !== null)
    .map((point) => ({
      day: (point.timestamp as string).slice(0, 10),
      billed: point.billed_cost,
      metered: point.metered_cost,
      estimated: point.estimated_cost,
      unpriced: point.unavailable_spans,
    }));
}

export function describeSpend(rows: SpendRow[], currency: string | null): string {
  const priced = rows.filter(
    (row) => row.billed !== null || row.metered !== null || row.estimated !== null,
  );
  if (priced.length === 0) {
    return `No priced usage across ${rows.length} day${rows.length === 1 ? "" : "s"}.`;
  }
  const dayTotal = (row: SpendRow) => (row.billed ?? 0) + (row.metered ?? 0) + (row.estimated ?? 0);
  const peak = priced.reduce((a, b) => (dayTotal(b) > dayTotal(a) ? b : a));
  return `Known spend over ${rows.length} days. Peak ${formatAmount(dayTotal(peak), currency)} on ${peak.day}. ${rows.length - priced.length} days had no priced usage.`;
}

export function shorten(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

const compact = (value: number) =>
  value >= 1000 ? `${(value / 1000).toFixed(1)}k` : Number(value.toPrecision(3)).toString();

/**
 * Stacked daily spend by confidence class. Billed, metered and estimated are separate series
 * (estimated is hatched-light and labelled), and a text table is available as an alternative.
 */
export function SpendTrendChart({
  points,
  currency,
}: {
  points: FinOpsTimeseriesPoint[];
  currency: string | null;
}) {
  const rows = buildSpendRows(points);
  if (rows.length === 0) {
    return (
      <p className="flex h-[240px] items-center justify-center text-sm text-ink-500">
        No usage recorded in this period.
      </p>
    );
  }
  const summary = describeSpend(rows, currency);
  return (
    <figure aria-label="Daily known spend by cost state" className="m-0">
      <div className="h-[260px] w-full min-w-0" role="img" aria-label={summary}>
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 4 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
            <XAxis dataKey="day" tick={{ fill: CHART.tick, fontSize: 11 }} minTickGap={16} />
            <YAxis
              tick={{ fill: CHART.tick, fontSize: 11 }}
              tickFormatter={compact}
              width={48}
            />
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(value: number | string) =>
                typeof value === "number" ? formatAmount(value, currency) : value
              }
            />
            <Legend />
            <Bar dataKey="billed" name="Billed" stackId="spend" fill={CHART.accent} />
            {/* Neutral grey, not a second blue: billed and metered must stay distinguishable. */}
            <Bar dataKey="metered" name="Metered" stackId="spend" fill={CHART.ink} />
            <Bar
              dataKey="estimated"
              name="Estimated"
              stackId="spend"
              fill={CHART.warn}
              fillOpacity={0.45}
              stroke={CHART.warn}
              strokeDasharray="3 2"
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-2 text-xs text-ink-500">{summary}</figcaption>
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-accent-700">View data table</summary>
        <div className="mt-2 max-h-56 overflow-auto">
          <table className="w-full text-left">
            <caption className="sr-only">Daily spend by cost state</caption>
            <thead>
              <tr className="text-ink-500">
                <th scope="col" className="py-1 pr-3">Day</th>
                <th scope="col" className="pr-3">Billed</th>
                <th scope="col" className="pr-3">Metered</th>
                <th scope="col" className="pr-3">Estimated</th>
                <th scope="col">Unpriced calls</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.day} className="border-t border-base-200">
                  <th scope="row" className="py-1 pr-3 font-normal">{row.day}</th>
                  <td className="pr-3">{row.billed === null ? "—" : formatAmount(row.billed, currency)}</td>
                  <td className="pr-3">{row.metered === null ? "—" : formatAmount(row.metered, currency)}</td>
                  <td className="pr-3">{row.estimated === null ? "—" : formatAmount(row.estimated, currency)}</td>
                  <td>{row.unpriced}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

export type RankDatum = { name: string; value: number };

/** Horizontal ranking of known cost. Items with unknown cost are listed by the caller. */
export function SpendRankChart({
  data,
  currency,
}: {
  data: RankDatum[];
  currency: string | null;
}) {
  if (data.length === 0) {
    return (
      <p className="flex h-[200px] items-center justify-center text-sm text-ink-500">
        No priced spend to rank in this period.
      </p>
    );
  }
  const height = Math.max(180, data.length * 36 + 40);
  const summary = `Top ${data.length} by known cost: ${data
    .map((item) => `${item.name} ${formatAmount(item.value, currency)}`)
    .join("; ")}.`;
  return (
    <figure aria-label="Known cost by AI system" className="m-0">
      <div className="w-full min-w-0" style={{ height }} role="img" aria-label={summary}>
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
            <CartesianGrid stroke={CHART.grid} strokeDasharray="3 3" />
            <XAxis type="number" tick={{ fill: CHART.tick, fontSize: 11 }} tickFormatter={compact} />
            <YAxis
              type="category"
              dataKey="name"
              width={130}
              tick={{ fill: CHART.tick, fontSize: 11 }}
              // Keep the start of a long name; the full text is in the tooltip and caption.
              tickFormatter={(value: string) => shorten(value, 20)}
            />
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(value: number | string) =>
                typeof value === "number" ? formatAmount(value, currency) : value
              }
            />
            <Bar dataKey="value" name="Known cost" fill={CHART.accent} radius={[0, 4, 4, 0]} maxBarSize={22} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="sr-only">{summary}</figcaption>
    </figure>
  );
}
