import { redirect } from "next/navigation";

// Webhooks are managed together with the other integrations.
export default function WebhookSettingsPage() {
  redirect("/settings/integrations");
}
