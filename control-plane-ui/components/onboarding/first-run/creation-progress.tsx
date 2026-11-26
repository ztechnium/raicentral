"use client";

import { CircleAlert, CircleCheck, Circle, LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

export type StageStatus = "pending" | "running" | "done" | "failed";
export type StageId = "resources" | "systems";

export const STAGES: { id: StageId; label: string }[] = [
  { id: "resources", label: "Resource catalog" },
  { id: "systems", label: "AI systems, agents, versions, runtimes and credentials" },
];

const ICONS = {
  pending: <Circle className="size-5 text-ink-300" aria-hidden />,
  running: <LoaderCircle className="size-5 animate-spin text-accent-600" aria-hidden />,
  done: <CircleCheck className="size-5 text-accent-600" aria-hidden />,
  failed: <CircleAlert className="size-5 text-danger-600" aria-hidden />,
};

const STATUS_LABELS: Record<StageStatus, string> = {
  pending: "Waiting",
  running: "Creating",
  done: "Created",
  failed: "Failed",
};

export function CreationProgress({
  stages,
  error,
  onRetry,
  onBack,
}: {
  stages: Record<StageId, StageStatus>;
  error: string | null;
  onRetry: () => void;
  onBack: () => void;
}) {
  return (
    <div className="space-y-4">
      <ol className="space-y-3" aria-live="polite">
        {STAGES.map((stage) => (
          <li
            key={stage.id}
            data-testid={`stage-${stage.id}`}
            data-status={stages[stage.id]}
            className="flex items-center gap-3 rounded-lg border border-base-200 bg-base-50 p-4"
          >
            {ICONS[stages[stage.id]]}
            <span className="text-sm text-ink-900">{stage.label}</span>
            <span className="ml-auto text-xs text-ink-500">{STATUS_LABELS[stages[stage.id]]}</span>
          </li>
        ))}
      </ol>
      {error ? (
        <div
          role="alert"
          className="space-y-3 rounded-lg border border-danger-600/30 bg-danger-50 p-4"
        >
          <p className="text-sm text-danger-700">{error}</p>
          <p className="text-xs text-ink-500">
            The failed stage saved nothing. Stages marked Created are kept, and trying again will
            not duplicate them.
          </p>
          <div className="flex gap-2">
            <Button onClick={onRetry}>Try again</Button>
            <Button variant="secondary" onClick={onBack}>
              Back to edit
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
