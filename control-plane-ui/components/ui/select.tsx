import { cn } from "@/lib/utils";

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn("rai-field h-9 px-3 pr-8 text-ink-700", className)} {...props}>
      {children}
    </select>
  );
}