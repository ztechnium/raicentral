"use client";

import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  analyzeInventory,
  listInventoryProposals,
  listProposalSets,
  newIdempotencyKey,
  previewInventory,
  type ProposalSummary,
  type SessionDetail,
} from "@/lib/onboarding-api";

/**
 * Step 04 inventory: explicit preview and analyze. Labels proposals as Proposed / Matched /
 * Needs clarification — never as registered production inventory.
 */
export function InventoryPanel({
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
  const [proposals, setProposals] = useState<ProposalSummary[]>([]);
  const extraction = session.extraction;
  const ready =
    extraction?.state === "EXTRACTED" &&
    extraction.coverage_acceptable &&
    extraction.run_id;

  const loadProposals = useCallback(async () => {
    const sets = await listProposalSets(session.id);
    if (!sets.items.length) {
      setProposals([]);
      return;
    }
    const latest = sets.items[0].id;
    const body = await listInventoryProposals(session.id, latest);
    setProposals(body.items);
  }, [session.id]);

  const runAnalyze = async () => {
    if (!ready || !extraction?.run_id) return;
    setBusy(true);
    setError(null);
    try {
      const preview = await previewInventory(session.id, {
        expected_row_version: session.row_version,
        requested_input_revision: session.input_revision,
        requested_clarification_revision: 0,
        extraction_run_id: extraction.run_id,
      });
      await analyzeInventory(session.id, {
        expected_row_version: session.row_version,
        requested_input_revision: session.input_revision,
        requested_clarification_revision: 0,
        plan_id: preview.plan_id,
        expected_plan_digest: preview.plan_digest,
        key: newIdempotencyKey(),
      });
      await onReload();
      await loadProposals();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Inventory analysis failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-4 rounded-lg border p-4">
      <div>
        <h2 className="text-lg font-semibold">Inventory</h2>
        <p className="text-sm text-muted-foreground">
          Analyze extracted sources to generate <strong>proposals</strong> in this workspace.
          Nothing here registers or enforces production controls.
        </p>
      </div>
      {!ready && (
        <p className="text-sm text-amber-700">
          Complete extraction with full coverage before inventory discovery.
        </p>
      )}
      {canManage && (
        <Button disabled={!ready || busy} onClick={runAnalyze}>
          {busy ? "Working…" : "Analyze inventory"}
        </Button>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {proposals.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No proposal set yet. After a successful analysis, proposed systems and catalog items
          appear here.
        </p>
      ) : (
        <ul className="divide-y text-sm">
          {proposals.map((p) => (
            <li key={p.id} className="py-2">
              <span className="font-medium">{p.entity_type}</span>
              <span className="ml-2 rounded bg-muted px-2 py-0.5">
                {p.match_state ?? "Proposed"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}