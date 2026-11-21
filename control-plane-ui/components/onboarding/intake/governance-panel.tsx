"use client";

import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  generateGovernance,
  listGovernanceProposals,
  listGovernanceSets,
  listProposalSets,
  newIdempotencyKey,
  previewGovernance,
  type GovernanceProposalSummary,
  type SessionDetail,
} from "@/lib/onboarding-api";

/**
 * Step 05 governance: explicit preview and generate. Proposals remain draft staging;
 * runtime verification is always not run in this step.
 */
export function GovernancePanel({
  session,
  canManage,
  onReload,
}: {
  session: SessionDetail;
  canManage: boolean;
  onReload: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposals, setProposals] = useState<GovernanceProposalSummary[]>([]);
  const extraction = session.extraction;
  const ready =
    extraction?.state === "EXTRACTED" &&
    extraction.coverage_acceptable &&
    extraction.run_id;

  const loadProposals = useCallback(async () => {
    const sets = await listGovernanceSets(session.id);
    if (!sets.items.length) {
      setProposals([]);
      return;
    }
    const latest = sets.items[0].id;
    const body = await listGovernanceProposals(session.id, latest);
    setProposals(body.items);
  }, [session.id]);

  const runGenerate = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const inventorySets = await listProposalSets(session.id);
      if (!inventorySets.items.length) {
        setError("Complete inventory discovery before governance generation.");
        return;
      }
      const inventorySetId = inventorySets.items[0].id;
      const preview = await previewGovernance(session.id, {
        expected_row_version: session.row_version,
        requested_input_revision: session.input_revision,
        requested_inventory_clarification_revision: 0,
        requested_governance_clarification_revision: 0,
        inventory_proposal_set_id: inventorySetId,
      });
      await generateGovernance(session.id, {
        expected_row_version: session.row_version,
        requested_input_revision: session.input_revision,
        requested_inventory_clarification_revision: 0,
        requested_governance_clarification_revision: 0,
        plan_id: preview.plan_id,
        expected_plan_digest: preview.plan_digest,
        key: newIdempotencyKey(),
      });
      await onReload();
      await loadProposals();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Governance generation failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-4 rounded-lg border p-4">
      <div>
        <h2 className="text-lg font-semibold">Governance proposals</h2>
        <p className="text-sm text-muted-foreground">
          Turn policy material into obligations, rules, budgets, and monitoring{" "}
          <strong>proposals</strong>. Runtime verification is not run here.
        </p>
      </div>
      {!ready && (
        <p className="text-sm text-amber-700">
          Complete extraction and inventory before governance generation.
        </p>
      )}
      {canManage && (
        <Button disabled={!ready || busy} onClick={runGenerate}>
          {busy ? "Working…" : "Generate governance proposals"}
        </Button>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {proposals.length === 0 ? (
        <p className="text-sm text-muted-foreground">No governance set yet.</p>
      ) : (
        <ul className="divide-y text-sm">
          {proposals.map((p) => (
            <li key={p.id} className="py-2">
              <span className="font-medium">{p.proposal_family}</span>
              <span className="ml-2 rounded bg-muted px-2 py-0.5">
                {p.match_state ?? "Proposed"}
              </span>
              <span className="ml-2 text-xs text-muted-foreground">
                Runtime verification: {p.runtime_verification ?? "NOT_RUN"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}