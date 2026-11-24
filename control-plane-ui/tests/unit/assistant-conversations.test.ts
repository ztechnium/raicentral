import { beforeEach, describe, expect, it } from "vitest";
import {
  deleteConversation,
  ensureActive,
  getConversation,
  listConversations,
  saveConversation,
  startConversation,
} from "@/lib/assistant-conversations";

const msg = (text: string) => ({ id: text, role: "user", text });

describe("assistant conversations", () => {
  beforeEach(() => window.localStorage.clear());

  it("reuses an empty active chat instead of piling up blanks", () => {
    const a = ensureActive();
    expect(startConversation()).toBe(a);
  });

  it("keeps the old chat, with its onboarding session, when a new one starts", () => {
    const first = ensureActive();
    saveConversation(first, { messages: [msg("Analyze the EU AI Act")], sessionId: "sess-1" });
    const second = startConversation();
    expect(second).not.toBe(first);
    expect(getConversation(second)?.sessionId).toBeNull();
    const list = listConversations();
    expect(list.map((c) => c.id)).toEqual([first]);
    expect(list[0].title).toBe("Analyze the EU AI Act");
    expect(list[0].sessionId).toBe("sess-1");
  });

  it("deleting the active chat falls back to the newest remaining one", () => {
    const a = ensureActive();
    saveConversation(a, { messages: [msg("one")], sessionId: null });
    const b = startConversation();
    saveConversation(b, { messages: [msg("two")], sessionId: null });
    expect(deleteConversation(b)).toBe(a);
  });

  it("migrates the legacy single conversation", () => {
    window.localStorage.setItem("rai-assistant-chat", JSON.stringify([msg("old question")]));
    const [only] = listConversations();
    expect(only.title).toBe("old question");
    expect(window.localStorage.getItem("rai-assistant-chat")).toBeNull();
  });
});
