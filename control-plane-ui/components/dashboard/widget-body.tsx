"use client";

import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import type { WidgetState } from "@/lib/dashboard/use-widget";

/** Renders the shared loading / forbidden / error states; `children` renders only when ready. */
export function WidgetBody<T>({
  state,
  label,
  onRetry,
  children,
}: {
  state: WidgetState<T>;
  label: string;
  onRetry: () => void;
  children: (data: T) => ReactNode;
}) {
  if (state.status === "loading") {
    return (
      <div
        role="status"
        aria-label={`Loading ${label}`}
        className="h-24 animate-pulse rounded-md bg-base-100"
      />
    );
  }
  if (state.status === "forbidden") {
    return (
      <p className="text-sm text-ink-500">Your role does not have access to {label}.</p>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-danger-700">
        <span>Unable to load {label}.</span>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  return <>{children(state.data)}</>;
}
