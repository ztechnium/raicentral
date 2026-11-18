import { describe, expect, it } from "vitest";
import {
  DEFAULT_FILTERS,
  FILTER_SUPPORT,
  REPORT_VIEWS,
  buildReportParams,
  describePeriod,
  ignoredFilters,
  parseFilters,
  reportCacheKey,
  resolvePeriod,
  serializeFilters,
  type ReportFilterState,
} from "@/lib/reports/filters";

const NOW = new Date("2026-11-18T12:00:00.000Z");
const state = (over: Partial<ReportFilterState> = {}): ReportFilterState => ({
  ...DEFAULT_FILTERS,
  ...over,
});

describe("filter URL serialization", () => {
  it("omits defaults so shared links stay short", () => {
    expect(serializeFilters(DEFAULT_FILTERS)).toBe("");
  });

  it("round-trips every filter", () => {
    const original = state({
      view: "risk",
      period: "custom",
      from: "2026-10-01T00:00:00.000Z",
      to: "2026-10-31T23:59:59.000Z",
      compare: false,
      aiSystemId: "11111111-1111-1111-1111-111111111111",
      severity: ["CRITICAL", "HIGH"],
      category: ["PROMPT_INJECTION"],
      status: ["OPEN"],
    });
    const parsed = parseFilters(new URLSearchParams(serializeFilters(original)));
    expect(parsed).toEqual({ ...original, effect: undefined, budgetMetric: undefined });
  });

  it("falls back to safe defaults for junk input", () => {
    const parsed = parseFilters(
      new URLSearchParams("view=hacker&period=forever&compare=maybe&from=nope&to=nope"),
    );
    expect(parsed.view).toBe("executive");
    expect(parsed.period).toBe("30d");
    expect(parsed.compare).toBe(true);
  });

  it("drops a custom period with a missing or inverted range", () => {
    expect(parseFilters(new URLSearchParams("period=custom")).period).toBe("30d");
    const inverted = new URLSearchParams(
      "period=custom&from=2026-11-10T00:00:00Z&to=2026-11-01T00:00:00Z",
    );
    expect(parseFilters(inverted).period).toBe("30d");
  });

  it("accepts repeated and comma separated list params", () => {
    const parsed = parseFilters(new URLSearchParams("severity=critical,high&severity=low"));
    expect(parsed.severity).toEqual(["CRITICAL", "HIGH", "LOW"]);
  });
});

describe("period resolution", () => {
  it("resolves presets as rolling windows ending now", () => {
    const p = resolvePeriod(state({ period: "7d" }), NOW);
    expect(p.end.toISOString()).toBe("2026-11-18T12:00:00.000Z");
    expect(p.start.toISOString()).toBe("2026-11-11T12:00:00.000Z");
  });

  it("previous period has equal length and never overlaps the current one", () => {
    for (const preset of ["24h", "7d", "30d", "90d"] as const) {
      const p = resolvePeriod(state({ period: preset }), NOW);
      const current = p.end.getTime() - p.start.getTime();
      const previous = p.previous.end.getTime() - p.previous.start.getTime();
      expect(previous).toBe(current);
      // Inclusive bounds: previous ends strictly before current starts.
      expect(p.previous.end.getTime()).toBeLessThan(p.start.getTime());
      expect(p.start.getTime() - p.previous.end.getTime()).toBe(1);
    }
  });

  it("uses the exact custom instants", () => {
    const p = resolvePeriod(
      state({ period: "custom", from: "2026-10-01T00:00:00.000Z", to: "2026-10-08T00:00:00.000Z" }),
      NOW,
    );
    expect(p.start.toISOString()).toBe("2026-10-01T00:00:00.000Z");
    expect(p.end.toISOString()).toBe("2026-10-08T00:00:00.000Z");
  });

  it("names the timezone and range explicitly", () => {
    const p = resolvePeriod(state({ period: "24h" }), NOW);
    const text = describePeriod(p, "UTC");
    expect(text).toContain("17 Nov 2026");
    expect(text).toContain("18 Nov 2026");
    expect(text).toContain("UTC");
  });

  it("does not drift across a timezone offset", () => {
    const p = resolvePeriod(state({ period: "24h" }), NOW);
    // ISO strings sent to the API are always UTC regardless of display zone.
    expect(buildReportParams(state({ period: "24h" }), p, "executive").period_end).toBe(
      "2026-11-18T12:00:00.000Z",
    );
    expect(describePeriod(p, "Asia/Riyadh")).toContain("15:00"); // 12:00Z = 15:00 +03
  });
});

describe("filter support matrix", () => {
  it("only sends filters the view applies (no silently ignored filters)", () => {
    const s = state({
      severity: ["CRITICAL"],
      category: ["X"],
      status: ["OPEN"],
      effect: "DENY",
      budgetMetric: "COST",
      aiSystemId: "sys-1",
    });
    const p = resolvePeriod(s, NOW);
    const keys = (view: (typeof REPORT_VIEWS)[number]) =>
      Object.keys(buildReportParams(s, p, view)).sort();

    expect(keys("risk")).toEqual(
      [
        "ai_system_id",
        "finding_status",
        "period_end",
        "period_start",
        "risk_category",
        "risk_severity",
      ].sort(),
    );
    expect(keys("controls")).toEqual(["ai_system_id", "effect", "period_end", "period_start"]);
    expect(keys("runtime")).toEqual(["ai_system_id", "period_end", "period_start"]);
    expect(keys("executive")).toEqual(["ai_system_id", "period_end", "period_start"]);
    expect(keys("assurance")).toEqual(["ai_system_id", "period_end", "period_start"]);
  });

  it("every view supports period and AI system so cross-tab scope is consistent", () => {
    for (const view of REPORT_VIEWS) {
      expect(FILTER_SUPPORT[view].period).toBe(true);
      expect(FILTER_SUPPORT[view].aiSystem).toBe(true);
    }
  });

  it("reports filters that are active but not applied on the current view", () => {
    const s = state({ severity: ["HIGH"], effect: "DENY" });
    expect(ignoredFilters(s, "runtime")).toEqual(["severity", "policy effect"]);
    expect(ignoredFilters(s, "risk")).toEqual(["policy effect"]);
    expect(ignoredFilters(s, "controls")).toEqual(["severity"]);
  });
});

describe("cache keys", () => {
  const p = resolvePeriod(state(), NOW);

  it("is identical for identical effective scope regardless of list order", () => {
    const a = buildReportParams(state({ severity: ["HIGH", "CRITICAL"] }), p, "risk");
    const b = buildReportParams(state({ severity: ["CRITICAL", "HIGH"] }), p, "risk");
    expect(reportCacheKey("/v1/reports/risk", a)).toBe(reportCacheKey("/v1/reports/risk", b));
  });

  it("differs when any scoping input changes", () => {
    const base = reportCacheKey("/v1/reports/risk", buildReportParams(state(), p, "risk"));
    const withSystem = reportCacheKey(
      "/v1/reports/risk",
      buildReportParams(state({ aiSystemId: "s" }), p, "risk"),
    );
    const otherPeriod = reportCacheKey(
      "/v1/reports/risk",
      buildReportParams(
        state({ period: "7d" }),
        resolvePeriod(state({ period: "7d" }), NOW),
        "risk",
      ),
    );
    const otherEndpoint = reportCacheKey(
      "/v1/reports/runtime",
      buildReportParams(state(), p, "risk"),
    );
    expect(new Set([base, withSystem, otherPeriod, otherEndpoint]).size).toBe(4);
  });

  it("differs per request time so Refresh misses the cache", () => {
    const later = resolvePeriod(state(), new Date(NOW.getTime() + 60_000));
    expect(reportCacheKey("/v1/reports/risk", buildReportParams(state(), p, "risk"))).not.toBe(
      reportCacheKey("/v1/reports/risk", buildReportParams(state(), later, "risk")),
    );
  });
});
