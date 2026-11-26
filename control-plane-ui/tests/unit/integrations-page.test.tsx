import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  listAlertIntegrations: vi.fn().mockResolvedValue({
    data: {
      items: [
        {
          id: "alert-1",
          name: "Ops Slack",
          provider: "slack",
          event_types: ["enforcement.*"],
          config: { webhook_url: "https://hooks.slack.com/services/secret-path" },
          custom_headers: {},
          enabled: true,
          status: "ACTIVE",
          created_at: "2026-11-01T10:00:00Z",
          last_event_at: null,
        },
      ],
    },
  }),
  listWebhookEndpoints: vi.fn().mockResolvedValue({
    data: {
      items: [
        {
          id: "hook-1",
          name: "Audit sink",
          url: "https://sink.example.com/rai",
          event_types: ["approval.requested"],
          custom_headers: {},
          enabled: true,
          status: "ACTIVE",
          created_at: "2026-11-02T10:00:00Z",
          last_event_at: "2026-11-20T08:30:00Z",
          last_event_status: "DELIVERED",
        },
      ],
    },
  }),
  listSearchIntegrations: vi.fn().mockResolvedValue({ data: { items: [] } }),
  listWebhookDeliveries: vi.fn().mockResolvedValue({ data: { items: [] } }),
  getAlertIntegrationCatalog: vi.fn().mockResolvedValue({
    data: {
      providers: [
        {
          id: "slack",
          label: "Slack",
          config_fields: [{ key: "webhook_url", label: "Incoming webhook URL", type: "url" }],
        },
        {
          id: "telegram",
          label: "Telegram",
          config_fields: [
            { key: "bot_token", label: "Bot token", type: "secret" },
            { key: "chat_id", label: "Chat ID", type: "text" },
          ],
        },
      ],
    },
  }),
  getSearchIntegrationCatalog: vi.fn().mockResolvedValue({ data: { providers: [] } }),
  getAlertEventGroups: vi.fn().mockResolvedValue({
    data: {
      groups: [
        {
          id: "enforcement",
          label: "Enforcement",
          events: [{ type: "enforcement.*", label: "All enforcement" }],
        },
      ],
    },
  }),
  createAlertIntegration: vi.fn().mockResolvedValue({ data: {} }),
  createWebhookEndpoint: vi.fn().mockResolvedValue({ data: { secret: "whsec_test" } }),
  updateAlertIntegration: vi.fn().mockResolvedValue({ data: {} }),
  updateWebhookEndpoint: vi.fn().mockResolvedValue({ data: {} }),
  upsertElasticsearchIntegration: vi.fn(),
  testSearchIntegration: vi.fn(),
  deleteAlertIntegration: vi.fn(),
  deleteSearchIntegration: vi.fn(),
  rotateWebhookEndpoint: vi.fn(),
}));

import IntegrationsPage from "@/app/(app)/settings/integrations/page";
import {
  createAlertIntegration,
  createWebhookEndpoint,
  updateAlertIntegration,
  updateWebhookEndpoint,
} from "@/lib/api";

const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (role: "button" | "radio", name: string) =>
  fireEvent.click(screen.getByRole(role, { name }));

describe("IntegrationsPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists integrations and webhooks together without exposing secret URLs", async () => {
    render(<IntegrationsPage />);

    expect(await screen.findByText("Ops Slack")).toBeInTheDocument();
    expect(screen.getByText("Audit sink")).toBeInTheDocument();
    expect(screen.getByText("hooks.slack.com")).toBeInTheDocument();
    expect(screen.queryByText(/secret-path/)).not.toBeInTheDocument();
    expect(screen.getByText("Never")).toBeInTheDocument();
    expect(screen.getByText("DELIVERED")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("adds an integration picked from the grid", async () => {
    render(<IntegrationsPage />);
    await screen.findByText("Ops Slack");

    click("button", "Add integration");
    click("radio", "Telegram");
    type("Bot token", "123:abc");
    // The footer button submits the form; a missing field blocks the request.
    fireEvent.submit(screen.getByLabelText("Bot token").closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("Chat ID is required.");

    type("Chat ID", "-100");
    fireEvent.submit(screen.getByLabelText("Chat ID").closest("form")!);

    await waitFor(() =>
      expect(createAlertIntegration).toHaveBeenCalledWith({
        name: "Telegram alerts",
        provider: "telegram",
        event_types: ["enforcement.*", "risk.finding.created"],
        config: { bot_token: "123:abc", chat_id: "-100" },
        custom_headers: {},
        enabled: true,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("creates a custom webhook and shows the signing secret once", async () => {
    render(<IntegrationsPage />);
    await screen.findByText("Ops Slack");

    click("button", "Add integration");
    click("radio", "Custom webhook");
    type("HTTPS URL", "https://new.example.com/hook");
    fireEvent.submit(screen.getByLabelText("HTTPS URL").closest("form")!);

    expect(await screen.findByText("whsec_test")).toBeInTheDocument();
    expect(createWebhookEndpoint).toHaveBeenCalledWith(
      expect.objectContaining({ url: "https://new.example.com/hook" }),
    );
  });

  it("edits existing rows with their saved values", async () => {
    render(<IntegrationsPage />);
    await screen.findByText("Ops Slack");

    click("button", "Edit Ops Slack");
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Incoming webhook URL")).toHaveValue(
      "https://hooks.slack.com/services/secret-path",
    );
    type("Name", "Ops Slack EU");
    fireEvent.submit(screen.getByLabelText("Name").closest("form")!);
    await waitFor(() =>
      expect(updateAlertIntegration).toHaveBeenCalledWith(
        "alert-1",
        expect.objectContaining({ name: "Ops Slack EU", event_types: ["enforcement.*"] }),
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    click("button", "Edit Audit sink");
    expect(screen.getByLabelText("HTTPS URL")).toHaveValue("https://sink.example.com/rai");
    fireEvent.click(screen.getByLabelText("Active"));
    fireEvent.submit(screen.getByLabelText("HTTPS URL").closest("form")!);
    await waitFor(() =>
      expect(updateWebhookEndpoint).toHaveBeenCalledWith(
        "hook-1",
        expect.objectContaining({ enabled: false, event_types: ["approval.requested"] }),
      ),
    );
  });
});
