"use client";

import { useCallback, useEffect, useState } from "react";
import { OnboardingChat } from "@/components/onboarding/chat/onboarding-chat";
import { Button } from "@/components/ui/button";
import {
  CHANGED_EVENT,
  deleteConversation,
  ensureActive,
  listConversations,
  setActive,
  startConversation,
  type StoredConversation,
} from "@/lib/assistant-conversations";

function when(ts: number): string {
  const diff = Date.now() - ts;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "Just now";
  if (min < 60) return `${min} min ago`;
  const hours = Math.floor(min / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(ts).toLocaleDateString();
}

/** Full-screen assistant with chat history. Reached from the widget; not in the sidebar. */
export default function AssistantPage() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [history, setHistory] = useState<StoredConversation[]>([]);

  const refresh = useCallback(() => setHistory(listConversations()), []);

  useEffect(() => {
    setActiveId(ensureActive());
    refresh();
    window.addEventListener(CHANGED_EVENT, refresh);
    return () => window.removeEventListener(CHANGED_EVENT, refresh);
  }, [refresh]);

  function select(id: string) {
    setActive(id);
    setActiveId(id);
  }

  function newChat() {
    setActiveId(startConversation());
  }

  function remove(id: string) {
    if (!window.confirm("Delete this conversation from this browser?")) return;
    const next = deleteConversation(id);
    if (id === activeId) setActiveId(next);
  }

  return (
    <div
      className="flex h-[calc(100vh-8rem)] min-h-[30rem] overflow-hidden rounded-xl border border-base-200 bg-base-50"
      data-testid="assistant-page"
    >
      <aside
        className="hidden w-72 shrink-0 flex-col border-r border-base-200 bg-base-100/50 md:flex"
        aria-label="Chat history"
      >
        <div className="p-3">
          <Button className="w-full" onClick={newChat}>
            + New chat
          </Button>
        </div>
        <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {history.length === 0 && (
            <li className="px-3 py-2 text-xs text-ink-500">Your conversations will appear here.</li>
          )}
          {history.map((c) => (
            <li key={c.id} className="group relative">
              <button
                type="button"
                onClick={() => select(c.id)}
                aria-current={c.id === activeId ? "true" : undefined}
                className={`w-full rounded-lg px-3 py-2 pr-9 text-left transition ${
                  c.id === activeId ? "bg-accent-50 text-ink-900" : "text-ink-700 hover:bg-base-100"
                }`}
              >
                <span className="block truncate text-sm font-medium">{c.title}</span>
                <span className="block text-xs text-ink-500">{when(c.updatedAt)}</span>
              </button>
              <button
                type="button"
                aria-label={`Delete conversation ${c.title}`}
                onClick={() => remove(c.id)}
                className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full text-ink-500 opacity-0 hover:bg-base-200 hover:text-ink-900 focus:opacity-100 group-hover:opacity-100"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="min-w-0 flex-1">
        {activeId && (
          <OnboardingChat key={activeId} conversationId={activeId} variant="page" onNewChat={newChat} />
        )}
      </div>
    </div>
  );
}
