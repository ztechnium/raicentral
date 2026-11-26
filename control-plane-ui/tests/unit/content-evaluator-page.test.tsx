import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  getAISystems: vi.fn().mockResolvedValue({
    data: [
      {
        id: "sys-1",
        system_key: "payments",
        name: "Payments",
        status: "APPROVED",
        agent_count: 1,
      },
    ],
  }),
  testContentEvaluator: vi.fn().mockResolvedValue({
    data: {
      status: "COMPLETED",
      provider: "openai",
      model: "gpt-4o-mini",
      latency_ms: 42,
      label: "Content Evaluator Test — not persisted as production finding",
      evaluations: [
        {
          rule_id: "rule-1",
          violated: true,
          confidence: 0.92,
          category: "PII",
          reason: "Bank account number in the reply",
          severity: "HIGH",
          evidence_spans: ["EG12 3456 7890"],
        },
      ],
      intervention_hints: [
        {
          rule_id: "rule-1",
          suggested_intervention: "DENY",
          confidence: 0.92,
          severity: "HIGH",
          reason: "Bank account number in the reply",
        },
      ],
    },
  }),
}));

import ContentEvaluatorPage from "@/app/(app)/risk/content-evaluator/page";
import { testContentEvaluator } from "@/lib/api";

describe("ContentEvaluatorPage", () => {
  it("shows only the test bench and renders a structured test result", async () => {
    render(<ContentEvaluatorPage />);

    expect(await screen.findByLabelText("AI system")).toHaveValue("sys-1");
    expect(screen.queryByText("Provider configuration")).not.toBeInTheDocument();
    expect(screen.queryByText("Confidence thresholds")).not.toBeInTheDocument();
    expect(screen.queryByText("Runtime limits")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Run test" }));

    expect(await screen.findByText("Bank account number in the reply")).toBeInTheDocument();
    expect(screen.getByText("1 of 1 rules violated")).toBeInTheDocument();
    expect(screen.getByText("Deny")).toBeInTheDocument();
    expect(screen.getByText("EG12 3456 7890")).toBeInTheDocument();
    expect(vi.mocked(testContentEvaluator)).toHaveBeenCalledWith({
      ai_system_id: "sys-1",
      content: "Customer bank account is EG12 3456 7890",
      content_type: "OUTPUT",
    });
  });
});
