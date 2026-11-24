"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { hasCompleteOnboardingInventory } from "@/lib/onboarding-visibility";

export type OnboardingVisibilityState =
  | { status: "loading" }
  | { status: "incomplete" }
  | { status: "complete" }
  | { status: "error"; message: string };

type OnboardingVisibilityContextValue = {
  state: OnboardingVisibilityState;
  refresh: () => void;
};

const OnboardingVisibilityContext =
  createContext<OnboardingVisibilityContextValue | null>(null);

export function OnboardingVisibilityProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [state, setState] = useState<OnboardingVisibilityState>({ status: "loading" });

  const refresh = useCallback(() => setRefreshVersion((version) => version + 1), []);

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    void hasCompleteOnboardingInventory()
      .then((complete) => {
        if (active) setState({ status: complete ? "complete" : "incomplete" });
      })
      .catch((error: unknown) => {
        if (active) {
          setState({
            status: "error",
            message:
              error instanceof Error
                ? error.message
                : "Could not verify whether onboarding is complete.",
          });
        }
      });
    return () => {
      active = false;
    };
  }, [pathname, refreshVersion]);

  return (
    <OnboardingVisibilityContext.Provider value={{ state, refresh }}>
      {children}
    </OnboardingVisibilityContext.Provider>
  );
}

export function useOnboardingVisibility() {
  return useContext(OnboardingVisibilityContext);
}
