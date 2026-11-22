"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { useSetupAccess } from "@/lib/hooks/use-setup-access";

function Notice({
  title,
  body,
  actions,
  fullScreen,
}: {
  title: string;
  body: string;
  actions: ReactNode;
  fullScreen?: boolean;
}) {
  return (
    <div
      className={
        fullScreen
          ? "flex min-h-screen items-center justify-center p-6"
          : "rounded-xl border border-base-200 bg-base-50 p-6"
      }
      data-testid="setup-gate-notice"
    >
      <div className="max-w-md space-y-3">
        <h1 className="text-base font-semibold text-ink-900">{title}</h1>
        <p className="text-sm text-ink-500">{body}</p>
        <div className="flex flex-wrap gap-2">{actions}</div>
      </div>
    </div>
  );
}

/**
 * Permission gate for the setup screens. Every non-ready state is finite:
 * loading never redirects, a failed permission fetch offers Retry, and a user
 * without `sdk:read` gets an explanation plus a dashboard link.
 */
export function SetupGate({
  children,
  requireManage = false,
  fullScreen = false,
}: {
  children: (access: { canManage: boolean }) => ReactNode;
  /** Mutation-only screens (the welcome wizard) need `sdk:manage`. */
  requireManage?: boolean;
  fullScreen?: boolean;
}) {
  const { access, retry } = useSetupAccess();

  if (access === "loading") {
    return (
      <div
        className={
          fullScreen
            ? "flex min-h-screen items-center justify-center text-sm text-ink-500"
            : "rounded-xl border border-base-200 bg-base-50 p-6 text-sm text-ink-500"
        }
      >
        Checking your access…
      </div>
    );
  }

  if (access === "error") {
    return (
      <Notice
        fullScreen={fullScreen}
        title="Setup is unavailable right now"
        body="We could not load your permissions, so setup status is unknown. This does not mean setup is incomplete."
        actions={
          <>
            <Button onClick={() => void retry()}>Retry</Button>
            <Link href="/dashboard">
              <Button variant="ghost">Back to dashboard</Button>
            </Link>
          </>
        }
      />
    );
  }

  if (access === "unavailable") {
    return (
      <Notice
        fullScreen={fullScreen}
        title="Setup is not available for your account"
        body="Your account does not have access to RaiCentral setup. You can keep using the areas you are authorized for."
        actions={
          <Link href="/dashboard">
            <Button>Go to dashboard</Button>
          </Link>
        }
      />
    );
  }

  if (requireManage && access === "read") {
    return (
      <Notice
        fullScreen={fullScreen}
        title="Setup changes need setup-management access"
        body="You can view setup status, but only users who can manage setup can run the welcome flow."
        actions={
          <>
            <Link href="/app/onboarding">
              <Button>View setup status</Button>
            </Link>
            <Link href="/dashboard">
              <Button variant="ghost">Back to dashboard</Button>
            </Link>
          </>
        }
      />
    );
  }

  return <>{children({ canManage: access === "manage" })}</>;
}
