"use client";

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";

/** Centered dialog. Closes on Escape, the close button, or a backdrop click. */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  if (!open || !mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <button
        type="button"
        aria-label="Close dialog"
        tabIndex={-1}
        className="absolute inset-0 bg-black/50"
        onClick={onClose}
      />
      <div className="relative flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-base-200 bg-base-50 shadow-2xl">
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-base-200 px-5 py-4">
          <div className="min-w-0 space-y-1">
            <h2 className="text-base font-semibold text-ink-900">{title}</h2>
            {description ? <p className="text-xs text-ink-500">{description}</p> : null}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label="Close"
            className="shrink-0 px-2 text-ink-400 hover:text-ink-900"
            onClick={onClose}
          >
            <span aria-hidden className="text-lg leading-none">
              ×
            </span>
          </Button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer ? (
          <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-base-200 px-5 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>,
    // Portal into the themed .console root so rai-* styles and theme tokens apply.
    (document.querySelector(".console") as HTMLElement | null) ?? document.body,
  );
}
