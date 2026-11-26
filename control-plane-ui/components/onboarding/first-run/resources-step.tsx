"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import {
  cleanName,
  newId,
  type CustomKind,
  type CustomResourceDraft,
  type FirstRunCatalog,
  type ResourceDraft,
  type SeedResourceDraft,
  type SystemDraft,
} from "@/lib/first-run";

const KIND_LABELS: Record<CustomKind, string> = {
  mcp_server: "MCP server",
  tool: "Tool",
  external_service: "External service",
  data_source: "Data source",
};

function UsedBy({
  resource,
  systems,
  onChange,
}: {
  resource: ResourceDraft;
  systems: SystemDraft[];
  onChange: (excludedSystems: string[]) => void;
}) {
  if (systems.length < 2) return null;
  return (
    <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
      <legend className="sr-only">Used by</legend>
      <span className="text-ink-400" aria-hidden>
        Used by
      </span>
      {systems.map((system) => (
        <label key={system.id} className="flex items-center gap-1.5 text-ink-700">
          <input
            type="checkbox"
            checked={!resource.excludedSystems.includes(system.id)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? resource.excludedSystems.filter((id) => id !== system.id)
                  : [...resource.excludedSystems, system.id],
              )
            }
          />
          {cleanName(system.name)}
        </label>
      ))}
    </fieldset>
  );
}

export function ResourcesStep({
  catalog,
  systems,
  resources,
  onChange,
}: {
  catalog: FirstRunCatalog;
  systems: SystemDraft[];
  resources: ResourceDraft[];
  onChange: (resources: ResourceDraft[]) => void;
}) {
  const [query, setQuery] = useState("");
  const seeds = resources.filter((r): r is SeedResourceDraft => r.type === "seed");
  const custom = resources.filter((r): r is CustomResourceDraft => r.type === "custom");
  const needle = query.trim().toLowerCase();
  const examples = catalog.resources.filter(
    (r) => !needle || `${r.name} ${r.category ?? ""} ${r.key}`.toLowerCase().includes(needle),
  );

  type Patch = Partial<Omit<SeedResourceDraft, "type">> &
    Partial<Omit<CustomResourceDraft, "type">>;
  const update = (id: string, patch: Patch) =>
    onChange(resources.map((r) => (r.id === id ? ({ ...r, ...patch } as ResourceDraft) : r)));
  const remove = (id: string) => onChange(resources.filter((r) => r.id !== id));

  function toggleSeed(key: string) {
    const existing = seeds.find((r) => r.key === key);
    if (existing) remove(existing.id);
    else {
      onChange([
        ...resources,
        { id: newId(), type: "seed", key, excludedTools: [], excludedSystems: [] },
      ]);
    }
  }

  return (
    <div className="space-y-6">
      <section aria-label="Example resources" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-ink-900">Example resources</h2>
          <Input
            type="search"
            className="max-w-xs"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search examples"
            aria-label="Search examples"
          />
        </div>
        <ul className="grid gap-2 sm:grid-cols-2">
          {examples.map((example) => {
            const selected = seeds.find((r) => r.key === example.key);
            const count = selected
              ? example.tools.filter((t) => !selected.excludedTools.includes(t.key)).length
              : example.tools.length;
            return (
              <li
                key={example.key}
                className="space-y-2 rounded-lg border border-base-200 bg-base-50 p-3"
              >
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={Boolean(selected)}
                    onChange={() => toggleSeed(example.key)}
                  />
                  <span>
                    <span className="block text-sm font-semibold text-ink-900">{example.name}</span>
                    <span className="block text-xs text-ink-500">
                      MCP server · {count} of {example.tools.length} tools
                    </span>
                  </span>
                </label>
                {selected ? (
                  <>
                    <details className="text-xs">
                      <summary className="cursor-pointer font-semibold text-accent-700">
                        Choose tools
                      </summary>
                      <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                        {example.tools.map((tool) => (
                          <li key={tool.key}>
                            <label className="flex items-center gap-2 text-ink-700">
                              <input
                                type="checkbox"
                                checked={!selected.excludedTools.includes(tool.key)}
                                onChange={(event) =>
                                  update(selected.id, {
                                    excludedTools: event.target.checked
                                      ? selected.excludedTools.filter((k) => k !== tool.key)
                                      : [...selected.excludedTools, tool.key],
                                  })
                                }
                              />
                              {tool.name}
                              <span className="ml-auto text-ink-400">{tool.risk}</span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    </details>
                    <UsedBy
                      resource={selected}
                      systems={systems}
                      onChange={(excludedSystems) => update(selected.id, { excludedSystems })}
                    />
                  </>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>

      <section aria-label="Your resources" className="space-y-3">
        <h2 className="text-sm font-semibold text-ink-900">Your resources</h2>
        {custom.map((resource, index) => (
          <div
            key={resource.id}
            role="group"
            aria-label={`Resource ${index + 1}`}
            className="space-y-2 rounded-lg border border-base-200 bg-base-50 p-3"
          >
            <div className="flex flex-wrap gap-2">
              <Select
                className="w-44"
                aria-label="Resource type"
                value={resource.kind}
                onChange={(event) =>
                  update(resource.id, { kind: event.target.value as CustomKind })
                }
              >
                {Object.entries(KIND_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
              <Input
                className="min-w-48 flex-1"
                aria-label="Resource name"
                placeholder="Name"
                maxLength={120}
                value={resource.name}
                onChange={(event) => update(resource.id, { name: event.target.value })}
              />
              <Button
                type="button"
                variant="ghost"
                aria-label={`Remove resource ${index + 1}`}
                onClick={() => remove(resource.id)}
              >
                <Trash2 className="size-4" aria-hidden />
              </Button>
            </div>
            <Input
              aria-label="Description"
              placeholder="Description (optional)"
              maxLength={1000}
              value={resource.description}
              onChange={(event) => update(resource.id, { description: event.target.value })}
            />
            {resource.kind === "mcp_server" || resource.kind === "external_service" ? (
              <Input
                aria-label="Endpoint"
                placeholder="Endpoint URL (optional)"
                maxLength={500}
                value={resource.endpoint}
                onChange={(event) => update(resource.id, { endpoint: event.target.value })}
              />
            ) : null}
            {resource.kind === "mcp_server" ? (
              <Input
                aria-label="Tool names"
                placeholder="Tool names, separated by commas"
                value={resource.tools}
                onChange={(event) => update(resource.id, { tools: event.target.value })}
              />
            ) : null}
            <UsedBy
              resource={resource}
              systems={systems}
              onChange={(excludedSystems) => update(resource.id, { excludedSystems })}
            />
          </div>
        ))}
        <Button
          type="button"
          variant="secondary"
          onClick={() =>
            onChange([
              ...resources,
              {
                id: newId(),
                type: "custom",
                kind: "mcp_server",
                name: "",
                description: "",
                endpoint: "",
                tools: "",
                excludedSystems: [],
              },
            ])
          }
        >
          <Plus className="size-4" aria-hidden />
          Add your own resource
        </Button>
      </section>
    </div>
  );
}
