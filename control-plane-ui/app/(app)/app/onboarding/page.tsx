"use client";

import { OnboardingChat } from "@/components/onboarding/chat/onboarding-chat";
import { SetupGate } from "@/components/onboarding/setup-gate";

export default function OnboardingPage() {
  return <SetupGate>{() => <OnboardingChat />}</SetupGate>;
}
