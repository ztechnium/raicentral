"use client";

import { useEffect, useMemo, useState } from "react";
import { SectionHeader } from "@/components/section-header";
import { Panel } from "@/components/registry/panel";
import { Table } from "@/components/ui/table";
import Link from "next/link";
import { WidgetBody } from "@/components/dashboard/widget-body";
import { apiGet } from "@/lib/api";
import { useWidget } from "@/lib/dashboard/use-widget";
import { pricingStatus, verificationLabel } from "@/lib/finops-pricing";

type PricingRow = {
  id: string;
  provider: string;
  model: string;
  input_token_price: number;
  output_token_price: number;
  cached_input_token_price?: number | null;
  pricing_unit?: number | null;
  currency: string;
  effective_from?: string | null;
  effective_to?: string | null;
  enabled: boolean;
  region?: string | null;
  service_tier?: string | null;
  verified_at?: string | null;
  source_url?: string | null;
};

type CursorPlan = {
  id: string;
  name: string;
  price: number | null;
  currency?: string;
  per?: string;
  audience?: string;
  custom?: boolean;
};

type CursorModel = {
  id: string;
  display_name: string;
  vendor: string;
  pool: string;
  input_token_price: number;
  output_token_price: number;
  cached_input_token_price?: number | null;
  cache_write_token_price?: number | null;
  cursor_token_rate_exempt: boolean;
};

type CursorCatalog = {
  version: string;
  source_url: string;
  pricing_unit: number;
  cursor_token_rate_per_million: number;
  plans: CursorPlan[];
  models: CursorModel[];
};

function formatRate(value: number | null | undefined): string {
  if (value == null) return "—";
  return `$${value}`;
}

function formatPlan(plan: CursorPlan): string {
  if (plan.price == null) return `${plan.name} · custom`;
  const amount = plan.currency === "INR" ? `₹${plan.price}` : `$${plan.price}`;
  const per = plan.per ? `/${plan.per}` : "";
  const audience = plan.audience ? ` (${plan.audience})` : "";
  return `${plan.name} ${amount}${per}/mo${audience}`;
}

type UnpricedModel = { key: string | null; label: string | null; calls: number };

/** Models with usage but no price, from real metering: these need a catalog entry. */
function MissingRates() {
  const [state, retry] = useWidget("pricing-gaps", () =>
    apiGet<{ items: UnpricedModel[] }>(
      "/v1/finops/breakdown?dimension=model&cost_state=UNAVAILABLE&limit=50",
    ),
  );
  return (
    <Panel
      title="Models with usage but no price"
      description="Last 30 days. These calls are reported as unavailable, never as zero, until a rate is added."
    >
      <WidgetBody state={state} label="pricing gaps" onRetry={retry}>
        {(data) =>
          data.items.length === 0 ? (
            <p className="text-sm text-ink-500" data-testid="no-missing-rates">
              No unpriced model usage in the last 30 days.
            </p>
          ) : (
            <ul className="divide-y divide-base-200" data-testid="missing-rates">
              {data.items.map((row) => (
                <li key={row.key ?? "none"} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                  <span className="break-words font-medium text-ink-900">
                    {row.label ?? "Calls with no model name"}
                  </span>
                  <span className="text-xs text-ink-600">
                    {row.calls.toLocaleString("en-US")} unpriced call{row.calls === 1 ? "" : "s"}
                    {row.key === null ? " · cannot be priced without a model name" : ""}
                  </span>
                </li>
              ))}
            </ul>
          )
        }
      </WidgetBody>
      <p className="mt-3 text-xs text-ink-500">
        <Link
          href="/finops/usage?dimension=model&cost_state=UNAVAILABLE"
          className="font-semibold text-accent-700"
        >
          Explore unpriced usage
        </Link>
      </p>
    </Panel>
  );
}

export default function FinOpsPricingPage() {
  const [items, setItems] = useState<PricingRow[]>([]);
  const [cursorCatalog, setCursorCatalog] = useState<CursorCatalog | null>(null);
  const [cursorLoading, setCursorLoading] = useState(true);
  const [modelQuery, setModelQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    apiGet<{ items: PricingRow[] }>("/v1/finops/pricing")
      .then((catalog) => {
        if (!active) return;
        setItems(catalog.data.items ?? []);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Unable to load pricing");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    apiGet<CursorCatalog>("/v1/finops/pricing/cursor")
      .then((cursor) => {
        if (!active) return;
        setCursorCatalog(cursor.data);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Unable to load Cursor pricing");
      })
      .finally(() => {
        if (active) setCursorLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const cursorModels = useMemo(() => {
    const models = cursorCatalog?.models ?? [];
    const query = modelQuery.trim().toLowerCase();
    if (!query) return models;
    return models.filter((model) =>
      `${model.display_name} ${model.id} ${model.vendor}`.toLowerCase().includes(query),
    );
  }, [cursorCatalog, modelQuery]);

  return (
    <div className="space-y-6" data-testid="finops-pricing">
      <SectionHeader
        eyebrow="FinOps"
        title="Pricing catalog"
        subtitle="Versioned provider/model prices. New versions affect future metering only — historical spans keep their pricing snapshot."
      />
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <Panel title="Cursor published rates">
        {loading || cursorLoading ? (
          <p className="text-sm text-ink-500">Loading…</p>
        ) : cursorCatalog == null ? (
          <p className="text-sm text-ink-500">Cursor rates are unavailable.</p>
        ) : (
          <div className="space-y-4" data-testid="cursor-pricing">
            <p className="text-sm text-ink-500">
              Token rates are USD per {cursorCatalog.pricing_unit.toLocaleString()} tokens, from the{" "}
              <a
                className="underline"
                href={cursorCatalog.source_url}
                target="_blank"
                rel="noreferrer"
              >
                Cursor pricing docs
              </a>{" "}
              ({cursorCatalog.version}). Teams and Enterprise add $
              {cursorCatalog.cursor_token_rate_per_million}/M tokens on third-party models. Grok and
              Composer are exempt. Input tokens here are uncached; pass cache reads and cache writes
              separately.
            </p>
            <p className="text-sm text-ink-700" data-testid="cursor-plans">
              {cursorCatalog.plans.map(formatPlan).join(" · ")}
            </p>
            <input
              className="w-full max-w-sm rounded-md border border-base-300 bg-base-50 px-3 py-2 text-sm"
              placeholder="Filter Cursor models"
              value={modelQuery}
              onChange={(event) => setModelQuery(event.target.value)}
              aria-label="Filter Cursor models"
            />
            <Table
              columns={[
                "Model",
                "Vendor",
                "Pool",
                "Input",
                "Cache write",
                "Cache read",
                "Output",
                "Token rate",
              ]}
              rows={cursorModels.map((model) => [
                model.display_name,
                model.vendor,
                model.pool === "cursor_models" ? "Cursor models" : "Other models",
                formatRate(model.input_token_price),
                formatRate(model.cache_write_token_price),
                formatRate(model.cached_input_token_price),
                formatRate(model.output_token_price),
                model.cursor_token_rate_exempt ? "Exempt" : "Teams / Enterprise",
              ])}
              rowTestIds={cursorModels.map((model) => `cursor-model-${model.id}`)}
            />
          </div>
        )}
      </Panel>
      <MissingRates />
      <Panel
        title="Active and historical versions"
        description="Rates are per the unit shown, in the row's own currency. Superseded versions are kept for history; they do not price new usage."
      >
        {loading ? (
          <p className="text-sm text-ink-500">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-ink-500">No pricing entries yet.</p>
        ) : (
          <Table
            columns={[
              "Provider",
              "Model",
              "Status",
              "Effective from",
              "Effective to",
              "Input",
              "Output",
              "Cache read",
              "Per (tokens)",
              "Currency",
              "Region / tier",
              "Last verified",
              "Source",
            ]}
            rows={items.map((row) => [
              row.provider,
              <span key="m" className="break-words">{row.model}</span>,
              pricingStatus(row),
              row.effective_from ? new Date(row.effective_from).toLocaleDateString("en-GB") : "—",
              row.effective_to ? new Date(row.effective_to).toLocaleDateString("en-GB") : "Open-ended",
              row.input_token_price,
              row.output_token_price,
              row.cached_input_token_price ?? "—",
              (row.pricing_unit ?? 1000).toLocaleString("en-US"),
              row.currency,
              [row.region, row.service_tier].filter(Boolean).join(" / ") || "—",
              verificationLabel(row.verified_at),
              row.source_url ? (
                <a key="s" href={row.source_url} target="_blank" rel="noreferrer" className="text-accent-700 underline">
                  Source
                </a>
              ) : (
                "—"
              ),
            ])}
          />
        )}
      </Panel>
    </div>
  );
}
