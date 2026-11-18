"use client";

import type { ReactNode } from "react";
import { Panel } from "@/components/registry/panel";
import { Button } from "@/components/ui/button";
import type { LoadFailure } from "@/lib/reports/client";
import type { ReportQuery } from "@/lib/reports/use-report-data";

/**
 * The four non-data conditions are different facts and look different:
 *  - empty:          the query succeeded and there are no records
 *  - not configured: the capability has no source data yet (setup needed)
 *  - not authorized: the caller's role cannot read this report
 *  - failed:         the request failed (retryable)
 */
export function NoRecords({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm text-ink-500" data-state="empty">
      {children}
    </p>
  );
}

export function NotConfigured({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div
      className="rounded-md border border-dashed border-base-300 bg-base-100 p-4"
      data-state="not-configured"
    >
      <p className="text-sm font-semibold text-ink-900">{title}</p>
      <div className="mt-1 text-sm text-ink-600">{children}</div>
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

export function FailureMessage({
  failure,
  onRetry,
}: {
  failure: LoadFailure;
  onRetry?: () => void;
}) {
  const heading =
    failure.kind === "not_authorized"
      ? "Not authorized"
      : failure.kind === "invalid"
        ? "These filters can't be applied here"
        : "This report failed to load";
  return (
    <div
      role={failure.kind === "failed" ? "alert" : "status"}
      className="rounded-md border border-base-200 bg-base-100 p-4"
      data-state={failure.kind}
    >
      <p className="text-sm font-semibold text-ink-900">{heading}</p>
      <p className="mt-1 text-sm text-ink-600">{failure.message}</p>
      {failure.traceId && <p className="mt-1 text-xs text-ink-500">Trace ID: {failure.traceId}</p>}
      {onRetry && failure.kind !== "not_authorized" && (
        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-2" aria-hidden="true">
      <div className="h-4 w-1/3 animate-pulse rounded bg-base-200" />
      <div className="h-24 w-full animate-pulse rounded bg-base-200" />
    </div>
  );
}

/**
 * A panel backed by one independent request. A failure here never affects any
 * other panel; previously loaded data stays visible with a warning if only a
 * refresh failed.
 */
export function QueryPanel<T>({
  title,
  description,
  query,
  children,
  isEmpty,
  emptyMessage = "No records in this period.",
  actions,
  className,
  testId,
}: {
  title: string;
  description?: string;
  query: ReportQuery<T>;
  children: (data: T) => ReactNode;
  isEmpty?: (data: T) => boolean;
  emptyMessage?: ReactNode;
  actions?: ReactNode;
  className?: string;
  testId?: string;
}) {
  let body: ReactNode;
  if (query.data === null && (query.status === "loading" || query.status === "idle")) {
    body = (
      <div aria-busy="true" aria-live="polite">
        <span className="sr-only">Loading {title}</span>
        <Skeleton />
      </div>
    );
  } else if (query.status === "error" && query.data === null) {
    body = <FailureMessage failure={query.error} onRetry={query.refetch} />;
  } else if (query.data !== null) {
    body = (
      <>
        {query.status === "error" && (
          <div className="mb-3">
            <FailureMessage failure={query.error} onRetry={query.refetch} />
            <p className="mt-2 text-xs text-ink-500">Showing the last data that loaded.</p>
          </div>
        )}
        {query.status === "loading" && <p className="mb-2 text-xs text-ink-500">Refreshing…</p>}
        {isEmpty?.(query.data) ? <NoRecords>{emptyMessage}</NoRecords> : children(query.data)}
      </>
    );
  } else {
    body = null;
  }

  return (
    <Panel
      title={title}
      description={description}
      actions={actions}
      className={className}
      data-testid={testId}
    >
      {body}
    </Panel>
  );
}
