"use client";

import { Plus, Trash2, TriangleAlert } from "lucide-react";
import { APPROVER_ROLES, OPERATORS_BY_VALUE_TYPE } from "@/components/registry/tool-rule-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cleanName, type SystemDraft } from "@/lib/first-run";
import {
  newCondition,
  type ApproverRole,
  type AppliesTo,
  type BudgetMetric,
  type ConditionDraft,
  type ConditionValueType,
  type RuleDraft,
  type RuleEffect,
  type ToolOption,
  type ToolRuleDraft,
} from "@/lib/first-run-policies";

const EFFECTS: { value: RuleEffect; label: string }[] = [
  { value: "DENY", label: "Block" },
  { value: "REQUIRE_APPROVAL", label: "Require human approval" },
  { value: "MONITOR", label: "Allow and monitor" },
];

const METRICS: { value: BudgetMetric; label: string }[] = [
  { value: "ACTION_COUNT", label: "Requests" },
  { value: "TOKEN_USAGE", label: "Tokens" },
  { value: "COST", label: "Cost (USD)" },
];

const WINDOWS = [
  { value: 3600, label: "per hour" },
  { value: 86_400, label: "per day" },
  { value: 604_800, label: "per 7 days" },
  { value: 2_592_000, label: "per 30 days" },
];

// "in" and "not_in" need a list, which this compact editor does not offer.
const operators = (type: ConditionValueType) =>
  (OPERATORS_BY_VALUE_TYPE[type] ?? []).filter((o) =>
    [
      "eq",
      "neq",
      "gt",
      "gte",
      "lt",
      "lte",
      "contains",
      "not_contains",
      "exists",
      "not_exists",
    ].includes(o.value),
  );

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="block text-xs font-semibold text-ink-600">{label}</span>
      {children}
    </label>
  );
}

function EffectFields({
  effect,
  approverRole,
  onChange,
}: {
  effect: RuleEffect;
  /** Undefined for rules that do not name an approver. */
  approverRole?: ApproverRole;
  onChange: (patch: { effect?: RuleEffect; approverRole?: ApproverRole }) => void;
}) {
  return (
    <>
      <Field label="Effect">
        <Select
          aria-label="Effect"
          className="w-full"
          value={effect}
          onChange={(e) => onChange({ effect: e.target.value as RuleEffect })}
        >
          {EFFECTS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </Field>
      {effect === "REQUIRE_APPROVAL" && approverRole ? (
        <Field label="Approver">
          <Select
            aria-label="Approver"
            className="w-full"
            value={approverRole}
            onChange={(e) => onChange({ approverRole: e.target.value as ApproverRole })}
          >
            {APPROVER_ROLES.map((role) => (
              <option key={role.value} value={role.value}>
                {role.label}
              </option>
            ))}
          </Select>
        </Field>
      ) : null}
    </>
  );
}

function Conditions({
  rule,
  onChange,
}: {
  rule: ToolRuleDraft;
  onChange: (conditions: ConditionDraft[]) => void;
}) {
  const update = (id: string, patch: Partial<ConditionDraft>) =>
    onChange(rule.conditions.map((c) => (c.id === id ? { ...c, ...patch } : c)));

  return (
    <div className="space-y-2">
      <span className="block text-xs font-semibold text-ink-600">Conditions</span>
      {!rule.conditions.length ? (
        <p className="text-xs text-ink-500">No condition: applies to every call of the tool.</p>
      ) : null}
      {rule.conditions.map((condition, index) => (
        <div key={condition.id} className="flex flex-wrap items-center gap-2">
          <Input
            className="min-w-32 flex-1"
            aria-label={`Condition ${index + 1} parameter`}
            placeholder="Parameter, e.g. amount"
            value={condition.parameterPath}
            onChange={(e) => update(condition.id, { parameterPath: e.target.value })}
          />
          <Select
            aria-label={`Condition ${index + 1} type`}
            value={condition.valueType}
            onChange={(e) => {
              const valueType = e.target.value as ConditionValueType;
              update(condition.id, {
                valueType,
                operator: "eq",
                value: valueType === "boolean" ? "true" : "",
              });
            }}
          >
            <option value="string">Text</option>
            <option value="number">Number</option>
            <option value="boolean">Yes / no</option>
          </Select>
          <Select
            aria-label={`Condition ${index + 1} operator`}
            value={condition.operator}
            onChange={(e) => update(condition.id, { operator: e.target.value })}
          >
            {operators(condition.valueType).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
          {condition.operator === "exists" ||
          condition.operator === "not_exists" ? null : condition.valueType === "boolean" ? (
            <Select
              aria-label={`Condition ${index + 1} value`}
              value={condition.value}
              onChange={(e) => update(condition.id, { value: e.target.value })}
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </Select>
          ) : (
            <Input
              className="w-32"
              aria-label={`Condition ${index + 1} value`}
              placeholder="Value"
              value={condition.value}
              onChange={(e) => update(condition.id, { value: e.target.value })}
            />
          )}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Remove condition ${index + 1}`}
            onClick={() => onChange(rule.conditions.filter((c) => c.id !== condition.id))}
          >
            <Trash2 className="size-3.5" aria-hidden />
          </Button>
        </div>
      ))}
      {rule.conditions.length < 10 ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => onChange([...rule.conditions, newCondition()])}
        >
          <Plus className="size-3.5" aria-hidden />
          Add condition
        </Button>
      ) : null}
    </div>
  );
}

export function RuleCard({
  label,
  rule,
  systems,
  tools,
  documentTitle,
  onChange,
  onRemove,
}: {
  label: string;
  rule: RuleDraft;
  systems: SystemDraft[];
  tools: ToolOption[];
  documentTitle?: string;
  onChange: (rule: RuleDraft) => void;
  onRemove: () => void;
}) {
  const patch = (values: object) => onChange({ ...rule, ...values } as RuleDraft);
  const tool = rule.family === "tool" ? tools.find((t) => t.ref === rule.toolRef) : undefined;
  // A tool rule only makes sense for systems that use the tool.
  const selectable =
    rule.family === "tool" ? systems.filter((s) => tool?.systemIds.includes(s.id)) : systems;
  const groups = [...new Set(tools.map((t) => t.group))];

  return (
    <div
      role="group"
      aria-label={label}
      className="space-y-3 rounded-lg border border-base-200 bg-base-50 p-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="min-w-48 flex-1"
          aria-label="Rule title"
          placeholder="Rule title"
          maxLength={200}
          value={rule.title}
          onChange={(e) => patch({ title: e.target.value })}
        />
        <label className="flex items-center gap-1.5 text-xs font-semibold text-ink-700">
          <input
            type="checkbox"
            checked={rule.enabled}
            onChange={(e) => patch({ enabled: e.target.checked })}
          />
          Active
        </label>
        <Button type="button" variant="ghost" aria-label={`Remove ${label}`} onClick={onRemove}>
          <Trash2 className="size-4" aria-hidden />
        </Button>
      </div>

      {rule.family === "content" ? (
        <>
          <Field label="What the AI must not say or receive">
            <Textarea
              aria-label="What the AI must not say or receive"
              className="w-full"
              rows={3}
              maxLength={5000}
              value={rule.content}
              onChange={(e) => patch({ content: e.target.value })}
            />
          </Field>
          <div className="grid gap-2 sm:grid-cols-3">
            <Field label="Applies to">
              <Select
                aria-label="Applies to"
                className="w-full"
                value={rule.appliesTo}
                onChange={(e) => patch({ appliesTo: e.target.value as AppliesTo })}
              >
                <option value="BOTH">Input and output</option>
                <option value="INPUT">Input</option>
                <option value="OUTPUT">Output</option>
              </Select>
            </Field>
            <EffectFields effect={rule.effect} approverRole={rule.approverRole} onChange={patch} />
          </div>
        </>
      ) : null}

      {rule.family === "tool" ? (
        <>
          <div className="grid gap-2 sm:grid-cols-3">
            <Field label="Tool">
              <Select
                aria-label="Tool"
                className="w-full"
                value={tool?.ref ?? ""}
                onChange={(e) => {
                  const next = tools.find((t) => t.ref === e.target.value);
                  patch({ toolRef: next?.ref ?? null, systemIds: next?.systemIds ?? [] });
                }}
              >
                <option value="">Choose a tool</option>
                {groups.map((group) => (
                  <optgroup key={group} label={group}>
                    {tools
                      .filter((t) => t.group === group)
                      .map((t) => (
                        <option key={t.ref} value={t.ref}>
                          {t.name}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </Select>
            </Field>
            <EffectFields effect={rule.effect} approverRole={rule.approverRole} onChange={patch} />
          </div>
          <Conditions rule={rule} onChange={(conditions) => patch({ conditions })} />
        </>
      ) : null}

      {rule.family === "budget" ? (
        <div className="grid gap-2 sm:grid-cols-4">
          <Field label="Limit on">
            <Select
              aria-label="Limit on"
              className="w-full"
              value={rule.metric}
              onChange={(e) => patch({ metric: e.target.value as BudgetMetric })}
            >
              {METRICS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Limit">
            <Input
              aria-label="Limit"
              className="w-full"
              type="number"
              min="0"
              step="any"
              value={rule.limit}
              onChange={(e) => patch({ limit: e.target.value })}
            />
          </Field>
          <Field label="Window">
            <Select
              aria-label="Window"
              className="w-full"
              value={rule.windowSeconds}
              onChange={(e) => patch({ windowSeconds: Number(e.target.value) })}
            >
              {WINDOWS.some((w) => w.value === rule.windowSeconds) ? null : (
                <option value={rule.windowSeconds}>per {rule.windowSeconds} seconds</option>
              )}
              {WINDOWS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
          <EffectFields effect={rule.effect} onChange={patch} />
          {rule.metric !== "ACTION_COUNT" ? (
            <p className="text-xs text-ink-500 sm:col-span-4">
              Enforced where {rule.metric === "COST" ? "cost" : "tokens"} can be measured, such as
              model calls. Tool calls without a measurement are not blocked.
            </p>
          ) : null}
        </div>
      ) : null}

      <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <legend className="sr-only">AI systems</legend>
        <span className="text-ink-400" aria-hidden>
          AI systems
        </span>
        {selectable.map((system) => (
          <label key={system.id} className="flex items-center gap-1.5 text-ink-700">
            <input
              type="checkbox"
              checked={rule.systemIds.includes(system.id)}
              onChange={(e) =>
                patch({
                  systemIds: e.target.checked
                    ? [...rule.systemIds, system.id]
                    : rule.systemIds.filter((id) => id !== system.id),
                })
              }
            />
            {cleanName(system.name)}
          </label>
        ))}
        {!selectable.length ? (
          <span className="text-ink-500">
            {rule.family === "tool" ? "Choose a tool first" : "None"}
          </span>
        ) : null}
      </fieldset>

      {rule.quote ? (
        <blockquote className="border-l-2 border-base-300 pl-3 text-xs text-ink-500">
          “{rule.quote}”{documentTitle ? <span> — {documentTitle}</span> : null}
        </blockquote>
      ) : null}
      {rule.warnings.length ? (
        <ul className="space-y-1 text-xs text-warn-700">
          {rule.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-1.5">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {warning}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
