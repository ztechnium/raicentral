"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SectionHeader } from "@/components/section-header";
import { Panel } from "@/components/registry/panel";
import { listCloudGovernanceProviders } from "@/lib/api";

type Provider = { id: string; name: string; status: string };

export default function IntegrationsCatalogPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listCloudGovernanceProviders()
      .then((res) => setProviders(res.data.providers))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Integrations"
        subtitle="Connect cloud providers for discovery and governance (read-only by default)."
      />
      {loading && <p className="text-sm text-ink-500">Loading providers…</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="grid gap-4 md:grid-cols-2">
        {providers.map((provider) => (
          <Panel key={provider.id} title={provider.name}>
            <p className="text-sm text-ink-600 mb-4 capitalize">
              Status: {provider.status.replace("_", " ")}
            </p>
            {provider.status === "available" && provider.id === "gcp" ? (
              <Link
                className="text-sm font-medium text-brand-600 hover:underline"
                href="/integrations/gcp"
              >
                Open GCP wizard →
              </Link>
            ) : (
              <p className="text-sm text-ink-500">Coming soon</p>
            )}
          </Panel>
        ))}
      </div>
    </div>
  );
}
