import { describe, expect, it } from "vitest";
import {
  DEFAULT_DASHBOARD_THEME,
  isDashboardTheme,
  readDashboardThemeFromDocument,
  resolveInitialDashboardTheme,
} from "@/lib/theme";

describe("dashboard theme", () => {
  it("accepts only light and dark", () => {
    expect(isDashboardTheme("light")).toBe(true);
    expect(isDashboardTheme("dark")).toBe(true);
    expect(isDashboardTheme(null)).toBe(false);
    expect(isDashboardTheme("system")).toBe(false);
  });

  it("defaults to light when document has no theme", () => {
    document.documentElement.removeAttribute("data-dashboard-theme");
    expect(readDashboardThemeFromDocument()).toBeNull();
    expect(resolveInitialDashboardTheme()).toBe(DEFAULT_DASHBOARD_THEME);
  });

  it("reads theme from document dataset", () => {
    document.documentElement.dataset.dashboardTheme = "dark";
    expect(readDashboardThemeFromDocument()).toBe("dark");
    document.documentElement.dataset.dashboardTheme = "light";
    expect(resolveInitialDashboardTheme()).toBe("light");
  });
});