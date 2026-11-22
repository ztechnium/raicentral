"use client";

import { AISetup } from "@/components/onboarding/ai-setup";
import { SetupGate } from "@/components/onboarding/setup-gate";

export default function AISetupPage() {
  return <SetupGate>{() => <AISetup />}</SetupGate>;
}
