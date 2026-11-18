"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getApprovalRequests } from "@/lib/api";
import type { ApprovalRequestSummary } from "@/lib/types";

const POLL_MS = 30_000;

/** Bell showing pending approvals routed to the signed-in user's role. */
export function ApprovalBell() {
  const [items, setItems] = useState<ApprovalRequestSummary[]>([]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getApprovalRequests({ view: "needs_my_decision" })
        .then((rows) => {
          if (!cancelled) setItems(rows.data);
        })
        .catch(() => {
          if (!cancelled) setItems([]);
        });
    void load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={`Approvals needing your decision: ${items.length}`}
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-md p-1.5 text-ink-500 hover:bg-base-700 hover:text-ink-900"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {items.length > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white">
            {items.length > 99 ? "99+" : items.length}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 rounded-lg border border-base-200 bg-base-100 p-2 shadow-lg">
          <p className="px-2 py-1 text-xs font-medium text-ink-500">Awaiting your approval</p>
          {items.length === 0 ? (
            <p className="px-2 py-3 text-sm text-ink-500">Nothing needs your decision.</p>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {items.map((item) => (
                <li key={item.id}>
                  <Link
                    href={`/governance/approvals/${item.id}`}
                    onClick={() => setOpen(false)}
                    className="block rounded-md px-2 py-2 text-sm hover:bg-base-700"
                  >
                    <span className="block truncate font-medium text-ink-900">
                      {item.human_summary ?? item.tool_name ?? item.action_type}
                    </span>
                    <span className="block truncate text-xs text-ink-500">
                      {item.risk_tier ?? "—"} · {item.target_identifier ?? item.action_type}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <Link
            href="/governance/approvals"
            onClick={() => setOpen(false)}
            className="mt-1 block rounded-md px-2 py-1.5 text-xs text-accent-600 hover:bg-base-700"
          >
            View all approvals
          </Link>
        </div>
      )}
    </div>
  );
}
