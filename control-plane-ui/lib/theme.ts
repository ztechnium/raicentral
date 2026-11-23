export type DashboardTheme = "light" | "dark";

export const DASHBOARD_THEME_STORAGE_KEY = "rai_dashboard_theme";

export const DEFAULT_DASHBOARD_THEME: DashboardTheme = "light";

export function isDashboardTheme(value: string | null): value is DashboardTheme {
  return value === "light" || value === "dark";
}

export function readDashboardTheme(): DashboardTheme {
  if (typeof window === "undefined") {
    return DEFAULT_DASHBOARD_THEME;
  }
  const stored = window.localStorage.getItem(DASHBOARD_THEME_STORAGE_KEY);
  return isDashboardTheme(stored) ? stored : DEFAULT_DASHBOARD_THEME;
}

export function applyDashboardThemeToDocument(theme: DashboardTheme): void {
  if (typeof document === "undefined") {
    return;
  }
  document.documentElement.dataset.dashboardTheme = theme;
}

export function readDashboardThemeFromDocument(): DashboardTheme | null {
  if (typeof document === "undefined") {
    return null;
  }
  const fromDom = document.documentElement.dataset.dashboardTheme ?? null;
  return isDashboardTheme(fromDom) ? fromDom : null;
}

export function resolveInitialDashboardTheme(): DashboardTheme {
  return readDashboardThemeFromDocument() ?? readDashboardTheme();
}