"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { AssistantWidget } from "@/components/onboarding/chat/assistant-widget";
import { FirstRunGate } from "@/components/onboarding/first-run/first-run-gate";
import { AuthProvider } from "@/providers/auth-provider";
import { OnboardingVisibilityProvider } from "@/providers/onboarding-visibility-provider";
import { OrganizationProvider } from "@/providers/organization-provider";
import { PermissionProvider } from "@/providers/permission-provider";
import { useAuth } from "@/lib/hooks/use-auth";
import { ADMIN_AUTH_TOKEN_STORAGE_KEY, resendVerificationEmail } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ThemeProvider, useDashboardTheme } from "@/providers/theme-provider";

function AppShellContent({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, isAuthenticated, isLoading } = useAuth();
  const { theme } = useDashboardTheme();
  const [resendMsg, setResendMsg] = useState<string | null>(null);
  const shellClass = cn("console min-h-screen bg-base-100 text-ink-900", theme === "dark" && "console-dark");

  useEffect(() => {
    if (isLoading) return;

    const sessionToken = window.localStorage.getItem(ADMIN_AUTH_TOKEN_STORAGE_KEY);
    if (!sessionToken || !isAuthenticated) {
      router.replace("/");
    }
  }, [isAuthenticated, isLoading, router]);

  async function onResend() {
    if (!user?.email) return;
    try {
      await resendVerificationEmail(user.email);
      setResendMsg("Verification email sent.");
    } catch (err) {
      setResendMsg(err instanceof Error ? err.message : "Failed to resend");
    }
  }

  if (isLoading) {
    return (
      <div className={cn(shellClass, "p-6 text-sm text-ink-500")}>
        Loading authentication...
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className={cn(shellClass, "p-6 text-sm text-ink-500")}>Redirecting to login...</div>
    );
  }

  const emailVerified = user?.email_verified ?? true;

  // Setup is a page of its own: no sidebar, header or assistant.
  const content =
    pathname === "/welcome" ? (
      <div className={shellClass}>{children}</div>
    ) : (
      <div className={shellClass}>
        <OnboardingVisibilityProvider>
          <Sidebar />
          <div className="min-h-screen lg:pl-60">
            <div className="sticky top-0 z-20 border-b border-base-200 bg-base-100/95 px-4 backdrop-blur">
              <Topbar />
            </div>
            {!emailVerified && (
              <div className="border-b border-warn-600/40 bg-warn-50 px-4 py-3 text-sm text-warn-700">
                Verify your email to create or change AI systems and other governed resources.{" "}
                <button
                  type="button"
                  className="font-semibold underline"
                  onClick={() => void onResend()}
                >
                  Resend verification
                </button>
                {resendMsg ? <span className="ml-2">{resendMsg}</span> : null}
              </div>
            )}
            <main className="mx-auto max-w-[1680px] animate-fadeUp p-4 sm:p-6">{children}</main>
          </div>
          <AssistantWidget />
        </OnboardingVisibilityProvider>
      </div>
    );

  return (
    <FirstRunGate
      fallback={<div className={cn(shellClass, "p-6 text-sm text-ink-500")}>Loading…</div>}
    >
      {content}
    </FirstRunGate>
  );
}

export default function AppShell({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <OrganizationProvider>
        <PermissionProvider>
          <ThemeProvider>
            <AppShellContent>{children}</AppShellContent>
          </ThemeProvider>
        </PermissionProvider>
      </OrganizationProvider>
    </AuthProvider>
  );
}
