import { cn } from "@/lib/utils";

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn("rai-field min-h-20 px-3 py-2", className)} {...props} />;
}