"use client";

import { useCallback, useEffect, useState } from "react";
import { OnboardingChat } from "@/components/onboarding/chat/onboarding-chat";
import { Button } from "@/components/ui/button";
import {
  CHANGED_EVENT,
  deleteConversation,
  ensureActive,
  listConversations,
  renameConversation,
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
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

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

  function startRename(c: StoredConversation) {
    setEditingId(c.id);
    setDraftTitle(c.title);
  }

  function commitRename() {
    if (editingId) renameConversation(editingId, draftTitle);
    setEditingId(null);
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
              {editingId === c.id ? (
                <div className="rounded-lg bg-accent-50 px-3 py-2">
                  <input
                    autoFocus
                    aria-label="Chat name"
                    value={draftTitle}
                    maxLength={80}
                    onChange={(e) => setDraftTitle(e.target.value)}
                    onFocus={(e) => e.currentTarget.select()}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename();
                      if (e.key === "Escape") setEditingId(null);
                    }}
                    className="w-full rounded border border-accent-600 bg-base-50 px-2 py-1 text-sm text-ink-900 outline-none"
                  />
                  <span className="block pt-1 text-xs text-ink-500">Enter to save · Esc to cancel</span>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => select(c.id)}
                    onDoubleClick={() => startRename(c)}
                    title="Double-click to rename"
                    aria-current={c.id === activeId ? "true" : undefined}
                    className={`w-full rounded-lg px-3 py-2 pr-16 text-left transition ${
                      c.id === activeId ? "bg-accent-50 text-ink-900" : "text-ink-700 hover:bg-base-100"
                    }`}
                  >
                    <span className="block truncate text-sm font-medium">{c.title}</span>
                    <span className="block text-xs text-ink-500">{when(c.updatedAt)}</span>
                  </button>
                  <div className="absolute right-1.5 top-2 flex opacity-0 focus-within:opacity-100 group-hover:opacity-100">
                    <button
                      type="button"
                      aria-label={`Rename conversation ${c.title}`}
                      title="Rename"
                      onClick={() => startRename(c)}
                      className="flex h-7 w-7 items-center justify-center rounded-full text-ink-500 hover:bg-base-200 hover:text-ink-900"
                    >
                      <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete conversation ${c.title}`}
                      title="Delete"
                      onClick={() => remove(c.id)}
                      className="flex h-7 w-7 items-center justify-center rounded-full text-ink-500 hover:bg-base-200 hover:text-ink-900"
                    >
                      ×
                    </button>
                  </div>
                </>
              )}
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
