"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Eye } from "lucide-react";
import { SectionHeader } from "@/components/section-header";
import { Panel } from "@/components/registry/panel";
import { Table } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { FormField } from "@/components/registry/form-field";
import {
  createRiskBudget,
  getAISystems,
  getBudgetScopeOptions,
  getRiskBudgets,
  updateRiskBudget,
  type BudgetScopeOption,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AISystemSummary, RuntimeBudget } from "@/lib/types";

/** Window presets. `seconds` is sent for the ones the backend has no short code for. */
const WINDOWS = [
  { value: "5m", label: "5 minutes" },
  { value: "15m", label: "15 minutes" },
  { value: "1h", label: "1 hour" },
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days", seconds: 604800 },
  { value: "30d", label: "30 days", seconds: 2592000 },
] as const;

/** What "Applies to" can target. `specific` scopes pick a record from the AI system. */
const SCOPES = [
  { value: "AI_SYSTEM", label: "Whole AI system", noun: "" },
  { value: "ORGANIZATION", label: "Whole organization", noun: "" },
  { value: "AGENT", label: "A specific agent", noun: "agent" },
  { value: "AGENT_VERSION", label: "A specific agent version", noun: "agent version" },
  { value: "RUNTIME_IDENTITY", label: "A specific runtime identity", noun: "runtime identity" },
  { value: "DELEGATION", label: "A specific delegation", noun: "delegation" },
  { value: "MODEL", label: "A specific model", noun: "model" },
  { value: "TOOL", label: "A specific tool", noun: "tool" },
] as const;

type BudgetFormState = {
  ai_system_id: string;
  scope_type: string;
  scope_id: string;
  name: string;
  metric: string;
  tool_name: string;
  limit_value: string;
  window: string;
  effect_on_exceed: string;
  warning_threshold: string;
  amount_parameter_path: string;
  enabled: boolean;
};

const EMPTY_FORM: BudgetFormState = {
  ai_system_id: "",
  scope_type: "AI_SYSTEM",
  scope_id: "",
  name: "",
  metric: "ACTION_COUNT",
  tool_name: "",
  limit_value: "100",
  window: "1h",
  effect_on_exceed: "DENY",
  warning_threshold: "0.8",
  amount_parameter_path: "amount",
  enabled: true,
};

export default function RiskBudgetsPage() {
  const router = useRouter();
  const [items, setItems] = useState<RuntimeBudget[]>([]);
  const [systems, setSystems] = useState<AISystemSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<BudgetFormState>(EMPTY_FORM);
  const [scopeOptions, setScopeOptions] = useState<BudgetScopeOption[]>([]);
  const [toolOptions, setToolOptions] = useState<BudgetScopeOption[]>([]);

  const scopeMeta = SCOPES.find((s) => s.value === form.scope_type) ?? SCOPES[0];
  const needsRecord = Boolean(scopeMeta.noun);

  // Records of the chosen type that belong to the selected AI system (all, if none chosen).
  useEffect(() => {
    if (!showForm || !needsRecord) {
      setScopeOptions([]);
      return;
    }
    let cancelled = false;
    void getBudgetScopeOptions({
      scope_type: form.scope_type,
      ai_system_id: form.ai_system_id || undefined,
    })
      .then((res) => {
        if (!cancelled) setScopeOptions(res.data.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setScopeOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [showForm, needsRecord, form.scope_type, form.ai_system_id]);

  // Tool filter choices (tools used by the AI system, or all tools).
  useEffect(() => {
    if (!showForm) return;
    let cancelled = false;
    void getBudgetScopeOptions({
      scope_type: "TOOL",
      ai_system_id: form.ai_system_id || undefined,
    })
      .then((res) => {
        if (!cancelled) setToolOptions(res.data.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setToolOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [showForm, form.ai_system_id]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [budgets, sys] = await Promise.all([getRiskBudgets({ limit: 100 }), getAISystems()]);
      setItems(budgets.data.items ?? []);
      setSystems(Array.isArray(sys.data) ? sys.data : []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load budgets");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openCreate() {
    setForm(EMPTY_FORM);
    setShowForm(true);
  }

  function closeForm() {
    setShowForm(false);
    setForm(EMPTY_FORM);
  }

  async function onCreate() {
    if (!form.name.trim()) return;
    if (form.scope_type !== "ORGANIZATION" && !form.ai_system_id) {
      setError("Choose an AI system, or pick Whole organization.");
      return;
    }
    const limit = Number(form.limit_value);
    if (!Number.isFinite(limit) || limit <= 0) {
      setError("Limit must be a number greater than 0.");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const created = await createRiskBudget({
        ai_system_id: form.ai_system_id || undefined,
        scope_type: form.scope_type,
        scope_id: needsRecord && form.scope_id ? form.scope_id : undefined,
        name: form.name.trim(),
        metric: form.metric,
        tool_name: form.tool_name.trim() || undefined,
        limit_value: Number(form.limit_value),
        ...(WINDOWS.find((w) => w.value === form.window && "seconds" in w)
          ? {
              window_seconds: (WINDOWS.find((w) => w.value === form.window) as { seconds: number })
                .seconds,
            }
          : { window: form.window }),
        effect_on_exceed: form.effect_on_exceed,
        warning_threshold: Number(form.warning_threshold),
        amount_parameter_path:
          form.metric === "AMOUNT" ? form.amount_parameter_path.trim() || undefined : undefined,
        enabled: form.enabled,
      });
      closeForm();
      router.push(`/risk/budgets/${created.data.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create budget");
    } finally {
      setSaving(false);
    }
  }

  async function onToggleEnabled(budget: RuntimeBudget) {
    setBusyId(budget.id);
    try {
      await updateRiskBudget(budget.id, { enabled: !budget.enabled });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update budget status");
    } finally {
      setBusyId(null);
    }
  }

  const rows = items.map((b) => [
    <Link
      key={`${b.id}-name`}
      href={`/risk/budgets/${b.id}`}
      className="font-medium text-accent-600 underline"
    >
      {b.name}
    </Link>,
    b.scope_type,
    b.metric,
    b.tool_name || "—",
    b.limit_value,
    `${b.window_seconds}s`,
    <div key={`${b.id}-triggers`} className="text-xs">
      <div className="font-semibold text-ink-900">{b.trigger_count ?? 0}</div>
      <div className="text-muted-foreground">
        {b.warning_count ?? 0} warn · {b.exceeded_count ?? 0} exceed
      </div>
    </div>,
    <button
      key={`${b.id}-enabled`}
      type="button"
      role="switch"
      aria-checked={b.enabled}
      aria-label={b.enabled ? `Deactivate ${b.name}` : `Activate ${b.name}`}
      disabled={busyId === b.id}
      onClick={() => void onToggleEnabled(b)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition disabled:opacity-60",
        b.enabled ? "border-accent-500 bg-accent-600" : "border-base-200 bg-base-200",
      )}
    >
      <span
        className={cn(
          "inline-block size-3.5 rounded-full bg-white transition",
          b.enabled ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>,
    b.effect_on_exceed,
    <Link
      key={`${b.id}-details`}
      href={`/risk/budgets/${b.id}`}
      aria-label={`View details for ${b.name}`}
      className="inline-flex size-7 items-center justify-center rounded-md border border-base-200 text-ink-600 hover:bg-base-700"
    >
      <Eye className="size-4" aria-hidden />
    </Link>,
  ]);

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Spend controls"
        subtitle="Hierarchical limits on action count, tool usage, amounts, tokens, and cost. Parent and child budgets all apply — a child never replaces a parent."
        actions={
          <Button onClick={() => (showForm ? closeForm() : openCreate())}>
            {showForm ? "Cancel" : "Create spend control"}
          </Button>
        }
      />
      {error ? <p className="text-sm text-red-600">{error}</p> : null}
      {showForm && (
        <Panel title="New spend control">
          <p className="mb-3 text-sm text-ink-600">
            Step through what to control, where it applies (organization → AI system → agent →
            tenant), the limit, and what happens at the limit. Overlapping parent controls still
            apply.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <FormField label="Name" required>
                <Input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="e.g. Support agent token cap"
                />
              </FormField>
            </div>
            <FormField label="AI System" hint="Leave empty only for an organization-wide control.">
              <Select
                value={form.ai_system_id}
                onChange={(e) =>
                  setForm({ ...form, ai_system_id: e.target.value, scope_id: "", tool_name: "" })
                }
              >
                <option value="">Organization-wide…</option>
                {systems.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField
              label="Applies to"
              hint="Everything in the AI system, or one specific record."
            >
              <Select
                value={form.scope_type}
                onChange={(e) => setForm({ ...form, scope_type: e.target.value, scope_id: "" })}
              >
                {SCOPES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </Select>
            </FormField>
            {needsRecord ? (
              <div className="sm:col-span-2">
                <FormField
                  label={`Which ${scopeMeta.noun}?`}
                  hint={
                    scopeOptions.length === 0
                      ? `No ${scopeMeta.noun}s are linked to ${
                          form.ai_system_id ? "this AI system" : "the organization"
                        } yet.`
                      : form.ai_system_id
                        ? `Showing ${scopeMeta.noun}s related to the selected AI system.`
                        : `Showing all ${scopeMeta.noun}s.`
                  }
                >
                  <Select
                    value={form.scope_id}
                    onChange={(e) => setForm({ ...form, scope_id: e.target.value })}
                  >
                    <option value="">
                      Every {scopeMeta.noun} (each one gets its own limit)
                    </option>
                    {scopeOptions.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                        {o.detail ? ` — ${o.detail}` : ""}
                      </option>
                    ))}
                  </Select>
                </FormField>
              </div>
            ) : null}
            <FormField label="Metric">
              <Select
                value={form.metric}
                onChange={(e) => setForm({ ...form, metric: e.target.value })}
              >
                {["ACTION_COUNT", "TOOL_COUNT", "AMOUNT", "TOKEN_USAGE", "COST"].map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField
              label="Only count this tool (optional)"
              hint="Limits the count to calls of one tool. Leave on Any tool for everything."
            >
              <Select
                value={form.tool_name}
                onChange={(e) => setForm({ ...form, tool_name: e.target.value })}
              >
                <option value="">Any tool</option>
                {toolOptions.map((t) => (
                  <option key={t.value} value={t.tool_name ?? t.label}>
                    {t.label}
                    {t.detail ? ` — ${t.detail}` : ""}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField label="Limit" hint="Total allowed per window, in the metric's unit.">
              <Input
                value={form.limit_value}
                onChange={(e) => setForm({ ...form, limit_value: e.target.value })}
              />
            </FormField>
            <FormField
              label="Window"
              hint="How long usage is counted before the counter starts again."
            >
              <Select
                value={form.window}
                onChange={(e) => setForm({ ...form, window: e.target.value })}
              >
                {WINDOWS.map((w) => (
                  <option key={w.value} value={w.value}>
                    {w.label}
                  </option>
                ))}
              </Select>
            </FormField>
            <div className="rounded-md border border-base-200 bg-base-50 p-3 text-xs text-ink-600 sm:col-span-2">
              <p className="font-semibold text-ink-900">What the window does</p>
              <p className="mt-1">
                The limit is a rate, not a lifetime total. Usage is added up inside the current
                window, and when the window ends the counter resets to zero. With a limit of{" "}
                <strong>{form.limit_value || "N"}</strong> per{" "}
                <strong>{WINDOWS.find((w) => w.value === form.window)?.label ?? form.window}</strong>
                , the {form.metric.toLowerCase().replace("_", " ")} is capped at that amount in
                every {WINDOWS.find((w) => w.value === form.window)?.label ?? form.window} period.
                Short windows stop bursts and runaway loops; long windows (24 hours to 30 days)
                cap daily or monthly spend.
              </p>
            </div>
            <FormField label="Effect on exceed">
              <Select
                value={form.effect_on_exceed}
                onChange={(e) => setForm({ ...form, effect_on_exceed: e.target.value })}
              >
                {["MONITOR", "REQUIRE_APPROVAL", "DENY", "SUSPEND_RUNTIME_IDENTITY"].map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField label="Active">
              <Select
                value={form.enabled ? "true" : "false"}
                onChange={(e) => setForm({ ...form, enabled: e.target.value === "true" })}
              >
                <option value="true">Active</option>
                <option value="false">Inactive</option>
              </Select>
            </FormField>
            {form.metric === "AMOUNT" && (
              <FormField label="Amount parameter path">
                <Input
                  value={form.amount_parameter_path}
                  onChange={(e) => setForm({ ...form, amount_parameter_path: e.target.value })}
                />
              </FormField>
            )}
          </div>
          <div className="mt-4">
            <Button disabled={saving} onClick={() => void onCreate()}>
              {saving ? "Saving…" : "Save budget"}
            </Button>
          </div>
        </Panel>
      )}
      <Panel title="Active spend controls">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No spend controls yet. Create an organization or AI-system limit to govern cost, tokens,
            or action volume.
          </p>
        ) : (
          <Table
            columns={[
              "Name",
              "Scope",
              "Metric",
              "Resource",
              "Limit",
              "Window",
              "Triggers",
              "Active",
              "Effect",
              "",
            ]}
            rows={rows}
          />
        )}
      </Panel>
    </div>
  );
}
