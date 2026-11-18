import { Suspense } from "react";
import { ReportsShell } from "@/components/reports/reports-shell";

/**
 * Reports & Analytics workspace.
 *
 * Six URL-addressable views (?view=executive|risk|controls|assurance|runtime|economics)
 * share one filter toolbar; each panel loads and fails independently.
 */
export default function ReportsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-ink-500">Loading reports…</p>}>
      <ReportsShell />
    </Suspense>
  );
}
