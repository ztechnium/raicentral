export type PricingStatus = "Active" | "Scheduled" | "Superseded" | "Disabled";

interface PricingWindow {
  enabled: boolean;
  effective_from?: string | null;
  effective_to?: string | null;
}

/**
 * Lifecycle of one catalog version. Historical spans keep their own pricing snapshot, so a
 * superseded row is context, not a live rate.
 */
export function pricingStatus(row: PricingWindow, now: Date = new Date()): PricingStatus {
  if (!row.enabled) return "Disabled";
  const from = row.effective_from ? Date.parse(row.effective_from) : null;
  const to = row.effective_to ? Date.parse(row.effective_to) : null;
  if (from !== null && !Number.isNaN(from) && from > now.getTime()) return "Scheduled";
  if (to !== null && !Number.isNaN(to) && to <= now.getTime()) return "Superseded";
  return "Active";
}

/** A price nobody has checked is shown as such; verification is never inferred. */
export function verificationLabel(verifiedAt?: string | null): string {
  if (!verifiedAt) return "Not verified";
  const date = new Date(verifiedAt);
  return Number.isNaN(date.getTime()) ? "Not verified" : date.toLocaleDateString("en-GB");
}
