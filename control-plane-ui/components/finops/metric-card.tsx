import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** A KPI tile that can carry a state badge, a caveat, and (optionally) link to its source. */
export function MetricCard({
  title,
  value,
  badge,
  detail,
  caveat,
  href,
  hrefLabel,
  tone = "neutral",
  testId,
}: {
  title: string;
  value: string;
  badge?: ReactNode;
  detail?: ReactNode;
  caveat?: ReactNode;
  href?: string;
  hrefLabel?: string;
  tone?: "neutral" | "warn" | "danger";
  testId?: string;
}) {
  const toneStyles = {
    neutral: "text-ink-900",
    warn: "text-warn-700",
    danger: "text-danger-700",
  };
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">{title}</p>
        {badge}
      </div>
      <p className={cn("mt-1 font-mono text-lg font-semibold", toneStyles[tone])}>{value}</p>
      {detail ? <div className="mt-1 text-[11px] text-ink-500">{detail}</div> : null}
      {caveat ? <div className="mt-1 text-[11px] text-warn-700">{caveat}</div> : null}
    </>
  );
  const base = "block h-full bg-base-50 px-4 py-3";
  if (href) {
    return (
      <Link
        href={href}
        aria-label={hrefLabel ? `${title}: ${hrefLabel}` : undefined}
        data-testid={testId}
        className={cn(
          base,
          "hover:bg-base-100 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent-600",
        )}
      >
        {body}
      </Link>
    );
  }
  return (
    <div className={base} data-testid={testId}>
      {body}
    </div>
  );
}
