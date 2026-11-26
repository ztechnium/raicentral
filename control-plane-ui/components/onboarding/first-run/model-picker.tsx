"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  MODEL_NAME_PATTERN,
  modelKey,
  type CatalogModel,
  type FirstRunCatalog,
  type ModelRef,
  type Provider,
} from "@/lib/first-run";

/** Searchable multi-select of the models offered by the chosen providers. */
export function ModelPicker({
  id,
  catalog,
  providers,
  selected,
  onChange,
}: {
  id: string;
  catalog: FirstRunCatalog;
  providers: Provider[];
  selected: ModelRef[];
  onChange: (models: ModelRef[]) => void;
}) {
  const [query, setQuery] = useState("");

  if (!providers.length) {
    return <p className="text-xs text-ink-500">Select a provider to see its models.</p>;
  }

  const providerLabel = (value: Provider) =>
    catalog.providers.find((p) => p.value === value)?.label ?? value;
  const chosen = new Set(selected.map(modelKey));
  const offered = catalog.models.filter((m) => providers.includes(m.provider));
  const known = new Set(offered.map(modelKey));
  // Custom models stay in the list so they can be unticked like any other.
  const options: CatalogModel[] = [
    ...offered,
    ...selected.filter((m) => !known.has(modelKey(m))).map((m) => ({ ...m, label: m.model_name })),
  ];
  const needle = query.trim().toLowerCase();
  const visible = options.filter(
    (m) =>
      !needle ||
      m.model_name.toLowerCase().includes(needle) ||
      m.label.toLowerCase().includes(needle),
  );
  const custom = query.trim();
  const canAddCustom =
    MODEL_NAME_PATTERN.test(custom) &&
    !options.some((m) => m.model_name.toLowerCase() === custom.toLowerCase());

  function toggle(model: ModelRef) {
    const key = modelKey(model);
    onChange(
      chosen.has(key)
        ? selected.filter((m) => modelKey(m) !== key)
        : [...selected, { provider: model.provider, model_name: model.model_name }],
    );
  }

  return (
    <div className="space-y-2">
      <Input
        id={id}
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search models, or type a model ID"
        aria-label="Search models"
      />
      <ul
        className="max-h-52 divide-y divide-base-200 overflow-y-auto rounded-md border border-base-200"
        aria-label="Models"
      >
        {visible.map((model) => (
          <li key={modelKey(model)}>
            <label className="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-surface-hover">
              <input
                type="checkbox"
                checked={chosen.has(modelKey(model))}
                onChange={() => toggle(model)}
              />
              <span className="text-ink-900">{model.label}</span>
              <span className="font-mono text-xs text-ink-500">{model.model_name}</span>
              <span className="ml-auto text-xs text-ink-400">{providerLabel(model.provider)}</span>
            </label>
          </li>
        ))}
        {!visible.length ? (
          <li className="px-3 py-2 text-xs text-ink-500">No model matches your search.</li>
        ) : null}
      </ul>
      {canAddCustom ? (
        <div className="flex flex-wrap gap-2">
          {providers.map((provider) => (
            <button
              key={provider}
              type="button"
              className="inline-flex items-center gap-1 text-xs font-semibold text-accent-700 underline"
              onClick={() => {
                toggle({ provider, model_name: custom });
                setQuery("");
              }}
            >
              <Plus className="size-3" aria-hidden />
              Add “{custom}” as a {providerLabel(provider)} model
            </button>
          ))}
        </div>
      ) : null}
      {selected.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Selected models">
          {selected.map((model) => (
            <li
              key={modelKey(model)}
              className="inline-flex items-center gap-1 rounded-full border border-accent-600/40 bg-accent-50 px-2.5 py-1 font-mono text-xs text-accent-700"
            >
              {model.model_name}
              <button
                type="button"
                aria-label={`Remove ${model.model_name}`}
                onClick={() => toggle(model)}
              >
                <X className="size-3" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
