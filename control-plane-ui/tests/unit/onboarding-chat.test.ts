import { describe, expect, it } from "vitest";
import { buildResults, isApplyConfirmation } from "@/lib/onboarding-chat";
import type { GovernanceProposalSummary, ProposalSummary } from "@/lib/onboarding-api";

const inv = (entity_type: string, claims: Record<string, unknown>, rel = {}): ProposalSummary => ({
  id: `${entity_type}-${String(claims.name ?? claims.model_name)}`,
  entity_type,
  generated: { claims, relationships: rel },
});

describe("isApplyConfirmation", () => {
  it.each(["apply", "Yes", "  confirm! ", "go ahead", "LGTM"])("accepts %s", (text) => {
    expect(isApplyConfirmation(text)).toBe(true);
  });

  it.each([
    "apply a refund limit of 500 to the support bot",
    "no",
    "change the budget",
    "",
  ])("treats %j as new instructions", (text) => {
    expect(isApplyConfirmation(text)).toBe(false);
  });
});

describe("buildResults", () => {
  it("nests agents under their system and keeps unattached agents separate", () => {
    const results = buildResults(
      [
        inv("ai_system", { system_key: "support-bot", name: "Support Bot", owner_name: "Dana" }),
        inv(
          "agent",
          { agent_key: "refund-agent", name: "Refund Agent" },
          { ai_system: { claimed_key: "support-bot" } },
        ),
        inv("agent", { agent_key: "loose", name: "Loose Agent" }),
        inv("model", { model_name: "gpt-4o", provider: "openai" }),
        inv("tool", { tool_key: "refund", name: "Refund" }),
      ],
      [],
    );
    expect(results.systems).toHaveLength(1);
    expect(results.systems[0].owner).toBe("Dana");
    expect(results.systems[0].agents.map((a) => a.key)).toEqual(["refund-agent"]);
    expect(results.unattachedAgents.map((a) => a.key)).toEqual(["loose"]);
    expect(results.models[0]).toMatchObject({ name: "gpt-4o", provider: "openai" });
    expect(results.resources[0]).toMatchObject({ kind: "tool", key: "refund" });
  });

  it("groups content, tool and budget rules and counts other governance", () => {
    const gov: GovernanceProposalSummary[] = [
      {
        id: "c",
        proposal_family: "content_rule",
        generated: { title: "No card numbers", effect: "DENY", system_key: "support-bot" },
      },
      {
        id: "t",
        proposal_family: "tool_rule",
        generated: { title: "Approve refunds", effect: "REQUIRE_APPROVAL", tool_selector: "refund" },
      },
      {
        id: "b",
        proposal_family: "runtime_budget",
        generated: { title: "Daily cap", metric: "COST", limit_value: 500, window_seconds: 86400 },
      },
      { id: "o", proposal_family: "obligation", generated: { title: "Keep logs" } },
    ];
    const results = buildResults([], gov);
    expect(results.rules.map((r) => r.family)).toEqual([
      "content_rule",
      "tool_rule",
      "runtime_budget",
    ]);
    expect(results.rules[1].tool).toBe("refund");
    expect(results.rules[2].budget).toBe("500 cost per day");
    expect(results.otherGovernance).toBe(1);
  });
});

import { isApplyIntent, wantsReanalysis } from "@/lib/onboarding-chat";

describe("isApplyIntent (a reviewed change is waiting)", () => {
  it.each([
    "can apply all this rules?",
    "Please approve and create these policies",
    "could you apply the changes now",
    "yes, register everything",
    "apply",
  ])("accepts %s", (text) => expect(isApplyIntent(text)).toBe(true));

  it.each([
    "apply a refund limit of 500 to the support bot",
    "create a rule that blocks send_email for Sales AI",
    "what rules did you find?",
    "do not apply the second rule",
    "",
  ])("treats %j as new instructions", (text) => expect(isApplyIntent(text)).toBe(false));
});

describe("wantsReanalysis", () => {
  it.each(["re-analyze the files", "analyze it again", "rerun", "start over"])("accepts %s", (t) =>
    expect(wantsReanalysis(t)).toBe(true),
  );
  it("ignores an apply request", () => expect(wantsReanalysis("can apply all this rules?")).toBe(false));
});
