"use client";

import { useState } from "react";
import { LoaderCircle, Plus, Sparkles, X } from "lucide-react";
import { RuleCard } from "@/components/onboarding/first-run/rule-card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { newId, type Draft, type FirstRunCatalog, type FirstRunStatus } from "@/lib/first-run";
import {
  extractPolicyRules,
  mergeExtraction,
  newRule,
  toolOptions,
  type PolicyDocument,
  type PolicyNote,
  type RuleDraft,
  type RuleFamily,
} from "@/lib/first-run-policies";

const FILE_TYPES = ".pdf,.docx,.xlsx,.csv,.txt,.md,.markdown";
const MAX_FILES = 5;
// The server evaluates at most this many content rules per check.
const CONTENT_RULE_LIMIT = 20;

const GROUPS: { family: RuleFamily; title: string; add: string; help: string }[] = [
  {
    family: "content",
    title: "Content rules",
    add: "Add content rule",
    help: "What the AI may not say or receive.",
  },
  {
    family: "tool",
    title: "Tool rules",
    add: "Add tool rule",
    help: "Which tool calls are blocked or need approval.",
  },
  {
    family: "budget",
    title: "Budgets",
    add: "Add budget",
    help: "Limits on requests, tokens or cost.",
  },
];

export interface ExtractionFeedback {
  added: number;
  warnings: string[];
  notes: PolicyNote[];
}

export function PoliciesStep({
  status,
  catalog,
  draft,
  documents,
  feedback,
  onRules,
  onExtracted,
  onBusy,
}: {
  status: FirstRunStatus;
  catalog: FirstRunCatalog;
  draft: Draft;
  documents: PolicyDocument[];
  feedback: ExtractionFeedback | null;
  onRules: (rules: RuleDraft[]) => void;
  onExtracted: (
    rules: RuleDraft[],
    documents: PolicyDocument[],
    feedback: ExtractionFeedback,
  ) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tools = toolOptions(draft, catalog);
  const systemIds = draft.systems.map((s) => s.id);

  if (!status.can_add_policies) {
    return (
      <p className="rounded-lg border border-base-200 bg-base-50 p-4 text-sm text-ink-700">
        Your account cannot create policies. Finish setup, then ask a RAI officer or an organization
        admin to add rules.
      </p>
    );
  }

  async function extract() {
    setBusy(true);
    onBusy(true);
    setError(null);
    try {
      const { data } = await extractPolicyRules(draft, catalog, files, text);
      const merged = mergeExtraction(draft.rules, data, newId().slice(0, 8));
      onExtracted(merged.rules, merged.documents, {
        added: merged.added,
        warnings: data.warnings,
        notes: data.notes,
      });
      setFiles([]);
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Extraction failed.");
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }

  const contentPerSystem = draft.systems.map(
    (s) => draft.rules.filter((r) => r.family === "content" && r.systemIds.includes(s.id)).length,
  );

  return (
    <div className="space-y-6">
      {status.ai.configured ? (
        <section
          aria-label="Extract rules"
          className="space-y-3 rounded-xl border border-base-200 bg-base-50 p-4"
        >
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
              <Sparkles className="size-4 text-accent-600" aria-hidden />
              Draft rules from your policies
            </h2>
            <p className="text-xs text-ink-500">
              Upload files or write the rules. Their text is sent to your configured model (
              {status.ai.provider} · {status.ai.model}). You review everything before it is created.
            </p>
          </div>
          <label className="block space-y-1">
            <span className="block text-xs font-semibold text-ink-600">
              Policy files (PDF, Word, Excel, CSV, text; up to {MAX_FILES}, 10 MB each)
            </span>
            <input
              type="file"
              multiple
              accept={FILE_TYPES}
              disabled={busy}
              className="block w-full text-sm text-ink-700"
              onChange={(event) => {
                const chosen = Array.from(event.target.files ?? []);
                setFiles((current) => [...current, ...chosen].slice(0, MAX_FILES));
                event.target.value = "";
              }}
            />
          </label>
          {files.length ? (
            <ul className="flex flex-wrap gap-1.5" aria-label="Chosen files">
              {files.map((file, index) => (
                <li
                  key={`${file.name}-${index}`}
                  className="inline-flex items-center gap-1 rounded-full border border-base-300 bg-surface px-2.5 py-1 text-xs text-ink-700"
                >
                  {file.name}
                  <button
                    type="button"
                    aria-label={`Remove ${file.name}`}
                    disabled={busy}
                    onClick={() => setFiles(files.filter((_, i) => i !== index))}
                  >
                    <X className="size-3" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <Textarea
            className="w-full"
            rows={4}
            maxLength={50_000}
            disabled={busy}
            aria-label="Policy text"
            placeholder="e.g. Never share bank account numbers. Refunds over 500 need a business approver. At most 1000 requests per day."
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          {error ? (
            <p role="alert" className="text-sm text-danger-700">
              {error}
            </p>
          ) : null}
          <Button
            type="button"
            disabled={busy || !status.email_verified || (!files.length && !text.trim())}
            onClick={() => void extract()}
          >
            {busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : null}
            {busy ? "Reading the policy… this can take a minute" : "Extract rules"}
          </Button>
        </section>
      ) : (
        <div className="rounded-lg border border-warn-600/30 bg-warn-50 p-4 text-sm text-warn-700">
          <strong>AI extraction is off.</strong> Add your LLM credential (RAI_AI_*) to
          <code className="mx-1 font-mono">.env</code>and restart the API to draft rules from files
          or text. You can add rules manually below.
          {status.ai.problem ? <span className="block text-xs">{status.ai.problem}</span> : null}
        </div>
      )}

      {feedback ? (
        <div
          role="status"
          className="space-y-2 rounded-lg border border-base-200 bg-base-50 p-4 text-sm text-ink-700"
        >
          <p>
            {feedback.added
              ? `Drafted ${feedback.added} ${feedback.added === 1 ? "rule" : "rules"}. Check them below.`
              : "No new rules were found."}
          </p>
          {feedback.warnings.map((warning) => (
            <p key={warning} className="text-xs text-warn-700">
              {warning}
            </p>
          ))}
          {feedback.notes.length ? (
            <details className="text-xs">
              <summary className="cursor-pointer font-semibold">
                {feedback.notes.length} requirement(s) cannot be enforced as rules
              </summary>
              <ul className="mt-2 space-y-1 text-ink-500">
                {feedback.notes.map((note) => (
                  <li key={`${note.document}-${note.quote}`}>
                    {note.title}: “{note.quote}”
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}

      {/* Locked while extracting, so the result cannot overwrite edits made meanwhile. */}
      <fieldset disabled={busy} className="min-w-0 space-y-6">
        {GROUPS.map((group) => {
          const rules = draft.rules.filter((r) => r.family === group.family);
          const noTools = group.family === "tool" && !tools.length;
          return (
            <section key={group.family} aria-label={group.title} className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold text-ink-900">
                  {group.title} ({rules.length})
                </h2>
                <p className="text-xs text-ink-500">{group.help}</p>
              </div>
              {group.family === "content" && rules.length && !status.content_rules_enforced ? (
                <p className="text-xs text-warn-700">
                  No content evaluator is configured, so content rules are saved but not enforced
                  until you set RAI_AI_* or CONTENT_EVALUATOR_* in .env.
                </p>
              ) : null}
              {group.family === "content" &&
              contentPerSystem.some((n) => n > CONTENT_RULE_LIMIT) ? (
                <p className="text-xs text-warn-700">
                  Only {CONTENT_RULE_LIMIT} content rules per AI system are checked each time.
                  Combine or remove some.
                </p>
              ) : null}
              {rules.map((rule, index) => (
                <RuleCard
                  key={rule.id}
                  label={`${group.family} rule ${index + 1}`}
                  rule={rule}
                  systems={draft.systems}
                  tools={tools}
                  documentTitle={documents.find((d) => d.ref === rule.documentRef)?.title}
                  onChange={(next) =>
                    onRules(draft.rules.map((r) => (r.id === next.id ? next : r)))
                  }
                  onRemove={() => onRules(draft.rules.filter((r) => r.id !== rule.id))}
                />
              ))}
              {noTools ? (
                <p className="text-xs text-ink-500">
                  Add a resource with tools in step 2 to create tool rules.
                </p>
              ) : null}
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={noTools}
                onClick={() => onRules([...draft.rules, newRule(group.family, systemIds)])}
              >
                <Plus className="size-3.5" aria-hidden />
                {group.add}
              </Button>
            </section>
          );
        })}
      </fieldset>
    </div>
  );
}
