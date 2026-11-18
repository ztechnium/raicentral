"use client";

import { useRef, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import { REPORT_VIEWS, VIEW_LABELS, type ReportView } from "@/lib/reports/filters";

export const tabId = (view: ReportView) => `report-tab-${view}`;
export const panelId = (view: ReportView) => `report-panel-${view}`;

/**
 * WAI-ARIA tablist: roving tabindex, ←/→ (wrapping), Home/End. Selecting a tab
 * changes the URL (history entry) so views are deep-linkable and the back
 * button works.
 */
export function ReportTabs({
  active,
  onChange,
}: {
  active: ReportView;
  onChange: (view: ReportView) => void;
}) {
  const refs = useRef<Partial<Record<ReportView, HTMLButtonElement | null>>>({});

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = REPORT_VIEWS.length - 1;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    const view = REPORT_VIEWS[next];
    onChange(view);
    refs.current[view]?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label="Report views"
      className="flex gap-1 overflow-x-auto border-b border-base-200"
    >
      {REPORT_VIEWS.map((view, index) => {
        const selected = view === active;
        return (
          <button
            key={view}
            ref={(node) => {
              refs.current[view] = node;
            }}
            id={tabId(view)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelId(view)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(view)}
            onKeyDown={(event) => move(event, index)}
            className={cn(
              "-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600",
              selected
                ? "border-accent-600 font-semibold text-ink-900"
                : "border-transparent text-ink-500 hover:text-ink-800",
            )}
          >
            {VIEW_LABELS[view]}
          </button>
        );
      })}
    </div>
  );
}
