import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Panel({
  title,
  description,
  actions,
  children,
  className,
  "data-testid": dataTestId,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  "data-testid"?: string;
}) {
  const showHeader = Boolean(title || description || actions);

  return (
    <section
      className={cn("rai-report-card overflow-hidden", className)}
      data-testid={dataTestId}
    >
      {showHeader && (
        <header className="flex min-h-11 flex-wrap items-center justify-between gap-3 border-b border-base-200 bg-surface-muted/60 px-4">
          <div>
            {title && <h2 className="rai-section-title">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-ink-500">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}
