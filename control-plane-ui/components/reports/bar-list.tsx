"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { formatCount } from "@/lib/reports/format";

export interface BarItem {
  key: string;
  label: string;
  value: number;
  /** Authorized drill-down destination for this data point. */
  href?: string | null;
  tone?: "accent" | "warn" | "danger" | "muted";
  note?: string;
}

const TONE = {
  accent: "bg-accent-600",
  warn: "bg-warn-600",
  danger: "bg-danger-600",
  muted: "bg-ink-400",
} as const;

/**
 * Ranked horizontal bars rendered as a real list. Unlike a canvas/SVG chart it
 * is keyboard focusable (each data point is a link when it has a destination),
 * readable by screen readers (the number is text), sortable, and scales down to
 * a phone width without horizontal scrolling.
 */
export function BarList({
  items,
  label,
  unit = "",
  sortable = true,
  limit,
  emptyLabel = "No data in this period.",
}: {
  items: BarItem[];
  /** Accessible name for the list. */
  label: string;
  unit?: string;
  sortable?: boolean;
  limit?: number;
  emptyLabel?: string;
}) {
  const [sortBy, setSortBy] = useState<"value" | "name">("value");
  const sorted = useMemo(() => {
    const copy = [...items];
    copy.sort((a, b) =>
      sortBy === "value"
        ? b.value - a.value || a.label.localeCompare(b.label)
        : a.label.localeCompare(b.label),
    );
    return limit ? copy.slice(0, limit) : copy;
  }, [items, sortBy, limit]);
  const max = Math.max(1, ...sorted.map((item) => item.value));

  if (items.length === 0) return <p className="text-sm text-ink-500">{emptyLabel}</p>;

  return (
    <div>
      {sortable && items.length > 1 && (
        <div className="mb-2 flex items-center justify-end gap-2 text-xs text-ink-500">
          <label htmlFor={`sort-${label}`}>Sort by</label>
          <select
            id={`sort-${label}`}
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value as "value" | "name")}
            className="rounded border border-base-200 bg-base-50 px-1.5 py-1 text-xs text-ink-700"
          >
            <option value="value">Value</option>
            <option value="name">Name</option>
          </select>
        </div>
      )}
      <ul aria-label={label} className="space-y-2">
        {sorted.map((item) => {
          const bar = (
            <div className="min-w-0">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 truncate text-ink-800" title={item.label}>
                  {item.label}
                </span>
                <span className="shrink-0 font-semibold tabular-nums text-ink-900">
                  {formatCount(item.value)}
                  {unit && <span className="ml-1 text-xs font-normal text-ink-500">{unit}</span>}
                </span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-base-200" aria-hidden="true">
                <div
                  className={cn("h-full rounded-full", TONE[item.tone ?? "accent"])}
                  style={{ width: `${Math.max(2, (item.value / max) * 100)}%` }}
                />
              </div>
              {item.note && <p className="mt-0.5 text-xs text-ink-500">{item.note}</p>}
            </div>
          );
          return (
            <li key={item.key}>
              {item.href ? (
                <Link
                  href={item.href}
                  className="block rounded-md p-1 hover:bg-base-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600"
                  aria-label={`${item.label}: ${item.value}${unit ? ` ${unit}` : ""}. Open records`}
                >
                  {bar}
                </Link>
              ) : (
                <div className="p-1">{bar}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
