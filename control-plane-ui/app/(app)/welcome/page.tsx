"use client";

import { SetupGate } from "@/components/onboarding/setup-gate";
import { WelcomeWizard } from "@/components/onboarding/welcome-wizard";

export default function WelcomePage() {
  return (
    <SetupGate requireManage fullScreen>
      {() => <WelcomeWizard />}
    </SetupGate>
  );
}
