"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { changeTone, formatChange, trendSense, type Polarity } from "@/lib/reports/format";
import type { ReportQuery } from "@/lib/reports/use-report-data";

/**
 * KPI tile. Every tile states how it is defined (so an executive can see what a
 * number means) and, where a destination exists, links to the underlying
 * records. Change vs the previous period is neutral-coloured unless the metric
 * has a real polarity (an increase in open findings is bad; an increase in
 * governed activity is just a change).
 */
export function Kpi({
  label,
  value,
  detail,
  definition,
  href,
  hrefLabel,
  changePct,
  polarity = "neutral",
  showChange = true,
  tone = "default",
  loading = false,
  error = false,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  /** Plain-language definition: numerator, denominator, period vs current state. */
  definition: string;
  href?: string;
  hrefLabel?: string;
  /** Percent change vs previous period; undefined/null renders "No comparison". */
  changePct?: number | null;
  polarity?: Polarity;
  showChange?: boolean;
  tone?: "default" | "danger" | "warn";
  loading?: boolean;
  error?: boolean;
}) {
  const descId = `kpi-${label.replace(/\W+/g, "-").toLowerCase()}-def`;
  const sense = trendSense(changePct);
  const toneClass = {
    good: "text-accent-700",
    bad: "text-danger-700",
    neutral: "text-ink-500",
  }[changeTone(sense, polarity)];

  return (
    <div
      className="rai-report-card flex min-w-0 flex-col justify-between p-4"
      aria-describedby={descId}
    >
      <div>
        <p className="rai-eyebrow">{label}</p>
        <p
          className={cn(
            "mt-2 break-words text-2xl font-bold",
            tone === "danger" && "text-danger-700",
            tone === "warn" && "text-warn-700",
            tone === "default" && "text-ink-900",
          )}
        >
          {loading ? (
            <span className="text-ink-400" aria-busy="true">
              …
            </span>
          ) : error ? (
            <span className="text-base text-ink-500">Unavailable</span>
          ) : (
            value
          )}
        </p>
        {detail && !loading && !error && <p className="mt-1 text-sm text-ink-600">{detail}</p>}
        {showChange && changePct !== undefined && !loading && !error && (
          <p className={cn("mt-1 text-xs", toneClass)}>{formatChange(changePct)}</p>
        )}
      </div>
      <div className="mt-3 space-y-1">
        <p id={descId} className="text-xs text-ink-500">
          {definition}
        </p>
        {href && (
          <Link href={href} className="rai-link text-xs">
            {hrefLabel ?? "View records"} →
          </Link>
        )}
      </div>
    </div>
  );
}

/** Convenience: a KPI whose value comes from a query that may be loading/failed. */
export function stateOf<T>(query: ReportQuery<T>) {
  return {
    loading: query.data === null && (query.status === "loading" || query.status === "idle"),
    error: query.data === null && query.status === "error",
  };
}
