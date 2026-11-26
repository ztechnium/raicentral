"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useWidget } from "@/lib/dashboard/use-widget";
import { useSetupAccess } from "@/lib/hooks/use-setup-access";
import { fetchWelcome } from "@/lib/public-api";
import { setupAction, type SetupAccess } from "@/lib/welcome-gate";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <section
      aria-label="Setup"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-base-200 bg-base-50 p-4"
      data-testid="setup-entry"
    >
      {children}
    </section>
  );
}

function EntryAction({ access }: { access: Extract<SetupAccess, "read" | "manage"> }) {
  const [welcome, retry] = useWidget("setup-welcome", () =>
    fetchWelcome().then((data) => ({ data })),
  );

  if (welcome.status === "loading") {
    return (
      <Shell>
        <p className="text-sm text-ink-500">Checking setup status…</p>
      </Shell>
    );
  }
  if (welcome.status === "forbidden") return null;
  if (welcome.status === "error") {
    // A failed fetch is unknown, never "setup incomplete".
    return (
      <Shell>
        <p className="text-sm text-ink-500">Setup status unavailable.</p>
        <Button size="sm" variant="secondary" onClick={retry}>
          Retry
        </Button>
      </Shell>
    );
  }

  const action = setupAction({
    access,
    welcomeComplete: welcome.data.welcome_completed,
    skipped: welcome.data.setup_skipped,
  });
  if (!action) return null;
  return (
    <Shell>
      <div>
        <h2 className="text-sm font-semibold text-ink-900">RaiCentral setup</h2>
        <p className="text-xs text-ink-500">
          {welcome.data.welcome_completed
            ? "The introductory setup is complete. Add systems or review setup status any time."
            : "Register your first AI system and connect it to RaiCentral."}
        </p>
      </div>
      <Link href={action.href}>
        <Button size="sm">{action.label}</Button>
      </Link>
    </Shell>
  );
}

/** Dashboard entry to setup. Visibility follows exact `sdk:*` permissions. */
export function SetupEntry() {
  const { access, retry } = useSetupAccess();
  if (access === "loading" || access === "unavailable") return null;
  if (access === "error") {
    return (
      <Shell>
        <p className="text-sm text-ink-500">Setup status unavailable.</p>
        <Button size="sm" variant="secondary" onClick={() => void retry()}>
          Retry
        </Button>
      </Shell>
    );
  }
  return <EntryAction access={access} />;
}
