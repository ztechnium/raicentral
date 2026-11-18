import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FilterBar } from "@/components/finops/filter-bar";
import type { FinOpsFilterState } from "@/lib/finops-filters";

const base: FinOpsFilterState = { period: "30d" };

function setup(filters: FinOpsFilterState = base, extra: Partial<Parameters<typeof FilterBar>[0]> = {}) {
  const onChange = vi.fn();
  const onRefresh = vi.fn();
  render(<FilterBar filters={filters} errors={[]} onChange={onChange} onRefresh={onRefresh} {...extra} />);
  return { onChange, onRefresh };
}

describe("FinOps filter bar", () => {
  it("changes the period to a preset", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "7d" } });
    expect(onChange).toHaveBeenCalledWith({ period: "7d" });
  });

  it("rejects an inverted custom range and never applies it", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "custom" } });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-10" } });
    fireEvent.change(screen.getByLabelText("To (inclusive)"), { target: { value: "2026-11-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply range" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/end date must not be before/i);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("applies a valid custom range", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "custom" } });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText("To (inclusive)"), { target: { value: "2026-11-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply range" }));
    expect(onChange).toHaveBeenCalledWith({ period: "custom", from: "2026-11-01", to: "2026-11-10" });
  });

  it("applies text filters together and trims blanks", () => {
    const { onChange } = setup({ period: "30d", model: "old" });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: " openai " } });
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(onChange).toHaveBeenCalledWith({ period: "30d", provider: "openai" });
  });

  it("removes one active filter from its chip", () => {
    const { onChange } = setup({ period: "30d", model: "gpt-4o", provider: "openai" });
    fireEvent.click(screen.getByRole("button", { name: "Remove Model filter gpt-4o" }));
    expect(onChange).toHaveBeenCalledWith({ period: "30d", provider: "openai" });
  });

  it("offers a currency choice only when more than one currency is present", () => {
    setup(base, { currencies: ["USD"] });
    expect(screen.queryByLabelText("Currency")).not.toBeInTheDocument();
  });

  it("lets the user pick a currency when data spans several", () => {
    const { onChange } = setup(base, { currencies: ["EUR", "USD"] });
    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "EUR" } });
    expect(onChange).toHaveBeenCalledWith({ period: "30d", currency: "EUR" });
  });

  it("only offers cost states the backend supports", () => {
    setup();
    const options = Array.from((screen.getByLabelText("Cost state") as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual(["", "BILLED", "METERED", "ESTIMATED", "UNAVAILABLE"]);
  });

  it("surfaces URL problems instead of hiding them, and refreshes on request", () => {
    const { onRefresh } = setup(base, { errors: ["AI system filter ignored: not a valid id."] });
    expect(screen.getByRole("alert")).toHaveTextContent("AI system filter ignored");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalled();
  });
});
