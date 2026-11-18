import { describe, expect, it } from "vitest";
import {
  NOT_AVAILABLE,
  changePercent,
  changeTone,
  escapeCsvCell,
  formatChange,
  formatCount,
  formatMoney,
  formatPercent,
  formatRatio,
  ratioPercent,
  trendSense,
} from "@/lib/reports/format";

describe("zero versus unknown", () => {
  it("renders a real zero as 0 and a missing value as not available", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(null)).toBe(NOT_AVAILABLE);
    expect(formatCount(undefined)).toBe(NOT_AVAILABLE);
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatPercent(null)).toBe(NOT_AVAILABLE);
    expect(formatPercent(Number.NaN)).toBe(NOT_AVAILABLE);
  });
});

describe("ratios", () => {
  it("returns null without a defensible denominator, never 0% or NaN", () => {
    expect(ratioPercent(3, 0)).toBeNull();
    expect(ratioPercent(0, 0)).toBeNull();
    expect(ratioPercent(null, 10)).toBeNull();
    expect(ratioPercent(1, -5)).toBeNull();
  });

  it("computes and formats percentages", () => {
    expect(ratioPercent(1, 4)).toBe(25);
    expect(ratioPercent(0, 4)).toBe(0);
    expect(formatRatio(12, 40)).toBe("12 of 40 (30.0%)");
    expect(formatRatio(0, 0)).toBe("0 of 0");
    expect(formatRatio(null, 5)).toBe(NOT_AVAILABLE);
  });
});

describe("trend direction", () => {
  it("has no change when the baseline is missing or zero", () => {
    expect(changePercent(5, 0)).toBeNull();
    expect(changePercent(5, null)).toBeNull();
    expect(trendSense(null)).toBe("unknown");
    expect(formatChange(null)).toBe("No comparison");
  });

  it("treats small movement as stable (same ±5% band as the backend)", () => {
    expect(trendSense(4.9)).toBe("flat");
    expect(trendSense(-4.9)).toBe("flat");
    expect(trendSense(5)).toBe("increase");
    expect(trendSense(-5)).toBe("decrease");
  });

  it("computes relative change", () => {
    expect(changePercent(120, 100)).toBe(20);
    expect(changePercent(80, 100)).toBe(-20);
  });

  it("gives direction a good/bad meaning only for metrics that have a polarity", () => {
    expect(changeTone("increase", "higher-is-worse")).toBe("bad");
    expect(changeTone("decrease", "higher-is-worse")).toBe("good");
    expect(changeTone("increase", "higher-is-better")).toBe("good");
    // Activity volume and intervention/deny rates are context-dependent.
    expect(changeTone("increase", "neutral")).toBe("neutral");
    expect(changeTone("decrease", "neutral")).toBe("neutral");
    expect(changeTone("unknown", "higher-is-worse")).toBe("neutral");
    expect(changeTone("flat", "higher-is-worse")).toBe("neutral");
  });
});

describe("money and cost state", () => {
  it("never renders unavailable or missing cost as zero", () => {
    expect(formatMoney({ amount: 0, costState: "UNAVAILABLE", currency: "USD" })).toBe(
      "Unavailable",
    );
    expect(formatMoney({ amount: null, currency: "USD" })).toBe("Unavailable");
    expect(formatMoney({ amount: undefined })).toBe("Unavailable");
  });

  it("keeps a genuine zero distinct from unknown", () => {
    expect(formatMoney({ amount: 0, costState: "METERED", currency: "USD" })).toBe("0.00 USD");
  });

  it("marks estimates and takes currency from data instead of hard-coding $", () => {
    expect(formatMoney({ amount: 12.5, costState: "ESTIMATED", currency: "EUR" })).toBe(
      "≈ 12.50 EUR",
    );
    expect(formatMoney({ amount: 1234.5, costState: "BILLED", currency: "usd" })).toBe(
      "1,234.50 USD",
    );
    expect(formatMoney({ amount: 3, costState: "METERED" })).not.toContain("$");
  });

  it("keeps sub-cent precision instead of rounding LLM costs to zero", () => {
    expect(formatMoney({ amount: 0.0000465, costState: "METERED", currency: "USD" })).toContain(
      "0.0000465",
    );
  });
});

describe("csv escaping", () => {
  it("quotes delimiters, quotes and newlines", () => {
    expect(escapeCsvCell('a,"b"')).toBe('"a,""b"""');
    expect(escapeCsvCell("line1\nline2")).toBe('"line1\nline2"');
    expect(escapeCsvCell(null)).toBe("");
    expect(escapeCsvCell(0)).toBe("0");
  });

  it("defuses spreadsheet formula injection", () => {
    expect(escapeCsvCell('=HYPERLINK("http://x")')).toBe('"\'=HYPERLINK(""http://x"")"');
    expect(escapeCsvCell("+1")).toBe("'+1");
    expect(escapeCsvCell("@SUM(A1)")).toBe("'@SUM(A1)");
    expect(escapeCsvCell("-2")).toBe("'-2");
  });
});
