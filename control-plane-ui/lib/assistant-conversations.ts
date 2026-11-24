/**
 * Browser-local history of assistant conversations. Each conversation owns its messages and
 * the onboarding session its attached files were saved to, so "new chat" starts with an empty
 * file quota. Server state (sessions, change sets) stays the source of truth for onboarding.
 */

export interface StoredConversation<M = unknown> {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  /** Onboarding session holding this conversation's attached files, once any were attached. */
  sessionId: string | null;
  messages: M[];
}

interface Store {
  activeId: string | null;
  items: StoredConversation[];
}

const STORE_KEY = "rai-assistant-conversations-v1";
const LEGACY_HISTORY_KEY = "rai-assistant-chat";
const MAX_CONVERSATIONS = 30;
export const CHANGED_EVENT = "rai-assistant-conversations-changed";
export const NEW_TITLE = "New chat";

let seq = 0;
const newId = () => `c${Date.now().toString(36)}${(seq++).toString(36)}${Math.random().toString(36).slice(2, 6)}`;

function read(): Store {
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Store;
      if (parsed && Array.isArray(parsed.items)) return { activeId: parsed.activeId ?? null, items: parsed.items };
    }
    // One-time migration of the single pre-history conversation.
    const legacy = window.localStorage.getItem(LEGACY_HISTORY_KEY);
    if (legacy) {
      const messages = JSON.parse(legacy) as unknown[];
      window.localStorage.removeItem(LEGACY_HISTORY_KEY);
      if (Array.isArray(messages) && messages.length) {
        const now = Date.now();
        const conv: StoredConversation = {
          id: newId(),
          title: deriveTitle(messages),
          createdAt: now,
          updatedAt: now,
          sessionId: null,
          messages,
        };
        const store = { activeId: conv.id, items: [conv] };
        write(store);
        return store;
      }
    }
  } catch {
    // unreadable storage: behave as empty
  }
  return { activeId: null, items: [] };
}

function write(store: Store) {
  const items = [...store.items].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONVERSATIONS);
  const payload = { activeId: store.activeId, items };
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(payload));
  } catch {
    // Quota: drop the oldest half once and retry; the chat still works in memory otherwise.
    try {
      window.localStorage.setItem(
        STORE_KEY,
        JSON.stringify({ ...payload, items: items.slice(0, Math.ceil(items.length / 2)) }),
      );
    } catch {
      // storage unavailable
    }
  }
  window.dispatchEvent(new Event(CHANGED_EVENT));
}

function deriveTitle(messages: unknown[]): string {
  const first = (messages as { role?: string; text?: string; files?: { name: string }[] }[]).find(
    (m) => m.role === "user" && (m.text || m.files?.length),
  );
  const raw = (first?.text || first?.files?.[0]?.name || NEW_TITLE).replace(/\s+/g, " ").trim();
  return raw.length > 60 ? `${raw.slice(0, 57)}…` : raw;
}

/** Conversations that have at least one message, newest first. */
export function listConversations(): StoredConversation[] {
  return read()
    .items.filter((c) => c.messages.length > 0)
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getConversation(id: string): StoredConversation | null {
  return read().items.find((c) => c.id === id) ?? null;
}

export function getActiveId(): string | null {
  return read().activeId;
}

export function setActive(id: string) {
  const store = read();
  if (store.items.some((c) => c.id === id)) write({ ...store, activeId: id });
}

/** The active conversation, creating an empty one when there is none. */
export function ensureActive(): string {
  const store = read();
  const active = store.items.find((c) => c.id === store.activeId);
  if (active) return active.id;
  return startConversation();
}

/** Start a new chat. An already-empty active chat is reused instead of piling up blanks. */
export function startConversation(): string {
  const store = read();
  const empty = store.items.find((c) => c.id === store.activeId && c.messages.length === 0);
  if (empty) return empty.id;
  const now = Date.now();
  const conv: StoredConversation = {
    id: newId(),
    title: NEW_TITLE,
    createdAt: now,
    updatedAt: now,
    sessionId: null,
    messages: [],
  };
  // Blank, never-used chats are not worth keeping.
  write({ activeId: conv.id, items: [conv, ...store.items.filter((c) => c.messages.length > 0)] });
  return conv.id;
}

export function saveConversation(id: string, update: { messages: unknown[]; sessionId: string | null }) {
  const store = read();
  const existing = store.items.find((c) => c.id === id);
  const now = Date.now();
  const unchanged =
    existing &&
    existing.sessionId === update.sessionId &&
    JSON.stringify(existing.messages) === JSON.stringify(update.messages);
  if (unchanged) return;
  const next: StoredConversation = {
    id,
    title: update.messages.length ? deriveTitle(update.messages) : NEW_TITLE,
    createdAt: existing?.createdAt ?? now,
    updatedAt: update.messages.length ? now : (existing?.updatedAt ?? now),
    sessionId: update.sessionId,
    messages: update.messages,
  };
  write({
    activeId: store.activeId ?? id,
    items: [next, ...store.items.filter((c) => c.id !== id)],
  });
}

/** Delete a chat; returns the id to show next. */
export function deleteConversation(id: string): string {
  const store = read();
  const items = store.items.filter((c) => c.id !== id);
  const nextActive = store.activeId === id ? (items.find((c) => c.messages.length > 0)?.id ?? null) : store.activeId;
  write({ activeId: nextActive, items });
  return nextActive ?? startConversation();
}
