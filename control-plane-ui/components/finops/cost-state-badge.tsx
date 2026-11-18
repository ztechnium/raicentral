import { Badge } from "@/components/ui/badge";
import { COST_STATE_HELP, costStateLabel } from "@/lib/finops-format";

/** Cost state as text (never colour alone), with the definition available on hover/focus. */
export function CostStateBadge({ state }: { state?: string | null }) {
  const key = state ?? "UNAVAILABLE";
  const variant =
    key === "BILLED"
      ? "accent"
      : key === "UNAVAILABLE"
        ? "warn"
        : key === "ESTIMATED"
          ? "warn"
          : "neutral";
  return (
    <span title={COST_STATE_HELP[key] ?? COST_STATE_HELP.UNAVAILABLE}>
      <Badge variant={variant}>{costStateLabel(key)}</Badge>
    </span>
  );
}
