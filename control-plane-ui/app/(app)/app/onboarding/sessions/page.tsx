"use client";

import { SessionList } from "@/components/onboarding/intake/session-list";
import { SetupGate } from "@/components/onboarding/setup-gate";

export default function OnboardingSessionsPage() {
  return <SetupGate>{() => <SessionList />}</SetupGate>;
}
