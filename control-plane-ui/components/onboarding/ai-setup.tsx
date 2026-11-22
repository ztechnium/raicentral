"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  IntakeApiError,
  cancelAITest,
  getAIConfiguration,
  listAITests,
  listProviderTargets,
  newIdempotencyKey,
  saveAIConfiguration,
  testAIConnection,
  type AIConfigDetail,
  type ProbeView,
  type ProviderTarget,
} from "@/lib/onboarding-api";
import { aiStateLabel, pollDelay, useIntakeAccess } from "@/lib/onboarding-intake";

type Load = { status: "loading" } | { status: "error"; message: string } | { status: "ready" };

/**
 * Screen D. Choosing an approved target/model is explicit; Save and Test are separate
 * actions; nothing runs on page load or save. Keys never appear here.
 */
export function AISetup() {
  const access = useIntakeAccess();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [config, setConfig] = useState<AIConfigDetail | null>(null);
  const [targets, setTargets] = useState<ProviderTarget[]>([]);
  const [tests, setTests] = useState<ProbeView[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [targetKey, setTargetKey] = useState("");
  const [model, setModel] = useState("");
  const [mode, setMode] = useState("");
  const [tokens, setTokens] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const testKey = useRef<string | null>(null);

  const hydrate = useCallback((c: AIConfigDetail) => {
    setConfig(c);
    setEnabled(c.enabled);
    setTargetKey(c.target_key ?? "");
    setModel(c.model ?? "");
    setMode(c.structured_mode ?? "");
    setTokens(c.max_output_tokens ? String(c.max_output_tokens) : "");
  }, []);

  const reload = useCallback(async () => {
    try {
      const c = await getAIConfiguration();
      hydrate(c);
      if (access.canProviders) {
        const [t, h] = await Promise.all([listProviderTargets(), listAITests()]);
        setTargets(t.items);
        setTests(h.items);
      }
      setLoad({ status: "ready" });
    } catch (e) {
      setLoad({
        status: "error",
        message: e instanceof Error ? e.message : "Could not load AI setup",
      });
    }
  }, [access.canProviders, hydrate]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Poll only while a test is pending; bounded backoff, cleaned up on unmount.
  const pending = config?.state === "TEST_PENDING";
  useEffect(() => {
    if (!pending) return;
    let cancelled = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const [c, h] = await Promise.all([getAIConfiguration(), listAITests()]);
        if (!cancelled) {
          setConfig(c);
          setTests(h.items);
        }
      } catch {
        // keep polling
      }
      attempt += 1;
      if (!cancelled) timer = setTimeout(() => void tick(), pollDelay(attempt));
    };
    timer = setTimeout(() => void tick(), pollDelay(0));
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pending]);

  const target = targets.find((t) => t.key === targetKey);

  async function save() {
    if (!config?.configuration_version) return;
    setBusy(true);
    setError(null);
    try {
      hydrate(
        await saveAIConfiguration({
          expected_configuration_version: config.configuration_version,
          enabled,
          target_key: targetKey || null,
          model: model || null,
          structured_mode: mode || null,
          max_output_tokens: tokens ? Number(tokens) : null,
        }),
      );
      testKey.current = null;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
      if (e instanceof IntakeApiError && e.code === "CONFIGURATION_CONFLICT") await reload();
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    if (!config?.configuration_version || !config.target_fingerprint) return;
    setBusy(true);
    setError(null);
    testKey.current ??= newIdempotencyKey();
    try {
      const result = await testAIConnection({
        expectedConfigurationVersion: config.configuration_version,
        expectedTargetFingerprint: config.target_fingerprint,
        key: testKey.current,
      });
      testKey.current = null;
      setConfig(result.ai_configuration);
      setTests((await listAITests()).items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the test");
      if (e instanceof IntakeApiError && e.status === 409) await reload();
    } finally {
      setBusy(false);
    }
  }

  async function cancel(probe: ProbeView) {
    setBusy(true);
    try {
      await cancelAITest(probe.id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not cancel");
    } finally {
      setBusy(false);
    }
  }

  if (load.status === "loading") return <p className="text-sm text-ink-500">Loading AI setup…</p>;
  if (load.status === "error") {
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p className="text-ink-700">{load.message}</p>
        <Button onClick={() => void reload()}>Retry</Button>
      </div>
    );
  }
  if (!config) return null;

  const last = config.last_test;
  const dirty =
    enabled !== config.enabled ||
    targetKey !== (config.target_key ?? "") ||
    model !== (config.model ?? "") ||
    mode !== (config.structured_mode ?? "") ||
    tokens !== (config.max_output_tokens ? String(config.max_output_tokens) : "");
  const activeProbe = tests.find((t) => t.state === "ACTIVE");
  const canTest =
    access.canProviders &&
    config.enabled &&
    !!config.target_fingerprint &&
    !dirty &&
    config.state !== "TEST_PENDING";

  return (
    <div className="space-y-6" data-testid="ai-setup">
      <header className="space-y-1">
        <Link href="/app/onboarding" className="text-xs text-accent-600">
          ← Onboarding
        </Link>
        <h1 className="text-lg font-semibold text-ink-900">AI connection</h1>
        <p className="text-sm text-ink-500">
          Needed later to generate proposals. Extracting and reading your documents works without
          it.
        </p>
      </header>

      <section aria-label="Status" className="rounded-xl border border-base-200 p-4 text-sm">
        <p className="font-medium text-ink-900" data-testid="ai-state">
          {aiStateLabel(config.state)}
        </p>
        {config.state === "TEST_STALE" && (
          <p className="text-xs text-ink-500">
            The configuration or deployment policy changed since the last test. Test again.
          </p>
        )}
        {config.state === "TEST_UNKNOWN" && (
          <p className="text-xs text-ink-500">
            The request may or may not have reached the provider. It is never resent automatically.
            Run a new test to find out.
          </p>
        )}
      </section>

      {!access.canProviders ? (
        <p className="rounded-lg border border-base-200 p-3 text-sm text-ink-600">
          Changing the AI connection needs provider-management access.
        </p>
      ) : targets.length === 0 ? (
        <p className="rounded-lg border border-base-200 p-3 text-sm text-ink-600" role="status">
          No approved AI targets are configured for this deployment. An operator must add one
          (ONBOARDING_AI_TARGETS_JSON) before you can choose a model.
        </p>
      ) : (
        <>
          <section aria-label="Configuration" className="space-y-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              Enable the AI connection
            </label>
            <label className="block text-sm">
              Approved target
              <select
                aria-label="Approved target"
                className="mt-1 block w-full rounded border border-base-200 p-1"
                value={targetKey}
                onChange={(e) => {
                  setTargetKey(e.target.value);
                  setModel("");
                  setMode("");
                }}
              >
                <option value="">Choose…</option>
                {targets.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            {target && (
              <>
                <p className="text-xs text-ink-500">
                  Destination <bdi>{target.destination_label}</bdi> ·{" "}
                  {target.credential_configured
                    ? "credential configured on this server"
                    : "credential not configured on this server"}
                </p>
                <label className="block text-sm">
                  Model
                  <select
                    aria-label="Model"
                    className="mt-1 block w-full rounded border border-base-200 p-1"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                  >
                    <option value="">Choose…</option>
                    {target.models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-sm">
                  Structured output
                  <select
                    aria-label="Structured output"
                    className="mt-1 block w-full rounded border border-base-200 p-1"
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    <option value="">Choose…</option>
                    {target.structured_modes.map((m) => (
                      <option key={m} value={m}>
                        {m === "JSON_SCHEMA" ? "JSON schema" : "JSON object"}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block text-sm">
                  Max output tokens (up to {target.max_output_tokens})
                  <input
                    aria-label="Max output tokens"
                    className="mt-1 block w-32 rounded border border-base-200 p-1"
                    inputMode="numeric"
                    value={tokens}
                    onChange={(e) => setTokens(e.target.value)}
                  />
                </label>
              </>
            )}
            <Button disabled={busy || !dirty} onClick={() => void save()}>
              Save configuration
            </Button>
            <p className="text-xs text-ink-500">Saving does not contact the provider.</p>
          </section>

          <section aria-label="Connection test" className="space-y-2">
            <Button variant="outline" disabled={busy || !canTest} onClick={() => void test()}>
              Test AI connection
            </Button>
            <p className="text-xs text-ink-500">
              Sends one short synthetic request and may use provider tokens. Your documents and
              prompts are not sent. It is never retried automatically.
            </p>
            {dirty && <p className="text-xs text-ink-500">Save your changes before testing.</p>}
            {activeProbe && (
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => void cancel(activeProbe)}
              >
                Cancel test
              </Button>
            )}
          </section>
        </>
      )}

      {error && (
        <p role="alert" className="text-sm text-danger-600">
          {error}
        </p>
      )}

      {access.canProviders && (
        <section aria-label="Test history" className="space-y-2">
          <h2 className="text-sm font-semibold text-ink-900">Last matching test</h2>
          {last ? (
            <p className="text-sm text-ink-700" data-testid="last-test">
              {last.outcome} · configuration version {last.configuration_version} · {last.model} ·{" "}
              {last.latency_ms != null ? `${last.latency_ms} ms` : "latency unknown"} ·{" "}
              {last.usage ? `${last.usage.total_tokens ?? "?"} tokens` : "usage unknown"}
              {last.error_code ? ` · ${last.error_code}` : ""}
            </p>
          ) : (
            <p className="text-sm text-ink-500">
              No test for the current configuration. Past tests below are history, not current
              availability.
            </p>
          )}
          <ul className="space-y-1 text-xs text-ink-500">
            {tests.map((t) => (
              <li key={t.id}>
                #{t.probe_number} · {t.state} · version {t.configuration_version} ·{" "}
                {t.ended_at ? new Date(t.ended_at).toLocaleString() : "in progress"}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
