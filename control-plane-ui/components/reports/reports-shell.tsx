"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { SectionHeader } from "@/components/section-header";
import { ReportsToolbar } from "@/components/reports/toolbar";
import { ReportTabs, panelId, tabId } from "@/components/reports/report-tabs";
import { SnapshotsPanel } from "@/components/reports/snapshots-panel";
import { NotConfigured } from "@/components/reports/states";
import type { ViewContext } from "@/components/reports/view-context";
import { ExecutiveView } from "@/components/reports/views/executive-view";
import { RiskView } from "@/components/reports/views/risk-view";
import { ControlsView } from "@/components/reports/views/controls-view";
import { AssuranceView } from "@/components/reports/views/assurance-view";
import { RuntimeView } from "@/components/reports/views/runtime-view";
import { EconomicsView } from "@/components/reports/views/economics-view";
import { getAISystems } from "@/lib/api";
import { usePermissions } from "@/lib/hooks/use-auth";
import {
  VIEW_LABELS,
  parseFilters,
  resolvePeriod,
  serializeFilters,
  type ReportFilterState,
  type ReportView,
} from "@/lib/reports/filters";
import { clearReportCache } from "@/lib/reports/use-report-data";

const VIEW_SUBTITLE: Record<ReportView, string> = {
  executive: "Is our AI portfolio adequately controlled, and what needs attention first?",
  risk: "Where is AI risk concentrated, and is it growing or being resolved?",
  controls: "Are our policies enforcing decisions, and what happened as a result?",
  assurance: "What evidence backs our assurance posture, and what is expiring?",
  runtime: "What is happening in production, and which interventions need follow-up?",
  economics: "Is AI spending measured, within budget and financially governed?",
};

function canRead(permissions: string[] | undefined): boolean | null {
  if (!permissions) return null; // unknown: let the backend decide
  return permissions.includes("*") || permissions.includes("reports:read");
}
function canSnapshot(permissions: string[] | undefined): boolean {
  return Boolean(
    permissions && (permissions.includes("*") || permissions.includes("reports:snapshot")),
  );
}

export function ReportsShell() {
  const router = useRouter();
  const pathname = usePathname() ?? "/reports";
  const searchParams = useSearchParams();
  const searchKey = searchParams?.toString() ?? "";
  const filters = useMemo(() => parseFilters(new URLSearchParams(searchKey)), [searchKey]);

  // The rolling window is anchored here; Refresh moves it. Keeping it stable
  // across tab switches is what lets each view's request be cached.
  const [asOf, setAsOf] = useState(() => new Date());
  const [refreshing, setRefreshing] = useState(false);
  const period = useMemo(() => resolvePeriod(filters, asOf), [filters, asOf]);

  const [systems, setSystems] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => {
    let cancelled = false;
    getAISystems()
      .then((res) => !cancelled && setSystems(res.data.map((s) => ({ id: s.id, name: s.name }))))
      .catch(() => !cancelled && setSystems([])); // filter just stays "All AI systems"
    return () => {
      cancelled = true;
    };
  }, []);
  const systemMap = useMemo(() => new Map(systems.map((s) => [s.id, s.name])), [systems]);

  const { tenant, isLoading: permissionsLoading } = usePermissions();
  const readable = canRead(tenant?.permissions);

  const update = useCallback(
    (patch: Partial<ReportFilterState>, mode: "replace" | "push" = "replace") => {
      const next = { ...filters, ...patch };
      const query = serializeFilters(next);
      const url = query ? `${pathname}?${query}` : pathname;
      if (mode === "push") router.push(url, { scroll: false });
      else router.replace(url, { scroll: false });
    },
    [filters, pathname, router],
  );

  const refresh = useCallback(() => {
    setRefreshing(true);
    clearReportCache();
    setAsOf(new Date());
    window.setTimeout(() => setRefreshing(false), 600);
  }, []);

  const ctx: ViewContext = useMemo(
    () => ({
      filters,
      period,
      systems: systemMap,
      compare: filters.compare,
      canSnapshot: canSnapshot(tenant?.permissions),
    }),
    [filters, period, systemMap, tenant?.permissions],
  );

  const view = filters.view;

  return (
    <div className="space-y-5">
      <SectionHeader
        eyebrow="Reports & Analytics"
        title={VIEW_LABELS[view]}
        subtitle={VIEW_SUBTITLE[view]}
      />

      {!permissionsLoading && readable === false ? (
        <NotConfigured title="You don't have access to reports">
          Reports need the <code className="text-xs">reports:read</code> permission, which your
          current role does not include. Ask an organization administrator if you need it. Nothing
          on this page has been loaded.
        </NotConfigured>
      ) : (
        <>
          <ReportsToolbar
            filters={filters}
            period={period}
            systems={systems}
            asOf={asOf}
            onChange={(patch) => update(patch)}
            onRefresh={refresh}
            refreshing={refreshing}
          />

          <ReportTabs active={view} onChange={(next) => update({ view: next }, "push")} />

          <div
            role="tabpanel"
            id={panelId(view)}
            aria-labelledby={tabId(view)}
            tabIndex={0}
            className="focus-visible:outline-none"
          >
            {/* Only the active view mounts, so only its requests are made. */}
            {view === "executive" && <ExecutiveView ctx={ctx} />}
            {view === "risk" && <RiskView ctx={ctx} />}
            {view === "controls" && <ControlsView ctx={ctx} />}
            {view === "assurance" && <AssuranceView ctx={ctx} />}
            {view === "runtime" && <RuntimeView ctx={ctx} />}
            {view === "economics" && <EconomicsView ctx={ctx} />}
          </div>

          <SnapshotsPanel filters={filters} period={period} canCreate={ctx.canSnapshot} />
        </>
      )}
    </div>
  );
}
