"use client";

import Image from "next/image";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CreationProgress,
  type StageId,
  type StageStatus,
} from "@/components/onboarding/first-run/creation-progress";
import { ResourcesStep } from "@/components/onboarding/first-run/resources-step";
import { ReviewStep } from "@/components/onboarding/first-run/review-step";
import { SetupSummary } from "@/components/onboarding/first-run/setup-summary";
import { SystemsStep } from "@/components/onboarding/first-run/systems-step";
import { Button } from "@/components/ui/button";
import { resendVerificationEmail } from "@/lib/api";
import {
  applyFirstRunResources,
  applyFirstRunSystems,
  buildResourcesRequest,
  buildSystemsRequest,
  clearDraft,
  getFirstRunCatalog,
  getFirstRunStatus,
  loadDraft,
  resourcesProblem,
  saveDraft,
  skipFirstRun,
  systemsProblem,
  type Draft,
  type FirstRunCatalog,
  type FirstRunStatus,
  type SystemsResult,
} from "@/lib/first-run";
import { useAuth } from "@/lib/hooks/use-auth";
import { completeWelcome } from "@/lib/public-api";
import { markWelcomeDoneLocally, welcomeDestination } from "@/lib/welcome-gate";
import { cn } from "@/lib/utils";

const STEPS = [
  { title: "AI systems", help: "Define your first AI systems and the models they may use." },
  { title: "Resources", help: "Select the tools and data your AI systems use. Optional." },
  { title: "Review", help: "Check what will be created." },
] as const;

const IDLE: Record<StageId, StageStatus> = { resources: "pending", systems: "pending" };

const message = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong.";

export function FirstRunWizard() {
  const router = useRouter();
  const { user } = useAuth();
  const [status, setStatus] = useState<FirstRunStatus | null>(null);
  const [catalog, setCatalog] = useState<FirstRunCatalog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [skipping, setSkipping] = useState(false);
  const [stages, setStages] = useState<Record<StageId, StageStatus> | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [result, setResult] = useState<SystemsResult | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const { data } = await getFirstRunStatus();
      const destination = welcomeDestination({
        welcomeCompleted: data.state === "COMPLETED",
        pathname: "/welcome",
      });
      if (destination) {
        router.replace(destination);
        return;
      }
      setStatus(data);
      if (data.can_setup) {
        const response = await getFirstRunCatalog();
        setCatalog(response.data);
        setDraft((current) => current ?? loadDraft());
      }
    } catch (err) {
      setLoadError(message(err));
    }
  }, [router]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (draft && !result) saveDraft(draft);
  }, [draft, result]);

  async function skip() {
    setSkipping(true);
    setError(null);
    try {
      await skipFirstRun();
      router.replace("/dashboard");
    } catch (err) {
      setError(message(err));
      setSkipping(false);
    }
  }

  async function resend() {
    if (!user?.email) return;
    try {
      await resendVerificationEmail(user.email);
      setNotice("Verification email sent.");
    } catch (err) {
      setNotice(message(err));
    }
  }

  function next() {
    if (!draft) return;
    const problem = step === 0 ? systemsProblem(draft.systems) : resourcesProblem(draft.resources);
    setError(problem);
    if (!problem) setStep(step + 1);
  }

  async function create() {
    if (!draft || !catalog) return;
    const problem = systemsProblem(draft.systems) ?? resourcesProblem(draft.resources);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setCreateError(null);
    let current: StageId = "resources";
    setStages({ resources: "running", systems: "pending" });
    try {
      const resources = await applyFirstRunResources(buildResourcesRequest(draft, catalog));
      current = "systems";
      setStages({ resources: "done", systems: "running" });
      const systems = await applyFirstRunSystems(buildSystemsRequest(draft, resources.data));
      setStages({ resources: "done", systems: "done" });
      setResult(systems.data);
      clearDraft();
    } catch (err) {
      setStages((previous) => ({ ...(previous ?? IDLE), [current]: "failed" }));
      setCreateError(message(err));
      return;
    }
    // The records exist; a failure to mark setup finished must not hide the secrets.
    try {
      await completeWelcome();
      markWelcomeDoneLocally();
    } catch {
      // The organization is no longer new, so nobody is sent back here.
    }
  }

  const creating = stages !== null && !result;
  const title = result ? "Setup complete" : creating ? "Creating your setup" : null;

  return (
    <div className="min-h-screen bg-base-100 text-ink-900" data-testid="first-run-wizard">
      <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:py-12">
        <div className="flex items-center gap-3">
          <Image src="/raicentral-logo.png" alt="" width={36} height={36} className="rounded-md" />
          <span className="text-base font-semibold tracking-tight">
            Rai<span className="text-accent-500">Central</span>
          </span>
          {!result ? (
            <Button
              variant="ghost"
              className="ml-auto"
              disabled={skipping || (creating && !createError)}
              onClick={() => void skip()}
            >
              {skipping ? "Skipping…" : "Skip for now"}
            </Button>
          ) : null}
        </div>

        {title ? (
          <h1 className="text-xl font-semibold">{title}</h1>
        ) : (
          <div className="space-y-3">
            <ol className="flex gap-2" aria-label="Setup steps">
              {STEPS.map((item, index) => (
                <li
                  key={item.title}
                  aria-current={index === step ? "step" : undefined}
                  className={cn(
                    "flex-1 border-t-2 pt-2 text-xs font-semibold",
                    index <= step
                      ? "border-accent-600 text-ink-900"
                      : "border-base-200 text-ink-400",
                  )}
                >
                  Step {index + 1} · {item.title}
                </li>
              ))}
            </ol>
            <h1 className="text-xl font-semibold">{STEPS[step].help}</h1>
          </div>
        )}

        {error ? (
          <p
            role="alert"
            className="rounded-lg border border-danger-600/30 bg-danger-50 p-3 text-sm text-danger-700"
          >
            {error}
          </p>
        ) : null}

        {loadError ? (
          <div role="alert" className="space-y-3">
            <p className="text-sm text-danger-700">Setup could not be loaded. {loadError}</p>
            <Button onClick={() => void load()}>Retry</Button>
          </div>
        ) : !status ? (
          <p className="text-sm text-ink-500">Loading setup…</p>
        ) : !status.can_setup ? (
          <div className="space-y-3">
            <p className="text-sm text-ink-500">
              Your account cannot run setup. Missing permission:{" "}
              {status.missing_permissions.join(", ")}.
            </p>
            <Button onClick={() => router.replace("/dashboard")}>Go to dashboard</Button>
          </div>
        ) : !catalog || !draft ? (
          <p className="text-sm text-ink-500">Loading setup…</p>
        ) : result ? (
          <SetupSummary result={result} onFinish={() => router.replace("/dashboard")} />
        ) : stages ? (
          <CreationProgress
            stages={stages}
            error={createError}
            onRetry={() => void create()}
            onBack={() => {
              setStages(null);
              setCreateError(null);
              setStep(0);
            }}
          />
        ) : (
          <>
            {!status.email_verified ? (
              <div className="rounded-lg border border-warn-600/30 bg-warn-50 p-3 text-sm text-warn-700">
                Verify your email before creating anything.{" "}
                <button
                  type="button"
                  className="font-semibold underline"
                  onClick={() => void resend()}
                >
                  Resend verification
                </button>{" "}
                <button
                  type="button"
                  className="font-semibold underline"
                  onClick={() => void load()}
                >
                  I have verified
                </button>
                {notice ? <span className="ml-2">{notice}</span> : null}
              </div>
            ) : null}

            {step === 0 ? (
              <SystemsStep
                catalog={catalog}
                systems={draft.systems}
                onChange={(systems) => setDraft({ ...draft, systems })}
              />
            ) : step === 1 ? (
              <ResourcesStep
                catalog={catalog}
                systems={draft.systems}
                resources={draft.resources}
                onChange={(resources) => setDraft({ ...draft, resources })}
              />
            ) : (
              <ReviewStep catalog={catalog} systems={draft.systems} resources={draft.resources} />
            )}

            <div className="flex justify-between gap-2 border-t border-base-200 pt-4">
              {step > 0 ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setError(null);
                    setStep(step - 1);
                  }}
                >
                  Back
                </Button>
              ) : (
                <span />
              )}
              {step < STEPS.length - 1 ? (
                <Button onClick={next}>Continue</Button>
              ) : (
                <Button disabled={!status.email_verified} onClick={() => void create()}>
                  Create
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
