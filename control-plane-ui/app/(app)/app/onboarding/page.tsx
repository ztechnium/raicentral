"use client";

import { GuidedOnboarding } from "@/components/onboarding/guided-onboarding";
import { IntakeEntry } from "@/components/onboarding/intake/intake-entry";
import { SetupGate } from "@/components/onboarding/setup-gate";

export default function OnboardingPage() {
  return (
    <SetupGate>
      {({ canManage }) => (
        <div className="space-y-6">
          <IntakeEntry />
          <GuidedOnboarding canManage={canManage} />
        </div>
      )}
    </SetupGate>
  );
}
