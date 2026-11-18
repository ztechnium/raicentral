"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  FILTER_LABELS,
  PERIOD_PRESETS,
  TEXT_FILTERS,
  activeFilters,
  validateCustomRange,
  type FilterKey,
  type FinOpsFilterState,
  type PeriodKey,
} from "@/lib/finops-filters";

const selectClass =
  "h-9 rounded-md border border-base-200 bg-base-100 px-3 text-sm text-ink-900 focus:border-accent-600 focus:outline-none focus:ring-1 focus:ring-accent-600";

const COST_STATE_OPTIONS = [
  ["BILLED", "Billed"],
  ["METERED", "Metered"],
  ["ESTIMATED", "Estimated"],
  ["UNAVAILABLE", "Unavailable (no price)"],
] as const;

/**
 * Period + the filters the backend supports. Every control here maps to a real query
 * parameter; unsupported dimensions are deliberately absent rather than shown inert.
 */
export function FilterBar({
  filters,
  errors,
  currencies = [],
  onChange,
  onRefresh,
}: {
  filters: FinOpsFilterState;
  errors: string[];
  /** Currencies present in the loaded data; the currency control appears only if >1. */
  currencies?: string[];
  onChange: (next: FinOpsFilterState) => void;
  onRefresh: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [from, setFrom] = useState(filters.from ?? "");
  const [to, setTo] = useState(filters.to ?? "");
  const [customError, setCustomError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(
      Object.fromEntries(TEXT_FILTERS.map((key) => [key, filters[key] ?? ""])) as Record<
        string,
        string
      >,
    );
    setFrom(filters.from ?? "");
    setTo(filters.to ?? "");
  }, [filters]);

  const applyText = (event: FormEvent) => {
    event.preventDefault();
    const next: FinOpsFilterState = { ...filters };
    for (const key of TEXT_FILTERS) {
      const value = (draft[key] ?? "").trim();
      if (value) next[key] = value;
      else delete next[key];
    }
    onChange(next);
  };

  const [customPending, setCustomPending] = useState(false);
  const showCustom = filters.period === "custom" || customPending;

  const changePeriod = (value: string) => {
    setCustomError(null);
    if (value === "custom") {
      // Keep the current range until a valid custom range is applied.
      if (!filters.from || !filters.to) {
        setFrom("");
        setTo("");
        setCustomPending(true);
        return;
      }
      onChange({ ...filters, period: "custom" as PeriodKey });
      return;
    }
    setCustomPending(false);
    const next = { ...filters, period: value as PeriodKey };
    delete next.from;
    delete next.to;
    onChange(next);
  };

  const applyCustom =(event: FormEvent) => {
    event.preventDefault();
    const problem = validateCustomRange(from, to);
    setCustomError(problem);
    if (problem) return;
    setCustomPending(false);
    onChange({ ...filters, period: "custom", from, to });
  };

  const clear = (key: FilterKey) => {
    const next = { ...filters };
    delete next[key];
    onChange(next);
  };

  const chips = activeFilters(filters);

  return (
    <section aria-label="FinOps filters" className="space-y-3" data-testid="finops-filters">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs font-medium text-ink-600">
          Period
          <select
            className={selectClass}
            value={showCustom ? "custom" : filters.period}
            onChange={(event) => changePeriod(event.target.value)}
          >
            {Object.entries(PERIOD_PRESETS).map(([id, preset]) => (
              <option key={id} value={id}>
                {preset.label}
              </option>
            ))}
            <option value="custom">Custom range…</option>
          </select>
        </label>

        {showCustom ? (
          <form className="flex flex-wrap items-end gap-2" onSubmit={applyCustom}>
            <label className="flex flex-col gap-1 text-xs font-medium text-ink-600">
              From
              <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-ink-600">
              To (inclusive)
              <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
            <Button type="submit" size="sm" variant="outline">
              Apply range
            </Button>
          </form>
        ) : null}

        <label className="flex flex-col gap-1 text-xs font-medium text-ink-600">
          Cost state
          <select
            className={selectClass}
            value={filters.cost_state ?? ""}
            onChange={(event) => {
              const next = { ...filters };
              if (event.target.value) next.cost_state = event.target.value;
              else delete next.cost_state;
              onChange(next);
            }}
          >
            <option value="">All states</option>
            {COST_STATE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>

        {currencies.length > 1 || filters.currency ? (
          <label className="flex flex-col gap-1 text-xs font-medium text-ink-600">
            Currency
            <select
              className={selectClass}
              value={filters.currency ?? ""}
              onChange={(event) => {
                const next = { ...filters };
                if (event.target.value) next.currency = event.target.value;
                else delete next.currency;
                onChange(next);
              }}
            >
              <option value="">All (not summed)</option>
              {Array.from(new Set([...currencies, ...(filters.currency ? [filters.currency] : [])])).map(
                (code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ),
              )}
            </select>
          </label>
        ) : null}

        <Button type="button" size="sm" variant="ghost" onClick={onRefresh}>
          Refresh
        </Button>
      </div>

      <form className="flex flex-wrap items-end gap-2" onSubmit={applyText}>
        {TEXT_FILTERS.map((key) => (
          <label key={key} className="flex flex-col gap-1 text-xs font-medium text-ink-600">
            {FILTER_LABELS[key]}
            <Input
              value={draft[key] ?? ""}
              onChange={(event) => setDraft((prev) => ({ ...prev, [key]: event.target.value }))}
              placeholder="Any"
              className="w-40"
              maxLength={300}
            />
          </label>
        ))}
        <Button type="submit" size="sm" variant="outline">
          Apply filters
        </Button>
      </form>

      {customError ? (
        <p role="alert" className="text-sm text-danger-700">
          {customError}
        </p>
      ) : null}
      {errors.map((message) => (
        <p key={message} role="alert" className="text-sm text-warn-700">
          {message}
        </p>
      ))}

      {chips.length > 0 ? (
        <ul className="flex flex-wrap gap-2" aria-label="Active filters">
          {chips.map(([key, value]) => (
            <li key={key}>
              <button
                type="button"
                onClick={() => clear(key)}
                aria-label={`Remove ${FILTER_LABELS[key]} filter ${value}`}
                className="inline-flex max-w-xs items-center gap-1.5 rounded-md border border-base-200 bg-base-100 px-2 py-1 text-xs text-ink-700 hover:bg-base-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600"
              >
                <span className="font-semibold">{FILTER_LABELS[key]}:</span>
                <span className="truncate">{value}</span>
                <span aria-hidden>×</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
