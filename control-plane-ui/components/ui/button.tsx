import { cn } from "@/lib/utils";

export function Button({
  children,
  variant = "primary",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "outline" | "danger" | "default";
  size?: "sm" | "md" | "lg";
}) {
  const styles = {
    primary:
      "rai-btn border border-accent-700 bg-accent-600 text-white shadow-sm hover:bg-accent-500 active:bg-accent-700",
    default:
      "rai-btn border border-accent-700 bg-accent-600 text-white shadow-sm hover:bg-accent-500 active:bg-accent-700",
    secondary:
      "rai-btn border border-base-200 bg-surface text-ink-700 shadow-sm hover:bg-surface-hover active:bg-base-200",
    ghost: "rai-btn bg-transparent text-ink-600 hover:bg-surface-hover hover:text-ink-900",
    outline:
      "rai-btn border border-base-200 bg-surface text-ink-700 shadow-sm hover:border-accent-600/40 hover:bg-surface-hover",
    danger:
      "rai-btn border border-danger-700 bg-danger-600 text-white shadow-sm hover:bg-danger-500 active:bg-danger-700",
  };

  const sizes = {
    sm: "h-8 rounded-md px-3 text-xs",
    md: "h-9 rounded-md px-4 text-sm",
    lg: "h-10 rounded-md px-5 text-sm",
  };

  return (
    <button
      className={cn(styles[variant], sizes[size], className)}
      {...props}
    >
      {children}
    </button>
  );
}