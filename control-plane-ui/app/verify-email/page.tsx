"use client";

import Link from "next/link";
import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { verifyEmailToken } from "@/lib/api";

type Status = "verifying" | "success" | "error";

function VerifyEmailInner() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [status, setStatus] = useState<Status>(token ? "verifying" : "error");
  const [message, setMessage] = useState<string>(
    token ? "" : "This page requires a valid verification token from your email.",
  );
  const startedRef = useRef(false);

  useEffect(() => {
    if (!token || startedRef.current) return;
    startedRef.current = true;
    verifyEmailToken(token)
      .then(() => setStatus("success"))
      .catch((err) => {
        setStatus("error");
        setMessage(
          err instanceof Error ? err.message : "Verification failed. The link may have expired.",
        );
      });
  }, [token]);

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-md rounded-xl border border-base-200 bg-white/90 p-6 shadow-panel">
        <h1 className="text-xl font-semibold text-ink-900">Email verification</h1>

        {status === "verifying" ? (
          <p className="mt-4 text-sm text-ink-500">Verifying your email…</p>
        ) : null}

        {status === "success" ? (
          <div className="mt-4 space-y-4">
            <div className="rounded-lg border border-accent-600/20 bg-accent-50 p-3 text-sm text-ink-700">
              Your email has been verified.
            </div>
            <Link
              href="/login"
              className="inline-flex w-full items-center justify-center rounded-lg bg-accent-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-accent-500"
            >
              Continue to sign in
            </Link>
          </div>
        ) : null}

        {status === "error" ? (
          <div className="mt-4 space-y-4">
            <div className="rounded-lg border border-danger-600/20 bg-danger-100 p-3 text-sm text-danger-600">
              {message}
            </div>
            <Link href="/login" className="text-sm font-medium text-accent-600 hover:text-accent-500">
              Back to sign in
            </Link>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center p-6 text-sm text-ink-500">
          Loading…
        </div>
      }
    >
      <VerifyEmailInner />
    </Suspense>
  );
}
