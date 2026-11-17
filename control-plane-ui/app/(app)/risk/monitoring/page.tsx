"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { SectionHeader } from "@/components/section-header";
import { Panel } from "@/components/registry/panel";
import { StatusBadge } from "@/components/registry/status-badge";
import { Table } from "@/components/ui/table";
import { getRiskMonitors } from "@/lib/api";
import type { RiskMonitor } from "@/lib/types";

function humanWindow(seconds?: number | null) {
  if (!seconds) return "";
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

/** What the monitor has measured so far. */
function observed(m: RiskMonitor): string {
  if (m.source === "RULE" && m.aggregation === "SUM") return `${m.current_sum} total`;
  if (m.source === "RULE") return `${m.current_count} matching event${m.current_count === 1 ? "" : "s"}`;
  return `${m.current_count} flagged call${m.current_count === 1 ? "" : "s"}`;
}

/** The numeric line that triggered the monitor, if there is one. */
function triggerLimit(m: RiskMonitor): string {
  if (m.source === "RULE" && m.threshold != null) {
    const unit = m.aggregation === "SUM" ? "total" : "events";
    const window = m.window_seconds ? ` in ${humanWindow(m.window_seconds)}` : "";
    return `More than ${m.threshold} ${unit}${window}`;
  }
  return "No fixed threshold (set by the policy or budget; see Reason)";
}

export default function RiskMonitoringPage() {
  const [items, setItems] = useState<RiskMonitor[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getRiskMonitors({ status: "ACTIVE", limit: 100 });
      setItems(res.data.items ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load monitors");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo(
    () =>
      items.map((m) => [
        <div key={`${m.id}-scope`}>
          <div className="text-xs text-muted-foreground">{m.scope_type}</div>
          <div className="text-xs font-medium">
            {m.scope_label ?? `${m.scope_id.slice(0, 12)}…`}
          </div>
        </div>,
        <div key={`${m.id}-reason`} className="text-xs">
          <div>{m.reason || "—"}</div>
          <div className="text-muted-foreground">
            {m.source === "RULE" ? `Rule: ${m.rule_name ?? "monitor rule"}` : "Policy / budget"}
          </div>
        </div>,
        observed(m),
        triggerLimit(m),
        m.started_at ? new Date(m.started_at).toLocaleString() : "—",
        m.expires_at ? new Date(m.expires_at).toLocaleString() : "—",
        <StatusBadge key={`${m.id}-status`} status={m.status} />,
      ]),
    [items],
  );

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Monitoring"
        subtitle="Active MARK_AND_MONITOR and threshold-driven monitoring scopes."
      />
      <Panel title="Active Monitors">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <p className="text-sm text-red-600">{error}</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No active monitors.</p>
        ) : (
          <Table
            columns={[
              "Scope",
              "Reason",
              "Observed",
              "Trigger limit",
              "Started",
              "Expires",
              "Status",
            ]}
            rows={rows}
          />
        )}
      </Panel>
    </div>
  );
}
