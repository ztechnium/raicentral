"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
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
  getSession,
  listChangeSets,
  listSessions,
  newIdempotencyKey,
  type ChangeSetSummary,
  type SessionDetail,
} from "@/lib/onboarding-api";
import {
  ChatPipelineError,
  STEP_LABELS,
  analyzeMessage,
  applyReview,
  initialSteps,
  isApplyConfirmation,
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

// One conversation per browser. Server state (sessions, change sets) is the source of truth.
const HISTORY_KEY = "rai-assistant-chat";
const HISTORY_LIMIT = 100;

function loadHistory(): ChatMessage[] {
  try {
    const raw = window.localStorage.getItem(HISTORY_KEY);
    const parsed = raw ? (JSON.parse(raw) as ChatMessage[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(messages: ChatMessage[]) {
  // Progress checklists are transient; a reload mid-run would show them frozen.
  const keep = messages.filter((m) => m.id !== "welcome" && !m.steps);
  try {
    window.localStorage.setItem(HISTORY_KEY, JSON.stringify(keep.slice(-HISTORY_LIMIT)));
  } catch {
    // storage unavailable or full: the chat still works, it just won't survive a refresh
  }
}

const STEP_ORDER = Object.keys(STEP_LABELS) as StepId[];
let counter = 0;
const nextId = () => `m${Date.now()}-${counter++}`;

export function OnboardingChat() {
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

  // Restore the conversation, the open onboarding session and its reviewed change set.
  useEffect(() => {
    const stored = loadHistory();
    if (stored.length) setMessages([WELCOME_MESSAGE, ...stored]);
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!access.canManage) return;
    let cancelled = false;
    (async () => {
      try {
        const page = await listSessions({ limit: 1 });
        const latest = page.items.find((s) => s.lifecycle === "OPEN");
        if (!latest || cancelled) return;
        const detail: SessionDetail = await getSession(latest.id);
        if (cancelled) return;
        setSessionId(detail.id);
        const sets = await listChangeSets(detail.id);
        const prepared = sets.items.find((c) => c.status === "PREPARED");
        if (prepared && !cancelled) {
          pendingChangeSet.current = await getChangeSet(detail.id, prepared.id);
        }
      } catch {
        // No resumable onboarding session; one is created when files are attached.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [access.canManage]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  useEffect(() => {
    if (hydrated) saveHistory(messages);
  }, [messages, hydrated]);

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
      if (isApplyConfirmation(text)) {
        if (pendingProposal.current) await runProposal(pendingProposal.current);
        else if (pendingChangeSet.current) await runApply(pendingChangeSet.current);
        else push({ role: "assistant", text: "There is nothing waiting for confirmation." });
        return;
      }
      pendingProposal.current = null; // an unconfirmed proposal expires with the next message
      await runAssistant([...history, { role: "user", content: text }]);
    } finally {
      setBusy(false);
    }
  }

  async function runAssistant(turns: { role: "user" | "assistant"; content: string }[]) {
    try {
      const answer = await chatWithAssistant(turns);
      if (answer.pending_action) pendingProposal.current = answer.pending_action;
      push({
        role: "assistant",
        text: answer.reply || undefined,
        proposal: answer.pending_action ?? undefined,
      });
      if (answer.ui_action === "run_onboarding") {
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
      const id = await ensureSession();
      pendingChangeSet.current = null;
      const outcome = await analyzeMessage({
        sessionId: id,
        text,
        files,
        onStep: (step, status) =>
          setMessages((all) =>
            all.map((m) =>
              m.id === progressId ? { ...m, steps: { ...(m.steps as StepState), [step]: status } } : m,
            ),
          ),
      });
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

  function clearConversation() {
    setMessages([WELCOME_MESSAGE]);
    pendingProposal.current = null;
    try {
      window.localStorage.removeItem(HISTORY_KEY);
    } catch {
      // ignore
    }
  }

  if (!access.canRead) {
    return <p className="text-sm text-ink-600">You do not have access to the assistant.</p>;
  }

  const composerDisabled = !access.canManage || !hydrated;

  return (
    <div
      className="mx-auto flex h-[calc(100vh-9rem)] min-h-[28rem] w-full max-w-3xl flex-col"
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
      <header className="flex items-center justify-between gap-3 pb-3">
        <div>
          <h1 className="text-lg font-semibold text-ink-900">RAI assistant</h1>
          <p className="text-xs text-ink-500">
            Ask about your AI estate, request changes, or attach documents to onboard.
          </p>
        </div>
        <div className="flex gap-1">
          <Button variant="ghost" size="sm" disabled={busy} onClick={clearConversation}>
            Clear chat
          </Button>
          <Link href="/app/onboarding/ai-setup">
            <Button variant="ghost" size="sm">
              AI connection
            </Button>
          </Link>
          {sessionId && (
            <Link href={`/app/onboarding/sessions/${sessionId}`}>
              <Button variant="ghost" size="sm">
                Details
              </Button>
            </Link>
          )}
        </div>
      </header>

      <div
        className="flex-1 space-y-4 overflow-y-auto rounded-xl border border-base-200 bg-base-50 p-4"
        role="log"
        aria-live="polite"
        aria-label="Conversation"
      >
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        <div ref={bottom} />
      </div>

      {!access.canManage && (
        <p className="pt-2 text-xs text-ink-500">
          You can view this page, but chatting needs manage and source permissions.
        </p>
      )}

      <div
        className={`mt-3 rounded-2xl border bg-base-50 p-2 ${dragging ? "border-accent-600" : "border-base-200"}`}
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
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg text-ink-600 hover:bg-base-700 disabled:opacity-50"
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
        className={`max-w-[92%] space-y-2 rounded-2xl px-4 py-2.5 text-sm ${
          isUser
            ? "bg-accent-600 text-white"
            : message.error
              ? "border border-danger-600 text-ink-900"
              : "border border-base-200 text-ink-900"
        }`}
      >
        {message.text && <p className="whitespace-pre-wrap">{message.text}</p>}
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
