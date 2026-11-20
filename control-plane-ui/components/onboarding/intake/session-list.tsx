"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  IntakeApiError,
  createSession,
  listSessions,
  newIdempotencyKey,
  type SessionPage,
} from "@/lib/onboarding-api";
import { useIntakeAccess, viewLabel } from "@/lib/onboarding-intake";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string; forbidden: boolean }
  | { status: "ready"; page: SessionPage };

export function SessionList() {
  const router = useRouter();
  const access = useIntakeAccess();
  const [archived, setArchived] = useState(false);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // One key per create attempt, so a retry after a lost response reuses the session.
  const createKey = useRef<string | null>(null);

  const load = useCallback(async () => {
    setState({ status: "loading" });
    try {
      setState({ status: "ready", page: await listSessions({ archived }) });
    } catch (error) {
      const api = error instanceof IntakeApiError ? error : null;
      setState({
        status: "error",
        message: api?.message ?? "Could not load sessions.",
        forbidden: api?.status === 403,
      });
    }
  }, [archived]);

  useEffect(() => {
    void load();
  }, [load]);

  async function create() {
    if (!title.trim()) return;
    setCreating(true);
    setCreateError(null);
    createKey.current ??= newIdempotencyKey();
    try {
      const session = await createSession(title.trim(), createKey.current);
      createKey.current = null;
      router.push(`/app/onboarding/sessions/${session.id}`);
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Could not create the session.");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="space-y-6" data-testid="intake-session-list">
      <header className="space-y-1">
        <h1 className="text-lg font-semibold text-ink-900">Onboarding sessions</h1>
        <p className="text-sm text-ink-500">
          Save your policies, inventories and a prompt, leave, and resume later. Saving does not
          analyze anything.
        </p>
      </header>

      {access.canManage && (
        <div className="flex flex-wrap items-end gap-2 rounded-xl border border-base-200 bg-base-50 p-4">
          <label className="flex-1 text-sm text-ink-700">
            New session title
            <Input
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Q4 AI estate onboarding"
            />
          </label>
          <Button onClick={() => void create()} disabled={creating || !title.trim()}>
            {creating ? "Creating…" : "New session"}
          </Button>
          {createError && (
            <p role="alert" className="w-full text-sm text-danger-600">
              {createError}
            </p>
          )}
        </div>
      )}
      {access.canRead && !access.canManage && (
        <p className="rounded-lg border border-base-200 bg-base-50 p-3 text-sm text-ink-500">
          You can see progress only. Creating sessions and viewing saved sources needs source
          access.
        </p>
      )}

      <div className="flex gap-2 text-sm">
        <Button variant={archived ? "ghost" : "secondary"} onClick={() => setArchived(false)}>
          Active
        </Button>
        <Button variant={archived ? "secondary" : "ghost"} onClick={() => setArchived(true)}>
          Archived
        </Button>
      </div>

      {state.status === "loading" && <p className="text-sm text-ink-500">Loading sessions…</p>}
      {state.status === "error" && (
        <div role="alert" className="space-y-2 rounded-xl border border-base-200 p-4 text-sm">
          <p className="text-ink-700">
            {state.forbidden
              ? "You do not have access to onboarding sessions."
              : `Sessions are unavailable: ${state.message}`}
          </p>
          {!state.forbidden && <Button onClick={() => void load()}>Retry</Button>}
        </div>
      )}
      {state.status === "ready" && state.page.items.length === 0 && (
        <p className="text-sm text-ink-500">
          {archived ? "No archived sessions." : "No sessions yet. Create one to get started."}
        </p>
      )}
      {state.status === "ready" && state.page.items.length > 0 && (
        <ul className="divide-y divide-base-200 rounded-xl border border-base-200">
          {state.page.items.map((item) => (
            <li key={item.id} className="flex items-center justify-between gap-4 p-4">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-ink-900">{item.label}</p>
                <p className="text-xs text-ink-500">
                  {viewLabel(item.view)} · {item.source_count} source
                  {item.source_count === 1 ? "" : "s"}
                  {item.updated_at
                    ? ` · updated ${new Date(item.updated_at).toLocaleString()}`
                    : ""}
                </p>
              </div>
              <Link href={`/app/onboarding/sessions/${item.id}`}>
                <Button variant="outline">Resume</Button>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
