import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { pricingStatus, verificationLabel } from "@/lib/finops-pricing";

const rows = [
  {
    id: "1",
    provider: "openai",
    model: "gpt-new",
    input_token_price: 1,
    output_token_price: 2,
    cached_input_token_price: null,
    pricing_unit: 1000,
    currency: "EUR",
    effective_from: "2026-01-01T00:00:00Z",
    effective_to: null,
    enabled: true,
    region: "eu",
    service_tier: "standard",
    verified_at: "2026-10-01T00:00:00Z",
    source_url: "https://example.test/pricing",
  },
  {
    id: "2",
    provider: "openai",
    model: "gpt-old",
    input_token_price: 9,
    output_token_price: 9,
    cached_input_token_price: null,
    pricing_unit: 1000,
    currency: "USD",
    effective_from: "2025-01-01T00:00:00Z",
    effective_to: "2025-12-31T00:00:00Z",
    enabled: true,
    region: null,
    service_tier: null,
    verified_at: null,
    source_url: null,
  },
];

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn((path: string) => {
    if (path === "/v1/finops/pricing") return Promise.resolve({ data: { items: rows } });
    if (path === "/v1/finops/pricing/cursor") return Promise.resolve({ data: null });
    if (path.startsWith("/v1/finops/breakdown")) {
      return Promise.resolve({
        data: {
          items: [
            { key: "m1", label: "mystery-model", calls: 7 },
            { key: null, label: "Not reported", calls: 2 },
          ],
        },
      });
    }
    return Promise.resolve({ data: { items: [] } });
  }),
  ApiError: class extends Error {},
}));

import FinOpsPricingPage from "@/app/(app)/finops/pricing/page";

describe("pricing lifecycle", () => {
  const now = new Date("2026-11-18T00:00:00Z");
  it("derives status from the effective window", () => {
    expect(pricingStatus({ enabled: true, effective_from: "2026-01-01T00:00:00Z" }, now)).toBe("Active");
    expect(
      pricingStatus({ enabled: true, effective_from: "2025-01-01T00:00:00Z", effective_to: "2025-12-31T00:00:00Z" }, now),
    ).toBe("Superseded");
    expect(pricingStatus({ enabled: true, effective_from: "2027-01-01T00:00:00Z" }, now)).toBe("Scheduled");
    expect(pricingStatus({ enabled: false, effective_from: "2026-01-01T00:00:00Z" }, now)).toBe("Disabled");
  });

  it("never implies verification that did not happen", () => {
    expect(verificationLabel(null)).toBe("Not verified");
    expect(verificationLabel("not-a-date")).toBe("Not verified");
    expect(verificationLabel("2026-10-01T00:00:00Z")).toBe("01/10/2026");
  });
});

describe("pricing catalog page", () => {
  it("shows region, tier, verification, currency per row and marks superseded versions", async () => {
    render(<FinOpsPricingPage />);
    const current = (await screen.findByText("gpt-new")).closest("tr") as HTMLElement;
    expect(current).toHaveTextContent("Active");
    expect(current).toHaveTextContent("eu / standard");
    expect(current).toHaveTextContent("EUR");
    expect(current).toHaveTextContent("01/10/2026");
    expect(current.querySelector("a")).toHaveAttribute("href", "https://example.test/pricing");

    const old = screen.getByText("gpt-old").closest("tr") as HTMLElement;
    expect(old).toHaveTextContent("Superseded");
    expect(old).toHaveTextContent("Not verified");
    expect(old).toHaveTextContent("USD");
  });

  it("lists models with usage but no price, from real metering", async () => {
    render(<FinOpsPricingPage />);
    const list = await screen.findByTestId("missing-rates");
    expect(list).toHaveTextContent("mystery-model");
    expect(list).toHaveTextContent("7 unpriced calls");
    expect(list).toHaveTextContent(/cannot be priced without a model name/);
    expect(screen.getByRole("link", { name: "Explore unpriced usage" })).toHaveAttribute(
      "href",
      "/finops/usage?dimension=model&cost_state=UNAVAILABLE",
    );
  });
});
