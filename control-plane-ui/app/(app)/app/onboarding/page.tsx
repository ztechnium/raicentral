"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { GuidedOnboarding } from "@/components/onboarding/guided-onboarding";
import { SetupGate } from "@/components/onboarding/setup-gate";
import { Button } from "@/components/ui/button";
import { useOnboardingVisibility } from "@/providers/onboarding-visibility-provider";

export default function OnboardingPage() {
  const router = useRouter();
  const visibility = useOnboardingVisibility();
  const state = visibility?.state;

  useEffect(() => {
    if (state?.status === "complete") router.replace("/dashboard");
  }, [router, state?.status]);

  if (!visibility || state?.status === "loading") {
    return <p className="text-sm text-ink-500">Checking onboarding progress…</p>;
  }
  if (state?.status === "complete") return null;

  return (
    <div className="space-y-4">
      {state?.status === "error" ? (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warn-600/30 bg-warn-50 p-3 text-sm text-warn-700"
        >
          <span>
            Could not verify whether setup is complete. Onboarding remains available. {state.message}
          </span>
          <Button variant="outline" size="sm" onClick={visibility.refresh}>
            Retry
          </Button>
        </div>
      ) : null}
      <SetupGate>{({ canManage }) => <GuidedOnboarding canManage={canManage} />}</SetupGate>
    </div>
  );
}
