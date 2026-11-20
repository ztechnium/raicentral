# Onboarding Step 01 — Community foundations

Implementation note for Phase 0 of the [AI onboarding roadmap](RaiCentral%20Community%20AI%20Onboarding%20Implementation%20Phases.md). It describes what the existing welcome and guided onboarding screens now do and what they do not claim.

**Not shipped in this step:** durable onboarding sessions, source evidence, background jobs, document or PDF intake, AI classification, policy or budget generation, and control verification. Durable intake sessions, source evidence and jobs are the next implementation step.

## Instance-scoped client

Community governs one organization per installation. The onboarding and welcome client functions take no organization argument and send no organization header:

```ts
fetchOnboarding();
patchOnboarding(body);
fetchWelcome();
saveWelcomeProfile(body);
seedWelcomeTemplate(templateKey);
completeWelcome();
listSdkIntegrations();
```

`PATCH /v1/productization/onboarding` treats the selected-ID fields (`first_ai_system_id`, `first_integration_id`, `first_trajectory_id`) as **omitted = preserve, explicit `null` = clear**. A selection change sends the clears for its dependents in the same request.

## Entry and access

- Login always lands on `/dashboard`. Setup is discoverable and optional; nobody is forced through `/welcome`.
- Capability comes from the exact `permissions` list in `/v1/auth/context`: `sdk:read` to view setup, `sdk:manage` to change it. `canWrite` and `isPlatformAdmin` are not used.
- The permission context exposes an optional `error`. A failed fetch is "unknown" with Retry, never "no permission" and never "setup incomplete".
- Dashboard actions: **Set up RaiCentral** (welcome incomplete), **Continue setup** (welcome complete), **View setup status** (read-only).
- Read-only users see status and send no setup writes.

## Server summary

`GET` and `PATCH /v1/productization/onboarding` return an additive, read-only `summary`. It is computed from existing records; no table was added and the client cannot assert it.

| Field | Meaning |
| --- | --- |
| `welcome_complete` | `welcome_completed_at` is set. |
| `connection.state` | `UNSCOPED` (no valid saved system + integration chain), `NOT_OBSERVED` (valid chain, no heartbeat) or `OBSERVED` (valid chain with a recorded `last_heartbeat_at`). |
| `connection.integration_id`, `runtime_identity_id`, `last_observed_at` | Resolved only for a valid chain; null when `UNSCOPED`. |
| `verification.state` | Always `NOT_RUN` in this step. |

`OBSERVED` is a recorded timestamp, not a live connection, a freshness guarantee or proof that controls work. The saved integration must resolve through its runtime identity and agent to the saved AI system; the first available integration or trajectory is never substituted.

## Checklist provenance

The 13 step IDs, response fields and legacy status enum are unchanged. Each step adds `completion_source` (`NONE`, `MANUAL`, `RECORD`, `OBSERVATION`) and `actionable`.

- **Records** (`RECORD`): resolved from real records, scoped to the selected system where applicable. `first_policies` needs an unarchived `ContentRule` or `ToolRule` in that system's policy collection; a `ControlAssessment` does not count. A disabled rule counts as configuration but is never described as enforced.
- **Observations** (`OBSERVATION`): `connect_application` needs a heartbeat on the selected integration; `first_governed_action` needs an `SdkDecisionRecord` for it, shown as "Governed decision observed" (it does not prove a tool executed).
- **Marked complete** (`MANUAL`): a saved checklist flag. It identifies no actor and proves no review.
- `test_connection` and `production_readiness` stay incomplete and are informational (`actionable: false`). They are not offered as next actions and do not block leaving setup.
- A stored `status = READY`, manual flags for the four evidence-only steps (`connect_application`, `test_connection`, `first_governed_action`, `production_readiness`) and `SdkIntegration.verification_status` no longer establish anything.

Because those inferences were removed, `progress` and `complete` can read lower than before for an existing installation. Stored historical values are untouched, and a completed installation stays accessible: leaving setup depends on `welcome_complete`, not on `complete`. The returned `onboarding.status` is never `READY`.

## Draft and review flow

- Manual creation in the welcome wizard creates drafts and attempts no review, approval or `ACTIVE` transition. It shows "Draft created — review required before runtime setup" and links to the version.
- A partial failure keeps the created IDs and retries only what is missing.
- Runtime creation stays blocked until the bound version is approved through the existing, explicit Submit for Review / Approve Version actions. The server remains the authority on every transition.
- Sample-template seeding is a separate, explicitly labelled flow with its existing semantics.

## Guided onboarding

- Opening the page never writes. Polling is read-only and never marks a step.
- **Connect later** only records a deferral (shown as "Deferred") and leaves connection and verification incomplete.
- Connect has an explicit selector for an existing integration, filtered to the chosen runtime identity and available only with `sdk:manage`. Changing system or runtime clears the incompatible integration and trajectory selections, saved and local.
- Responses are applied only if they describe the integration and runtime currently on screen.
- **Finish setup** completes welcome through its own contract (or just navigates when already complete). It writes no `READY` and no evidence step, and it is not a "controls verified" result.

## Remaining limits

- Verification is always `NOT_RUN`; evidence-backed verification is a later phase.
- Browser storage (`rai_welcome_done_instance`, `rai_onboarding_wizard_instance`) remains a navigation aid. It cannot override server status, grant permission or certify evidence.

## Tests

Backend: `tests/public_product/test_onboarding.py`, `tests/public_product/test_welcome.py` (PostgreSQL-backed). Frontend: `welcome-gate`, `onboarding-summary`, `setup-access`, `guided-onboarding` and `welcome-wizard` under `control-plane-ui/tests/unit` (mocked UI tests; they show client behavior and API calls, not backend enforcement).
