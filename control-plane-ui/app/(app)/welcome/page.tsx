"use client";

import { FirstRunWizard } from "@/components/onboarding/first-run/first-run-wizard";
import { SetupGate } from "@/components/onboarding/setup-gate";

export default function WelcomePage() {
  return (
    <SetupGate requireManage fullScreen>
      {() => <FirstRunWizard />}
    </SetupGate>
  );
}
