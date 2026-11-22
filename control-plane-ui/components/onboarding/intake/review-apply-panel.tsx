"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  applyChangeSet,
  getChangeSet,
  listChangeSets,
  listGovernanceProposals,
  listGovernanceSets,
  listInventoryProposals,
  listProposalSets,
  newIdempotencyKey,
  prepareChangeSet,
  type ChangeOperationSummary,
  type ChangeSetSummary,
  type SessionDetail,
} from "@/lib/onboarding-api";

/**
 * Step 06: immutable review and controlled application. Shows exact operations;
 * new controls remain disabled until activated elsewhere.
 */
export function ReviewApplyPanel({
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
  const [changeSet, setChangeSet] = useState<ChangeSetSummary | null>(null);
  const [applyHint, setApplyHint] = useState<string | null>(null);

  const refreshLatest = useCallback(async () => {
    const body = await listChangeSets(session.id);
    const latest = body.items.find((c) => c.status === "PREPARED" || c.status === "APPLIED");
    if (!latest) {
      setChangeSet(null);
      return;
    }
    setChangeSet(await getChangeSet(session.id, latest.id));
  }, [session.id]);

  useEffect(() => {
    void refreshLatest();
  }, [refreshLatest, session.row_version, session.input_revision]);

  const prepare = async () => {
    setBusy(true);
    setError(null);
    setApplyHint(null);
    try {
      const invSets = await listProposalSets(session.id);
      const govSets = await listGovernanceSets(session.id);
      if (!invSets.items.length) {
        setError("Run inventory analysis before preparing a review.");
        return;
      }
      const invSetId = invSets.items[0].id;
      const invProps = await listInventoryProposals(session.id, invSetId);
      const inventory: Record<string, "ACCEPT" | "SKIP"> = {};
      for (const p of invProps.items) {
        inventory[p.id] = "ACCEPT";
      }
      const governance: Record<string, "ACCEPT" | "SKIP"> = {};
      let governanceSetId: string | undefined;
      if (govSets.items.length) {
        governanceSetId = govSets.items[0].id;
        const govProps = await listGovernanceProposals(session.id, governanceSetId);
        for (const p of govProps.items) {
          governance[p.id] = "ACCEPT";
        }
      }
      const result = await prepareChangeSet(session.id, {
        expected_row_version: session.row_version,
        requested_input_revision: session.input_revision,
        selection: {
          inventory,
          governance,
          inventory_proposal_set_id: invSetId,
          governance_proposal_set_id: governanceSetId,
        },
        key: newIdempotencyKey(),
      });
      setChangeSet(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not prepare review");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!changeSet || changeSet.status !== "PREPARED") return;
    setBusy(true);
    setError(null);
    try {
      const body = await applyChangeSet(session.id, changeSet.id, {
        expected_row_version: session.row_version,
        requested_input_revision: session.input_revision,
        expected_payload_digest: changeSet.payload_digest,
        key: newIdempotencyKey(),
      });
      setApplyHint(
        `Application queued (${body.application_id}). Reload this page to follow progress.`,
      );
      await onReload();
      await refreshLatest();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Apply failed");
    } finally {
      setBusy(false);
    }
  };

  const counts = summarize(changeSet?.operations ?? []);

  return (
    <section className="space-y-4 rounded-lg border p-4">
      <div>
        <h2 className="text-lg font-semibold">Review &amp; apply</h2>
        <p className="text-sm text-muted-foreground">
          Review exactly what onboarding will register. New rules and budgets are created disabled;
          runtime verification has not run.
        </p>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {applyHint && <p className="text-sm text-ink-600">{applyHint}</p>}

      {!changeSet && (
        <Button disabled={!canManage || busy} onClick={() => void prepare()}>
          Prepare review
        </Button>
      )}

      {changeSet && (
        <div className="space-y-3 text-sm">
          <p>
            Change set <span className="font-mono text-xs">{changeSet.id.slice(0, 8)}…</span> ·{" "}
            {changeSet.status} · {counts.create} create · {counts.reuse} reuse · {counts.update}{" "}
            update
          </p>
          <ul className="max-h-64 space-y-2 overflow-y-auto rounded border p-2">
            {changeSet.operations.map((op) => (
              <li key={op.id} className="border-b border-ink-100 pb-2 last:border-0">
                <OperationRow op={op} />
              </li>
            ))}
          </ul>
          {changeSet.status === "PREPARED" && (
            <Button disabled={!canManage || busy} onClick={() => void apply()}>
              Apply reviewed changes
            </Button>
          )}
          {changeSet.status === "APPLIED" && (
            <p className="text-ink-600">Applied. Inspect registry and policy records for links.</p>
          )}
        </div>
      )}
    </section>
  );
}

function OperationRow({ op }: { op: ChangeOperationSummary }) {
  const action = op.action as Record<string, unknown> | undefined;
  const title =
    (action?.name as string) ||
    (action?.title as string) ||
    (action?.system_key as string) ||
    op.target_kind;
  return (
    <div>
      <span className="font-medium">
        {op.verb} {op.target_kind}
      </span>
      <span className="text-muted-foreground"> — {title}</span>
    </div>
  );
}

function summarize(ops: ChangeOperationSummary[]) {
  return ops.reduce(
    (acc, op) => {
      if (op.verb === "CREATE") acc.create += 1;
      else if (op.verb === "REUSE") acc.reuse += 1;
      else if (op.verb === "UPDATE") acc.update += 1;
      return acc;
    },
    { create: 0, reuse: 0, update: 0 },
  );
}