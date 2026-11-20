"use client";

import { useParams } from "next/navigation";
import { SessionWorkspace } from "@/components/onboarding/intake/session-workspace";
import { SetupGate } from "@/components/onboarding/setup-gate";

export default function OnboardingSessionPage() {
  const params = useParams<{ sessionId: string }>();
  return <SetupGate>{() => <SessionWorkspace sessionId={params.sessionId} />}</SetupGate>;
}
