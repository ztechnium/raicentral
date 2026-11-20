"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useIntakeAccess } from "@/lib/onboarding-intake";

/** Permission-aware entry to the intake workspace. Never redirects automatically. */
export function IntakeEntry() {
  const access = useIntakeAccess();
  if (!access.canRead) return null;
  return (
    <section
      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-base-200 bg-base-50 p-4"
      data-testid="intake-entry"
    >
      <div className="space-y-1">
        <h2 className="text-sm font-semibold text-ink-900">Prepare onboarding sources</h2>
        <p className="text-sm text-ink-500">
          Save policies, inventories and a prompt, leave, and resume later.
        </p>
      </div>
      <Link href="/app/onboarding/sessions">
        <Button variant="outline">
          {access.canManage ? "Prepare onboarding sources" : "View source preparation"}
        </Button>
      </Link>
    </section>
  );
}
