"use client";

import { BreakdownExplorer } from "@/components/finops/breakdown-explorer";
import { FilterBar } from "@/components/finops/filter-bar";
import { SectionHeader } from "@/components/section-header";
import { useFinOpsFilters } from "@/lib/use-finops-filters";

export default function FinOpsUsagePage() {
  const { filters, errors, query, dataKey, params, setFilters, setParams, refresh } =
    useFinOpsFilters();
  return (
    <div className="space-y-6" data-testid="finops-usage">
      <SectionHeader
        eyebrow="FinOps"
        title="Usage & costs"
        subtitle="Group spend by any supported dimension, filter it, and drill into the trajectories behind a number. Unattributed and unpriced usage stays visible."
      />
      <FilterBar
        filters={filters}
        errors={errors}
        onChange={setFilters}
        onRefresh={refresh}
      />
      <BreakdownExplorer
        filters={filters}
        query={query}
        dataKey={dataKey}
        params={params}
        setParams={setParams}
        setFilters={setFilters}
      />
    </div>
  );
}
