"use client";

import { TrendLineChart } from "@/components/reporting/trend-line-chart";
import { CHART } from "@/components/reporting/chart-colors";
import type { TimeSeriesPoint } from "@/lib/reports/types";
import { formatCount } from "@/lib/reports/format";

export function summarizeSeries(points: TimeSeriesPoint[], noun: string): string {
  if (points.length === 0) return `No ${noun} data in this period.`;
  const values = points.map((p) => p.value);
  const total = values.reduce((sum, v) => sum + v, 0);
  const peak = points.reduce((best, p) => (p.value > best.value ? p : best), points[0]);
  const day = (iso: string) => new Date(iso).toISOString().slice(0, 10);
  return (
    `${noun}: ${points.length} daily points from ${day(points[0].timestamp)} to ` +
    `${day(points[points.length - 1].timestamp)}. Total ${formatCount(total)}, ` +
    `peak ${formatCount(peak.value)} on ${day(peak.timestamp)}.`
  );
}

/**
 * Line chart with a text alternative: a one-sentence summary for screen
 * readers plus the underlying values in a collapsible table, so the chart is
 * never the only way to get the numbers.
 */
export function TrendFigure({
  points,
  noun,
  color = CHART.accent,
}: {
  points: TimeSeriesPoint[];
  noun: string;
  color?: string;
}) {
  const summary = summarizeSeries(points, noun);
  return (
    <figure aria-label={summary} className="min-w-0">
      <div role="img" aria-label={summary}>
        <TrendLineChart data={points} title={noun} color={color} />
      </div>
      <figcaption className="mt-2 text-xs text-ink-500">{summary}</figcaption>
      {points.length > 0 && (
        <details className="mt-2 text-xs text-ink-600">
          <summary className="cursor-pointer font-semibold text-accent-700">
            View data table
          </summary>
          <table className="mt-2 w-full text-left">
            <caption className="sr-only">{noun} by day</caption>
            <thead>
              <tr className="border-b border-base-200 text-ink-500">
                <th scope="col" className="py-1 pr-3 font-semibold">
                  Day (UTC)
                </th>
                <th scope="col" className="py-1 font-semibold">
                  {noun}
                </th>
              </tr>
            </thead>
            <tbody>
              {points.map((point) => (
                <tr key={point.timestamp} className="border-b border-base-100">
                  <td className="py-1 pr-3">
                    {new Date(point.timestamp).toISOString().slice(0, 10)}
                  </td>
                  <td className="py-1 tabular-nums">{formatCount(point.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </figure>
  );
}
