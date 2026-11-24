"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { getConversation, saveConversation } from "@/lib/assistant-conversations";
import { Markdown } from "@/components/onboarding/chat/markdown";
import { ProposalCard } from "@/components/onboarding/chat/proposal-card";
import { ResultsCard } from "@/components/onboarding/chat/results-card";
import {
  chatWithAssistant,
  executeProposal,
  type ExecutedCall,
  type ProposedAction,
} from "@/lib/assistant-api";
import {
  IntakeApiError,
  createSession,
  getChangeSet,
  listChangeSets,
  newIdempotencyKey,
  type ChangeSetSummary,
} from "@/lib/onboarding-api";
import {
  ChatPipelineError,
  STEP_LABELS,
  analyzeMessage,
  applyReview,
  initialSteps,
  isApplyConfirmation,
  isApplyIntent,
  wantsReanalysis,
  type OnboardingResults,
  type StepId,
  type StepState,
} from "@/lib/onboarding-chat";
import { SUPPORTED_ACCEPT, formatBytes, useIntakeAccess } from "@/lib/onboarding-intake";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text?: string;
  files?: { name: string; size: number }[];
  steps?: StepState;
  results?: OnboardingResults;
  changeSet?: ChangeSetSummary | null;
  proposal?: ProposedAction;
  executed?: ExecutedCall[];
  error?: boolean;
}

const WELCOME =
  "Ask me anything about your AI systems, agents, rules, budgets, risks or audit trail, or tell me what to change. To onboard from documents, attach policies or inventories with the + button and I will analyze them. I always show you a change before making it; say “apply” to confirm.";
const WELCOME_MESSAGE: ChatMessage = { id: "welcome", role: "assistant", text: WELCOME };

const HISTORY_LIMIT = 100;

/** Progress checklists are transient; a reload mid-run would show them frozen. */
function persistable(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((m) => m.id !== "welcome" && !m.steps).slice(-HISTORY_LIMIT);
}

const STEP_ORDER = Object.keys(STEP_LABELS) as StepId[];
let counter = 0;
const nextId = () => `m${Date.now()}-${counter++}`;

export function OnboardingChat({
  conversationId,
  variant = "page",
  onClose,
  onNewChat,
}: {
  /** Which stored conversation this chat shows. Remount (key) it to switch. */
  conversationId: string;
  /** "widget" is the compact floating panel; "page" is the full-screen assistant. */
  variant?: "page" | "widget";
  onClose?: () => void;
  onNewChat: () => void;
}) {
  const widget = variant === "widget";
  const access = useIntakeAccess();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME_MESSAGE]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  // What a bare "apply" confirms. A reviewed onboarding change set lives on the server; an
  // assistant proposal exists only until the next message.
  const pendingChangeSet = useRef<ChangeSetSummary | null>(null);
  const pendingProposal = useRef<ProposedAction | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const patch = useCallback((id: string, update: Partial<ChatMessage>) => {
    setMessages((all) => all.map((m) => (m.id === id ? { ...m, ...update } : m)));
  }, []);
  const push = useCallback((message: Omit<ChatMessage, "id">) => {
    const id = nextId();
    setMessages((all) => [...all, { ...message, id }]);
    return id;
  }, []);

  // Restore this conversation and, if it saved files, its reviewed change set.
  useEffect(() => {
    const stored = getConversation(conversationId);
    if (stored?.messages.length) setMessages([WELCOME_MESSAGE, ...(stored.messages as ChatMessage[])]);
    setSessionId(stored?.sessionId ?? null);
    setHydrated(true);
  }, [conversationId]);

  useEffect(() => {
    if (!access.canManage || !sessionId || pendingChangeSet.current) return;
    let cancelled = false;
    (async () => {
      try {
        const sets = await listChangeSets(sessionId);
        const prepared = sets.items.find((c) => c.status === "PREPARED");
        if (prepared && !cancelled) {
          pendingChangeSet.current = await getChangeSet(sessionId, prepared.id);
        }
      } catch {
        // Nothing to resume; a change set is prepared again when files are analyzed.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [access.canManage, sessionId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    if (hydrated) saveConversation(conversationId, { messages: persistable(messages), sessionId });
  }, [messages, sessionId, hydrated, conversationId]);

  function addFiles(files: File[]) {
    if (files.length) setPending((p) => [...p, ...files]);
  }

  async function ensureSession(): Promise<string> {
    if (sessionId) return sessionId;
    const created = await createSession("AI onboarding chat", newIdempotencyKey());
    setSessionId(created.id);
    return created.id;
  }

  async function send() {
    const text = draft.trim();
    const files = pending;
    if (busy || (!text && files.length === 0)) return;
    setDraft("");
    setPending([]);
    const history = toTurns(messages);
    push({
      role: "user",
      text: text || undefined,
      files: files.map((f) => ({ name: f.name, size: f.size })),
    });
    setBusy(true);
    try {
      if (files.length > 0) {
        // Attaching files is the one unambiguous request to onboard from documents.
        pendingProposal.current = null;
        await runAnalysis(text, files);
        return;
      }
      const waiting = Boolean(pendingProposal.current || pendingChangeSet.current);
      if (isApplyConfirmation(text) || (waiting && isApplyIntent(text))) {
        if (pendingProposal.current) await runProposal(pendingProposal.current);
        else if (pendingChangeSet.current) await runApply(pendingChangeSet.current);
        else push({ role: "assistant", text: "There is nothing waiting for confirmation." });
        return;
      }
      pendingProposal.current = null; // an unconfirmed proposal expires with the next message
      await runAssistant([...history, { role: "user", content: text }], text);
    } finally {
      setBusy(false);
    }
  }

  async function runAssistant(
    turns: { role: "user" | "assistant"; content: string }[],
    userText: string,
  ) {
    try {
      const answer = await chatWithAssistant(turns);
      if (answer.pending_action) pendingProposal.current = answer.pending_action;
      // The analysis is slow and already done once. Re-running it is only for an explicit request;
      // otherwise a reviewed change that is waiting is what the person is talking about.
      const rerun = answer.ui_action === "run_onboarding";
      if (rerun && pendingChangeSet.current && !wantsReanalysis(userText)) {
        push({
          role: "assistant",
          text: "The files are already analyzed and the review is waiting. Say **apply** to register it, tell me what to change, or ask me to re-analyze the files.",
        });
        return;
      }
      push({
        role: "assistant",
        text: answer.reply || undefined,
        proposal: answer.pending_action ?? undefined,
      });
      if (rerun) {
        if (!sessionId) {
          push({ role: "assistant", text: "No files are saved yet. Attach them with the + button." });
        } else {
          await runAnalysis("", []);
        }
      }
    } catch (error) {
      push({ role: "assistant", text: errorText(error), error: true });
    }
  }

  async function runProposal(action: ProposedAction) {
    const progressId = push({ role: "assistant", text: "Applying your change…" });
    try {
      const { results } = await executeProposal(action.calls);
      pendingProposal.current = null;
      const failed = results.find((r) => !r.ok);
      patch(progressId, {
        text: failed ? "Stopped at the first error. Nothing after it ran." : "Done.",
        executed: results,
        error: Boolean(failed),
      });
    } catch (error) {
      patch(progressId, { text: errorText(error), error: true });
    }
  }

  async function runAnalysis(text: string, files: File[]) {
    const progressId = push({ role: "assistant", steps: initialSteps() });
    try {
      let id = await ensureSession();
      pendingChangeSet.current = null;
      const run = (sid: string) =>
        analyzeMessage({
          sessionId: sid,
          text,
          files,
          onStep: (step, status) =>
            setMessages((all) =>
              all.map((m) =>
                m.id === progressId ? { ...m, steps: { ...(m.steps as StepState), [step]: status } } : m,
              ),
            ),
        });
      let outcome;
      try {
        outcome = await run(id);
      } catch (error) {
        if (!(error instanceof ChatPipelineError) || !/limit of \d+ files/i.test(error.message)) {
          throw error;
        }
        // The saved session is full. Move to a fresh one and retry once.
        const created = await createSession("AI onboarding chat", newIdempotencyKey());
        setSessionId(created.id);
        id = created.id;
        outcome = await run(id);
      }
      pendingChangeSet.current = outcome.changeSet?.status === "PREPARED" ? outcome.changeSet : null;
      const attention = outcome.attention.length
        ? ` Note: ${outcome.attention.map((a) => a.label).join(", ")} could only be partly read.`
        : "";
      push({
        role: "assistant",
        text: summaryLine(outcome.results, outcome.changeSet) + attention,
        results: outcome.results,
        changeSet: outcome.changeSet,
      });
    } catch (error) {
      pendingChangeSet.current = null;
      patch(progressId, { error: true });
      push({ role: "assistant", text: errorText(error), error: true });
    }
  }

  async function runApply(changeSet: ChangeSetSummary) {
    const progressId = push({ role: "assistant", text: "Applying the reviewed changes…" });
    try {
      const id = await ensureSession();
      const { status } = await applyReview(id, changeSet);
      pendingChangeSet.current = null;
      patch(progressId, {
        text:
          status === "SUCCEEDED" || status === "APPLIED"
            ? "Applied. New rules and budgets are created disabled until activated, and runtime verification has not run yet."
            : `The changes were submitted (status: ${status}).`,
      });
    } catch (error) {
      patch(progressId, { text: errorText(error), error: true });
    }
  }

  if (!access.canRead) {
    return <p className="text-sm text-ink-600">You do not have access to the assistant.</p>;
  }

  const composerDisabled = !access.canManage || !hydrated;

  return (
    <div
      className="flex h-full min-h-0 w-full flex-col"
      data-testid="onboarding-chat"
      onDragOver={(e) => {
        e.preventDefault();
        if (!composerDisabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (!composerDisabled) addFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <header className={`flex items-center justify-between gap-3 border-b border-base-200 ${widget ? "px-4 py-3" : "px-6 py-3"}`}>
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-ink-900">RAI assistant</h1>
          <p className="truncate text-xs text-ink-500">
            Ask about your AI estate, usage and cost, or request changes.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="ghost" size="sm" disabled={busy} onClick={onNewChat}>
            New chat
          </Button>
          {widget &&
            (busy ? (
              <span
                title="Wait for the answer before opening full screen"
                className="flex h-8 w-8 items-center justify-center rounded-full text-ink-500 opacity-50"
              >
                <ExpandIcon />
              </span>
            ) : (
              <Link
                href="/assistant"
                aria-label="Open full-screen assistant"
                title="Open full screen"
                className="flex h-8 w-8 items-center justify-center rounded-full text-ink-600 hover:bg-base-100"
              >
                <ExpandIcon />
              </Link>
            ))}
          {widget && onClose && (
            <button
              type="button"
              aria-label="Close assistant"
              onClick={onClose}
              className="flex h-8 w-8 items-center justify-center rounded-full text-lg text-ink-600 hover:bg-base-100"
            >
              ×
            </button>
          )}
        </div>
      </header>

      <div
        className={`min-h-0 flex-1 overflow-y-auto bg-base-50 ${widget ? "p-4" : "px-6 py-5"}`}
        role="log"
        aria-live="polite"
        aria-label="Conversation"
      >
        <div className={`space-y-4 ${widget ? "" : "mx-auto max-w-3xl"}`}>
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {busy && <TypingDots />}
          <div ref={bottom} />
        </div>
      </div>

      {!access.canManage && (
        <p className="px-4 pt-2 text-xs text-ink-500">
          You can view this page, but chatting needs manage and source permissions.
        </p>
      )}

      <div
        className={`rounded-2xl border bg-base-50 p-2 ${widget ? "m-3" : "mx-auto mb-4 mt-2 w-[calc(100%-3rem)] max-w-3xl"} ${dragging ? "border-accent-600" : "border-base-200"}`}
      >
        {pending.length > 0 && (
          <ul className="flex flex-wrap gap-2 px-1 pb-2" aria-label="Attached files">
            {pending.map((f, i) => (
              <li
                key={`${f.name}-${i}`}
                className="flex items-center gap-2 rounded-full border border-base-200 px-3 py-1 text-xs text-ink-700"
              >
                <span className="max-w-48 truncate">{f.name}</span>
                <span className="text-ink-500">{formatBytes(f.size)}</span>
                <button
                  type="button"
                  aria-label={`Remove ${f.name}`}
                  className="text-ink-500 hover:text-ink-900"
                  onClick={() => setPending((p) => p.filter((_, j) => j !== i))}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex items-end gap-2">
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            accept={SUPPORTED_ACCEPT}
            data-testid="chat-file-input"
            onChange={(e) => {
              addFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
          <button
            type="button"
            aria-label="Attach files"
            title="Attach PDF, DOCX, XLSX, CSV, TXT or Markdown to onboard from documents"
            disabled={composerDisabled || busy}
            onClick={() => fileInput.current?.click()}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg text-ink-600 hover:bg-base-100 disabled:opacity-50"
          >
            +
          </button>
          <textarea
            aria-label="Message"
            rows={1}
            value={draft}
            disabled={composerDisabled}
            placeholder="Ask anything, or describe a change…"
            className="max-h-40 min-h-9 flex-1 resize-none bg-transparent px-1 py-2 text-sm text-ink-900 outline-none placeholder:text-ink-500"
            onChange={(e) => {
              setDraft(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
            onPaste={(e) => {
              const files = Array.from(e.clipboardData.files);
              if (files.length) {
                e.preventDefault();
                addFiles(files);
              }
            }}
          />
          <Button
            aria-label="Send"
            disabled={composerDisabled || busy || (!draft.trim() && pending.length === 0)}
            onClick={() => void send()}
          >
            {busy ? "Working…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Plain-text turns for the assistant: what was said, not the cards. */
function toTurns(messages: ChatMessage[]): { role: "user" | "assistant"; content: string }[] {
  return messages
    .filter((m) => m.id !== "welcome" && m.text && !m.error)
    .map((m) => ({ role: m.role, content: m.text as string }))
    .slice(-12);
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`min-w-0 space-y-2 rounded-2xl px-4 py-2.5 text-sm ${
          isUser
            ? "max-w-[85%] rounded-br-md bg-accent-600 text-white"
            : message.error
              ? "max-w-full rounded-bl-md border border-danger-600 text-ink-900"
              : "max-w-full rounded-bl-md border border-base-200 bg-base-100/40 text-ink-900"
        }`}
      >
        {message.text &&
          (isUser ? (
            <p className="whitespace-pre-wrap">{message.text}</p>
          ) : (
            <Markdown text={message.text} />
          ))}
        {message.files && message.files.length > 0 && (
          <ul className="space-y-1 text-xs opacity-90">
            {message.files.map((f, i) => (
              <li key={`${f.name}-${i}`}>
                📎 {f.name} · {formatBytes(f.size)}
              </li>
            ))}
          </ul>
        )}
        {message.steps && <StepList steps={message.steps} />}
        {message.results && (
          <ResultsCard results={message.results} changeSet={message.changeSet ?? null} />
        )}
        {(message.proposal || message.executed) && (
          <ProposalCard proposal={message.proposal} executed={message.executed} />
        )}
      </div>
    </div>
  );
}

function ExpandIcon() {
  return (
    <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
    </svg>
  );
}

function TypingDots() {
  return (
    <div className="flex justify-start" aria-label="The assistant is working">
      <div className="flex gap-1 rounded-2xl rounded-bl-md border border-base-200 px-4 py-3">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="h-1.5 w-1.5 animate-pulse rounded-full bg-ink-500"
            style={{ animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

function StepList({ steps }: { steps: StepState }) {
  return (
    <ul className="space-y-1" aria-label="Progress">
      {STEP_ORDER.map((id) => {
        const status = steps[id];
        const mark = { pending: "○", running: "◐", done: "●", failed: "✕" }[status];
        return (
          <li
            key={id}
            className={`flex items-center gap-2 text-xs ${
              status === "pending" ? "text-ink-500" : status === "failed" ? "text-danger-600" : "text-ink-800"
            }`}
          >
            <span aria-hidden className={status === "running" ? "animate-pulse" : ""}>
              {mark}
            </span>
            {STEP_LABELS[id]}
          </li>
        );
      })}
    </ul>
  );
}

function summaryLine(results: OnboardingResults, changeSet: ChangeSetSummary | null): string {
  const agents =
    results.systems.reduce((n, s) => n + s.agents.length, 0) + results.unattachedAgents.length;
  const found = `I found ${plural(results.systems.length, "AI system")}, ${plural(agents, "agent")}, ${plural(results.models.length, "model")} and ${plural(results.rules.length, "rule")}.`;
  if (!changeSet) {
    return `${found} Nothing in it can be registered yet. Tell me what is missing, for example which AI system a rule belongs to.`;
  }
  return `${found} Review it below. Say “apply” to register it, or tell me what to change.`;
}

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function errorText(error: unknown): string {
  if (error instanceof ChatPipelineError) {
    return `${STEP_LABELS[error.step]} did not finish: ${error.message}`;
  }
  if (error instanceof IntakeApiError && error.status === 409 && error.code === "REVISION_CONFLICT") {
    return "The conversation changed in another tab. Send your message again.";
  }
  return error instanceof Error ? error.message : "Something went wrong. Send your message again.";
}
