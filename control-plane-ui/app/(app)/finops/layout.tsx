import { Suspense, type ReactNode } from "react";
import { FinOpsTabs } from "@/components/finops/finops-tabs";

export default function FinOpsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="space-y-6">
      <Suspense fallback={<div className="h-10" aria-hidden />}>
        <FinOpsTabs />
      </Suspense>
      <Suspense fallback={<p className="text-sm text-ink-500">Loading…</p>}>{children}</Suspense>
    </div>
  );
}
