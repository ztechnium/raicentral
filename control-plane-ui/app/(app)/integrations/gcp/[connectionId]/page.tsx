"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Panel } from "@/components/registry/panel";
import { SectionHeader } from "@/components/section-header";
import { getCloudConnectionAssets } from "@/lib/api";
import type { CloudDiscoveredAssetSummary } from "@/lib/types";

export default function GcpConnectionDashboardPage() {
  const params = useParams();
  const connectionId = String(params.connectionId);
  const [assets, setAssets] = useState<CloudDiscoveredAssetSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCloudConnectionAssets(connectionId)
      .then((res) => setAssets(res.data.assets))
      .catch((err: Error) => setError(err.message));
  }, [connectionId]);

  return (
    <div className="space-y-6">
      <SectionHeader title="GCP connection" description={`Connection ${connectionId}`} />
      <Panel title="Discovered resources">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {assets.length === 0 ? (
          <p className="text-sm text-ink-500">No assets stored yet. Run Discover from the wizard.</p>
        ) : (
          <ul className="divide-y text-sm">
            {assets.map((asset) => (
              <li key={asset.id} className="py-2">
                <div className="font-medium">{asset.display_name || asset.canonical_resource_name}</div>
                <div className="text-ink-600">
                  {asset.asset_type} · {asset.status}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Link href="/integrations/gcp" className="text-sm text-brand-600 hover:underline">
        ← GCP wizard
      </Link>
    </div>
  );
}
