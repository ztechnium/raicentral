import { render, screen } from "@testing-library/react";
import { GovernancePanel } from "@/components/onboarding/intake/governance-panel";

const session = {
  id: "sess-1",
  row_version: 1,
  input_revision: 0,
  extraction: { state: "EXTRACTED", coverage_acceptable: true, run_id: "run-1" },
} as const;

describe("GovernancePanel", () => {
  it("labels proposals without implying enforcement", () => {
    render(
      <GovernancePanel session={session as never} canManage={false} onReload={async () => {}} />,
    );
    expect(screen.getByText(/Runtime verification is not run here/i)).toBeInTheDocument();
    expect(screen.getByText(/Governance proposals/i)).toBeInTheDocument();
  });
});