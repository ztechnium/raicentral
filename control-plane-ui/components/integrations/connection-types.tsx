import {
  Database,
  Gamepad2,
  ListChecks,
  MessageCircle,
  Send,
  Slack,
  SquareKanban,
  Trello,
  Users,
  Webhook,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  AlertIntegrationSummary,
  SearchIntegrationSummary,
  WebhookEndpointSummary,
} from "@/lib/types";

export const WEBHOOK_TYPE = "custom_webhook";
export const SEARCH_TYPE = "elasticsearch";

/** One row of the integrations table, whichever backend resource it comes from. */
export type Connection =
  | { kind: "alert"; item: AlertIntegrationSummary }
  | { kind: "webhook"; item: WebhookEndpointSummary }
  | { kind: "search"; item: SearchIntegrationSummary };

export function connectionType(connection: Connection) {
  if (connection.kind === "webhook") return WEBHOOK_TYPE;
  if (connection.kind === "search") return SEARCH_TYPE;
  return connection.item.provider;
}

// Display order of the picker grid.
const TYPES: Array<{ id: string; label: string; icon: LucideIcon; color: string }> = [
  { id: "slack", label: "Slack", icon: Slack, color: "#4A154B" },
  { id: "microsoft_teams", label: "Microsoft Teams", icon: Users, color: "#5059C9" },
  { id: "discord", label: "Discord", icon: Gamepad2, color: "#5865F2" },
  { id: "telegram", label: "Telegram", icon: Send, color: "#229ED9" },
  { id: "whatsapp", label: "WhatsApp", icon: MessageCircle, color: "#128C7E" },
  { id: "jira", label: "Jira", icon: SquareKanban, color: "#0052CC" },
  { id: "trello", label: "Trello", icon: Trello, color: "#0079BF" },
  { id: "clickup", label: "ClickUp", icon: ListChecks, color: "#7B68EE" },
  { id: WEBHOOK_TYPE, label: "Custom webhook", icon: Webhook, color: "#475569" },
  { id: SEARCH_TYPE, label: "Elasticsearch", icon: Database, color: "#00857C" },
];

export const TYPE_ORDER = TYPES.map((type) => type.id);

export function typeLabel(id: string) {
  return TYPES.find((type) => type.id === id)?.label ?? id;
}

export function TypeIcon({ type, className }: { type: string; className?: string }) {
  const meta = TYPES.find((item) => item.id === type);
  const Icon = meta?.icon ?? Webhook;
  return (
    <span
      aria-hidden
      className={cn("flex size-7 shrink-0 items-center justify-center rounded-md", className)}
      style={{ backgroundColor: meta?.color ?? "#475569", color: "#fff" }}
    >
      <Icon className="size-[60%]" />
    </span>
  );
}
