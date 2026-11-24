"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { OnboardingChat } from "@/components/onboarding/chat/onboarding-chat";
import { ensureActive, startConversation } from "@/lib/assistant-conversations";

const OPEN_KEY = "rai-assistant-open";

/**
 * Floating RAI assistant, available on every page. The chat mounts on first open and then
 * stays mounted (hidden when closed) so an in-flight answer and the draft survive closing.
 */
export function AssistantWidget() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);

  useEffect(() => {
    try {
      if (window.localStorage.getItem(OPEN_KEY) === "1") {
        setOpen(true);
        setEverOpened(true);
        setConversationId(ensureActive());
      }
    } catch {
      // storage unavailable: start closed
    }
  }, []);

  // Back from the full-screen page: show whichever chat is active there now.
  useEffect(() => {
    if (pathname !== "/assistant" && everOpened) setConversationId(ensureActive());
  }, [pathname, everOpened]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function toggle(next: boolean) {
    setOpen(next);
    if (next) {
      setEverOpened(true);
      // Pick up a chat started or selected on the full-screen page.
      setConversationId(ensureActive());
    }
    try {
      window.localStorage.setItem(OPEN_KEY, next ? "1" : "0");
    } catch {
      // ignore
    }
  }

  // The full-screen assistant page is the same chat; no floating copy on top of it.
  if (pathname === "/assistant") return null;

  return (
    <>
      {everOpened && conversationId && (
        <section
          aria-label="RAI assistant"
          aria-hidden={!open}
          data-testid="assistant-widget"
          className={`fixed bottom-20 right-4 z-40 flex h-[min(40rem,calc(100vh-7rem))] w-[min(26rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-base-200 bg-base-50 shadow-2xl sm:right-6 ${open ? "" : "hidden"}`}
        >
          <OnboardingChat
            key={conversationId}
            conversationId={conversationId}
            variant="widget"
            onClose={() => toggle(false)}
            onNewChat={() => setConversationId(startConversation())}
          />
        </section>
      )}
      <button
        type="button"
        aria-label={open ? "Close RAI assistant" : "Open RAI assistant"}
        aria-expanded={open}
        onClick={() => toggle(!open)}
        className="fixed bottom-4 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-accent-600 text-white shadow-lg transition hover:scale-105 hover:bg-accent-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-600 focus-visible:ring-offset-2 sm:right-6"
      >
        {open ? (
          <span aria-hidden className="text-xl leading-none">×</span>
        ) : (
          <svg aria-hidden width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12Z" />
          </svg>
        )}
      </button>
    </>
  );
}
