export type CostState =
  | "ACTUAL"
  | "ESTIMATED"
  | "METERED"
  | "BILLED"
  | "UNAVAILABLE"
  | "MIXED"
  | "NO_DATA"
  | string;

const SOURCE_LABELS: Record<string, string> = {
  PROVIDER_RESPONSE: "Provider reported",
  GATEWAY_OBSERVED: "Gateway observed",
  SIGNED_SERVICE: "Signed service",
  SDK_REPORTED: "SDK reported",
  AGENT_SUPPLIED: "Agent supplied",
  PRICING_CATALOG_DERIVED: "Pricing catalog",
  PROVIDER_BILLING_API: "Provider billing",
  INVOICE_IMPORT: "Invoice import",
  EXPLICIT_COST: "Explicit cost",
  ESTIMATED: "Estimated",
  UNKNOWN: "Unknown source",
  MIXED: "Mixed sources",
};

export function sourceLabel(source?: string | null) {
  if (!source) return "Unknown source";
  return SOURCE_LABELS[source] ?? source;
}

export function costStateLabel(state?: string | null) {
  switch (state) {
    case "BILLED":
      return "Billed";
    case "METERED":
    case "ACTUAL":
      return "Metered";
    case "ESTIMATED":
      return "Estimated";
    case "MIXED":
      return "Mixed";
    case "NO_DATA":
      return "No data";
    default:
      return "Unavailable";
  }
}

/** What each cost state means, for tooltips and legends. Never relabel one as another. */
export const COST_STATE_HELP: Record<string, string> = {
  BILLED: "Invoice- or provider-billing-backed amount.",
  METERED: "Derived from measured usage and a price in the catalog. Not an invoice.",
  ESTIMATED: "Pre-execution estimate. Not confirmed incurred spend.",
  UNAVAILABLE: "Usage was recorded but no price was available. Shown as unknown, never as zero.",
  MIXED: "Contains more than one of the states above.",
  NO_DATA: "No usage matched these filters.",
};

export function formatMoney(amount: number | null | undefined, currency = "USD") {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return null;
  // Sub-cent LLM costs must not round to $0.0000: keep 3 significant digits.
  const abs = Math.abs(amount);
  const maximumFractionDigits =
    abs >= 1 ? 2 : abs === 0 ? 2 : Math.min(10, Math.max(4, Math.ceil(-Math.log10(abs)) + 2));
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    minimumFractionDigits: abs >= 1 || abs === 0 ? 2 : 4,
    maximumFractionDigits,
  }).format(amount);
}

/**
 * Renders a cost according to its *state*, never according to whether the number is truthy.
 * A genuine zero with a known state renders as money; unknown renders as "Unavailable".
 * `currency` of null/"MIXED" means amounts of different currencies were not summed.
 */
export function formatCost(
  amount: number | null | undefined,
  state?: string | null,
  currency: string | null = "USD",
) {
  if (state === "NO_DATA") return "No data";
  // Several currencies are present: say so, rather than calling the total "Unavailable".
  if (currency === "MIXED") return "Mixed currencies";
  if (!state || state === "UNAVAILABLE" || amount === null || amount === undefined) {
    return "Unavailable";
  }
  if (!currency) return "Mixed currencies";
  const money = formatMoney(amount, currency) ?? "Unavailable";
  if (state === "ESTIMATED") return `≈ ${money}`;
  if (state === "METERED" || state === "ACTUAL") return `${money} metered`;
  if (state === "BILLED") return `${money} billed`;
  return money;
}

/** Plain amount with no state suffix, for cards that carry the state in their own label. */
export function formatAmount(amount: number | null | undefined, currency: string | null) {
  if (currency === "MIXED") return "Mixed currencies";
  if (amount === null || amount === undefined || Number.isNaN(amount)) return "Unavailable";
  if (!currency) return "Mixed currencies";
  return formatMoney(amount, currency) ?? "Unavailable";
}

export function formatLatency(ms?: number | null) {
  if (ms === null || ms === undefined || Number.isNaN(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.round(ms / 60_000);
  return `${minutes}m`;
}

export function formatTokens(value?: number | null) {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

export function formatCount(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

/** 0 → "0%". null (no denominator) → "—", never "0%". */
export function formatPct(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Number(value.toFixed(1))}%`;
}

/**
 * Period-over-period change, worded neutrally: growth in AI spend is a fact about usage,
 * not a defect, so no good/bad framing is applied here.
 */
export function formatChange(pct?: number | null) {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return "No prior-period baseline";
  if (pct === 0) return "No change vs previous period";
  const sign = pct > 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}% vs previous period`;
}
