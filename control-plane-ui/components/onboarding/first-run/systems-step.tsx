"use client";

import { Plus, Trash2 } from "lucide-react";
import { ModelPicker } from "@/components/onboarding/first-run/model-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  agentName,
  cleanName,
  newSystem,
  versionName,
  type FirstRunCatalog,
  type Provider,
  type SystemDraft,
} from "@/lib/first-run";
import { cn } from "@/lib/utils";

export function SystemsStep({
  catalog,
  systems,
  onChange,
}: {
  catalog: FirstRunCatalog;
  systems: SystemDraft[];
  onChange: (systems: SystemDraft[]) => void;
}) {
  const update = (id: string, patch: Partial<SystemDraft>) =>
    onChange(systems.map((s) => (s.id === id ? { ...s, ...patch } : s)));

  function toggleProvider(system: SystemDraft, provider: Provider) {
    const on = system.providers.includes(provider);
    update(system.id, {
      providers: on
        ? system.providers.filter((p) => p !== provider)
        : [...system.providers, provider],
      models: on ? system.models.filter((m) => m.provider !== provider) : system.models,
    });
  }

  return (
    <div className="space-y-4">
      {systems.map((system, index) => (
        <section
          key={system.id}
          aria-label={`AI system ${index + 1}`}
          className="space-y-4 rounded-xl border border-base-200 bg-base-50 p-4"
        >
          <div className="flex items-end gap-2">
            <label className="flex-1 space-y-1" htmlFor={`name-${system.id}`}>
              <span className="text-sm font-semibold text-ink-900">AI system name</span>
              <Input
                id={`name-${system.id}`}
                value={system.name}
                maxLength={120}
                placeholder="e.g. Customer Support"
                onChange={(event) => update(system.id, { name: event.target.value })}
              />
            </label>
            {systems.length > 1 ? (
              <Button
                type="button"
                variant="ghost"
                aria-label={`Remove AI system ${index + 1}`}
                onClick={() => onChange(systems.filter((s) => s.id !== system.id))}
              >
                <Trash2 className="size-4" aria-hidden />
              </Button>
            ) : null}
          </div>

          {cleanName(system.name) ? (
            <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
              <dt className="text-ink-400">Agent</dt>
              <dd className="text-ink-700">{agentName(system.name)}</dd>
              <dt className="text-ink-400">First version</dt>
              <dd className="text-ink-700">{versionName(system.name)}</dd>
            </dl>
          ) : null}

          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-ink-900">Providers</legend>
            <div className="flex flex-wrap gap-2">
              {catalog.providers.map((provider) => {
                const on = system.providers.includes(provider.value);
                return (
                  <button
                    key={provider.value}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleProvider(system, provider.value)}
                    className={cn(
                      "rounded-md border px-3 py-1.5 text-sm font-semibold",
                      on
                        ? "border-accent-700 bg-accent-600 text-white"
                        : "border-base-200 bg-surface text-ink-700 hover:bg-surface-hover",
                    )}
                  >
                    {provider.label}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <div className="space-y-2">
            <div className="text-sm font-semibold text-ink-900">Allowed models</div>
            <ModelPicker
              id={`models-${system.id}`}
              catalog={catalog}
              providers={system.providers}
              selected={system.models}
              onChange={(models) => update(system.id, { models })}
            />
          </div>
        </section>
      ))}
      {systems.length < 20 ? (
        <Button
          type="button"
          variant="secondary"
          onClick={() => onChange([...systems, newSystem()])}
        >
          <Plus className="size-4" aria-hidden />
          Add another AI system
        </Button>
      ) : null}
    </div>
  );
}
