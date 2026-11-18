/**
 * Display helpers for reports. The recurring rule: `null`/`undefined` means
 * "unknown / not available" and must never be rendered as 0.
 */

export const NOT_AVAILABLE = "Not available";

export function isMissing(value: number | null | undefined): value is null | undefined {
  return value === null || value === undefined || Number.isNaN(value);
}

export function formatCount(value: number | null | undefined): string {
  return isMissing(value) ? NOT_AVAILABLE : value.toLocaleString("en-US");
}

/** Percent where the input is already 0-100. */
export function formatPercent(value: number | null | undefined, digits = 1): string {
  return isMissing(value) ? NOT_AVAILABLE : `${value.toFixed(digits)}%`;
}

/**
 * `numerator / denominator` as a percent. Returns null (unknown) when there is
 * no defensible denominator, instead of 0% or NaN.
 */
export function ratioPercent(
  numerator: number | null | undefined,
  denominator: number | null | undefined,
): number | null {
  if (isMissing(numerator) || isMissing(denominator) || denominator <= 0) return null;
  return (numerator / denominator) * 100;
}

/** "12 of 40 (30.0%)", or "12 of 0" when the denominator is empty. */
export function formatRatio(
  numerator: number | null | undefined,
  denominator: number | null | undefined,
): string {
  if (isMissing(numerator) || isMissing(denominator)) return NOT_AVAILABLE;
  const pct = ratioPercent(numerator, denominator);
  return pct === null
    ? `${formatCount(numerator)} of ${formatCount(denominator)}`
    : `${formatCount(numerator)} of ${formatCount(denominator)} (${pct.toFixed(1)}%)`;
}

export type TrendSense = "increase" | "decrease" | "flat" | "unknown";

/** Relative change; null when the baseline is missing or zero (undefined, not 0%). */
export function changePercent(
  current: number | null | undefined,
  previous: number | null | undefined,
): number | null {
  if (isMissing(current) || isMissing(previous) || previous === 0) return null;
  return ((current - previous) / Math.abs(previous)) * 100;
}

/** Same ±5% "stable" band as the backend (filters.get_trend_direction). */
export function trendSense(changePct: number | null | undefined): TrendSense {
  if (isMissing(changePct)) return "unknown";
  if (Math.abs(changePct) < 5) return "flat";
  return changePct > 0 ? "increase" : "decrease";
}

export function formatChange(changePct: number | null | undefined): string {
  if (isMissing(changePct)) return "No comparison";
  if (Math.abs(changePct) < 5)
    return `Stable (${changePct >= 0 ? "+" : ""}${changePct.toFixed(1)}%)`;
  return `${changePct > 0 ? "▲ +" : "▼ "}${changePct.toFixed(1)}%`;
}

/**
 * Whether a direction is good, bad or neutral depends on the metric. Counts of
 * activity (actions, evaluations) are neutral: more is not "better" or
 * "worse" by itself. Only risk/exposure-style metrics have a polarity.
 */
export type Polarity = "higher-is-worse" | "higher-is-better" | "neutral";

export function changeTone(sense: TrendSense, polarity: Polarity): "good" | "bad" | "neutral" {
  if (sense === "unknown" || sense === "flat" || polarity === "neutral") return "neutral";
  const worse = polarity === "higher-is-worse" ? sense === "increase" : sense === "decrease";
  return worse ? "bad" : "good";
}

export interface MoneyInput {
  amount: number | null | undefined;
  currency?: string | null;
  /** FinOps cost state. UNAVAILABLE/MIXED/missing never renders as a number. */
  costState?: string | null;
}

/**
 * Money with its state. Unknown is "Unavailable" (never $0), estimates carry
 * "≈", and the currency comes from the data (no hard-coded "$").
 */
export function formatMoney({ amount, currency, costState }: MoneyInput): string {
  const state = (costState ?? "").toUpperCase();
  if (isMissing(amount) || state === "UNAVAILABLE") return "Unavailable";
  const code = currency?.toUpperCase() || null;
  const digits = Math.abs(amount) > 0 && Math.abs(amount) < 0.01 ? 7 : 2;
  const number = amount.toLocaleString("en-US", {
    minimumFractionDigits: Math.min(digits, 2),
    maximumFractionDigits: digits,
  });
  const text = code ? `${number} ${code}` : number;
  return state === "ESTIMATED" ? `≈ ${text}` : text;
}

/** CSV cell with RFC 4180 quoting and spreadsheet formula-injection defence. */
export function escapeCsvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function humanize(key: string): string {
  return key
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
