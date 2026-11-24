import { cn } from "@/lib/utils";

export function Card({
  title,
  value,
  tone = "neutral",
}: {
  title: string;
  value: string;
  tone?: "neutral" | "warn" | "danger";
}) {
  const toneStyles = {
    neutral: "text-ink-900",
    warn: "text-warn-700",
    danger: "text-danger-700",
  };

  return (
    <div className="rai-report-card border-l-4 border-l-accent-600 px-4 py-3">
      <p className="rai-eyebrow">{title}</p>
      <p className={cn("mt-1 font-mono text-lg font-semibold tracking-tight", toneStyles[tone])}>
        {value}
      </p>
    </div>
  );
}