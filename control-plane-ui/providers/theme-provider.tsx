"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  DASHBOARD_THEME_STORAGE_KEY,
  applyDashboardThemeToDocument,
  resolveInitialDashboardTheme,
  type DashboardTheme,
} from "@/lib/theme";

type ThemeContextValue = {
  theme: DashboardTheme;
  setTheme: (theme: DashboardTheme) => void;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<DashboardTheme>(resolveInitialDashboardTheme);

  const setTheme = useCallback((next: DashboardTheme) => {
    setThemeState(next);
    window.localStorage.setItem(DASHBOARD_THEME_STORAGE_KEY, next);
    applyDashboardThemeToDocument(next);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((current) => {
      const next: DashboardTheme = current === "dark" ? "light" : "dark";
      window.localStorage.setItem(DASHBOARD_THEME_STORAGE_KEY, next);
      applyDashboardThemeToDocument(next);
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useDashboardTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useDashboardTheme must be used within ThemeProvider");
  }
  return ctx;
}