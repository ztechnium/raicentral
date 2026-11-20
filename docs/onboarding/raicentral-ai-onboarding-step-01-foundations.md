# RaiCentral AI Onboarding — Step 01: Community Foundations

> **Implementation work order for Claude.** Implement this step in the existing RaiCentral repository, including focused regression tests. Use this document as the specification. Complete the bounded change; do not respond with another implementation plan.

## 1. Goal and scope

**Goal:** make the existing Community onboarding reachable by authorized users, preserve explicit governance approvals, and distinguish introductory setup, recorded runtime activity, and control verification.

This is **Step 01 of implementation**, corresponding to **Phase 0 — Correct current Community onboarding behavior** in the AI onboarding roadmap. The next implementation step will introduce durable onboarding sessions, source evidence, and background jobs. File interpretation and AI generation come after those foundations.

The eventual product will accept organization documents, spreadsheets, PDFs, and a text prompt; classify the sources; propose AI systems, catalog resources, rules, and budget controls; and apply reviewed proposals through existing domain services. **This step prepares the current application for that workflow.**

### Repository baseline

| Item | Value |
| --- | --- |
| Repository | https://github.com/ztechnium/raicentral |
| Edition | RaiCentral Community Edition: one governance organization per installed instance |
| Reviewed branch | `main` |
| Reviewed commit | `0c052350db88c834e79c752284e264a68d7c6210` |
| Specification date | 19 November 2026 |
| Review method | Read-only source inspection; application tests were not executed when this work order was written |

Implement against the current working branch. Read its applicable `AGENTS.md` files first, inspect `git status`, and compare the relevant code with this baseline. Preserve unrelated work. Do not reset the repository to the reviewed commit. If a listed defect is already fixed, retain the fix and verify its behavior.

### Deliver in this step

1. Permission-aware entry into the existing welcome and guided onboarding screens.
2. Instance-scoped onboarding client functions and accurate response types.
3. A server-derived summary separating welcome completion, connection observations, and verification.
4. Draft-preserving creation and explicit, visible lifecycle transitions.
5. Focused regression tests and a short implementation note.

### Defer to later steps

Do not add document upload intake, PDF/DOCX parsing, OCR, spreadsheet inference, embeddings, LLM calls, agent orchestration, proposal generation, budget generation, bulk application, external connectors, or onboarding session/job tables. Do not introduce organization registration, organization switching, an `organizations` table, or `X-Organization-ID`.

Keep the current structured Catalog workbook import available. Do not label it as general AI document intake or broaden its accepted file formats in this change.

**Expected change size:** one cohesive, reviewable change. No database migration should be necessary for this specification.

## 2. Existing code to read first

Paths below are relative to the repository root. The source references at the end link to the reviewed revision.

| Area | Existing files | What to inspect |
| --- | --- | --- |
| Community constraints | `README.md`; `DEVELOPMENT.md`; `docs/releases/SYSTEM_STATUS.md` | Single-instance edition, supported setup and test commands |
| Welcome state and checklist | `src/rai_gateway/public_product/onboarding.py`; `src/rai_gateway/public_product/welcome.py` | `OnboardingService`, `build_checklist()`, `_compute_step_completion()`, `WelcomeService.complete()` |
| HTTP boundary | `src/rai_gateway/sdk/api.py` | Onboarding GET/PATCH, request body and existing permission dependencies; locate the welcome route handlers as well |
| Persisted setup and observations | `src/rai_gateway/models/productization.py` | `OrganizationOnboarding`, `SdkIntegration`, `SdkDecisionRecord` |
| Domain relationships | `src/rai_gateway/models/registry.py`; `src/rai_gateway/models/identity.py`; `src/rai_gateway/models/policy_engine.py` | System/agent/version/runtime relationships and actual policy rules |
| Lifecycle definitions | `src/rai_gateway/registry/domain.py` | Allowed system, agent, and version lifecycle states and transitions |
| Client contracts | `control-plane-ui/lib/public-api.ts` | Onboarding and welcome functions, obsolete organization arguments, response interfaces |
| Permissions | `control-plane-ui/providers/permission-provider.tsx`; `control-plane-ui/lib/auth-context.ts`; `control-plane-ui/lib/hooks/use-auth.ts` | Existing `/v1/auth/context` permission list and `usePermissions()` |
| Entry and routing | `control-plane-ui/lib/welcome-gate.ts`; `control-plane-ui/app/page.tsx`; `control-plane-ui/app/(app)/app-shell.tsx` | Login destination, platform-role bypass, welcome fetch and redirect handling |
| Setup pages | `control-plane-ui/app/(app)/welcome/page.tsx`; `control-plane-ui/app/(app)/app/onboarding/page.tsx`; `control-plane-ui/app/(app)/dashboard/page.tsx` | Setup entry, loading states, existing dashboard guidance |
| Setup components | `control-plane-ui/components/onboarding/welcome-wizard.tsx`; `control-plane-ui/components/onboarding/guided-onboarding.tsx`; `control-plane-ui/components/onboarding/catalog-upload.tsx` | Implicit approvals, completion handlers, polling, local progress, existing Catalog import |
| Existing tests | `tests/public_product/test_onboarding.py`; `tests/public_product/test_welcome.py`; `control-plane-ui/tests/unit/welcome-gate.test.ts` | Current fixtures and compatibility assertions |

Search for every caller before changing a shared function signature. Reuse existing services, UI components, API error handling, and domain permissions.

## 3. Target user experience

### 3.1 Entry and access

Use the dashboard as the normal post-login destination. Resolve onboarding capability inside the authenticated application, where the permission context is available. Avoid making login guess permissions from a platform-role flag.

| User/context | Expected behavior |
| --- | --- |
| User with `sdk:read` and `sdk:manage`, including the bootstrap administrator | Can open `/welcome` and `/app/onboarding`; sees a clear setup action on the dashboard |
| User with `sdk:read` only | Can view setup status; no setup mutation controls, automatic PATCHes, seeding, or credential creation |
| Platform-only user without product permissions | Can use authorized platform areas; opening setup shows a finite access-unavailable state and a dashboard link |
| Permission context is loading | Show a loading state; do not redirect or infer access |
| Permission or welcome fetch fails | Show a finite unavailable/error state with Retry; do not interpret the failure as incomplete onboarding |
| Welcome is already complete | Preserve the user's requested normal route and offer the existing onboarding page for follow-up work |

**Routing decision for this step:** setup is discoverable and optional to enter. Do not force every authenticated user through `/welcome`. Remove the platform-admin exclusion that prevents an authorized bootstrap administrator from opening it. Preserve the existing `/onboarding` alias to `/app/onboarding`.

Dashboard action labels:

- Welcome incomplete: **Set up RaiCentral** → `/welcome`.
- Welcome complete: **Continue setup** → `/app/onboarding`.
- Read-only access: **View setup status** → `/app/onboarding`.

Reuse the existing dashboard guidance area when possible. Do not add a nonfunctional “Upload with AI” button.

### 3.2 Welcome and manual creation

Keep the current Organization → Catalog → Implement structure. A user who creates a real AI system, agent, and version sees their actual returned lifecycle states. New records remain drafts under the existing domain defaults.

After creation, show **Draft created — review required before runtime setup** and link to the existing review/detail flow. The next screen must explain why runtime creation is blocked until the required approval has happened.

The Implement screen must describe the real sequence: review the version, create the runtime identity, then explicitly issue a credential. Remove copy that promises an immediately usable key simply because the welcome form was completed.

### 3.3 Guided onboarding and evidence

Keep the current System → Agent → Version → Runtime → Credential → Connect → Verify structure. Adjust its labels and status presentation to make these facts visible separately:

| Display | Meaning |
| --- | --- |
| Welcome complete | The introductory setup flow was finished; derived from `welcome_completed_at` |
| Checklist item recorded | A configuration record exists or a clearly labeled saved checklist flag exists; a legacy flag does not prove who completed it |
| Draft / Under review / Approved | The actual domain lifecycle value returned by the server |
| Runtime activity observed | A server observation belongs to the selected integration and runtime; display its timestamp |
| No runtime activity observed | The selected context is valid but no matching observation exists |
| Select an integration to check activity | The server cannot resolve a valid selected context |
| Verification not run | No scenario verification has been established by this onboarding flow |
| Deferred | The user chose to do a step later; this is a navigation preference |

“Connect later” must leave connection and verification incomplete. Reaching a later screen must not give earlier deferred steps green completion checks. A recorded heartbeat is historical activity; do not call it a current live connection or proof that controls work.

The final screen may say **Finish setup** or **Return to dashboard**. Finishing welcome/setup does not produce a “Production ready,” “Compliant,” or “Controls verified” badge.

## 4. Implementation sequence

### 4.1 Confirm the existing contracts

Before editing, identify the current request/response types, permission context, lifecycle actions, and every relevant call site. Record any material differences from the baseline in the completion report.

Keep the existing singleton `OrganizationOnboarding` record and its `singleton_key = "default"`. Keep `OrganizationGovernanceProfile` and existing domain relationships. There is no need for a new tenant identifier or onboarding persistence model in Step 01.

### 4.2 Make onboarding client functions instance-scoped

Update the touched functions in `control-plane-ui/lib/public-api.ts` to these signatures, preserving their existing bodies and return types where appropriate:

```ts
fetchOnboarding()
patchOnboarding(body)
fetchWelcome()
saveWelcomeProfile(body)
seedWelcomeTemplate(templateKey)
completeWelcome()
```

Remove the unused `organizationId` arguments from these functions and their callers. Remove unnecessary `organizationId` props from `WelcomeWizard` and `GuidedOnboarding`. Authentication still uses the existing access token and retry behavior.

Align `OnboardingResponse` and welcome types with the actual Community JSON. Remove required `organization_id` / `organization_status` fields where the server does not return them. Include the real welcome fields, checklist fields, and the additive summary specified below. Avoid `any` or fabricated placeholder IDs to satisfy type errors.

For the selected ID fields in PATCH (`first_ai_system_id`, `first_integration_id`, `first_trajectory_id`), define **omitted = preserve** and **explicit null = clear**. Use `string | null` for supplied values in the touched client types. The reviewed service ignores null IDs, so changing TypeScript alone is insufficient: update the route-to-service handoff to preserve which fields were actually supplied, and make the service distinguish an omitted argument from a clear. Do not use an `exclude_none` transformation that discards a requested clear. Existing callers that omit an ID must keep their current behavior. This needs no schema migration.

Keep broader `AuthProvider` and `OrganizationProvider` compatibility abstractions outside this refactor. If shared header helpers have other callers, update those callers deliberately or keep a compatibility wrapper without adding organization headers.

Retain valid selections stored under `rai_welcome_done_instance` and `rai_onboarding_wizard_instance`, or provide a small read-through migration if the implementation changes their names. Local storage is a navigation aid only. It cannot override newer server status, grant permissions, establish lifecycle approval, or certify runtime evidence. Interpret legacy `connectSkipped` only as a deferral.

### 4.3 Use exact permissions for entry and actions

Use the existing hook:

```ts
const { tenant, isLoading, refreshPermissions } = usePermissions();

const canReadSetup = tenant?.permissions.includes("sdk:read") ?? false;
const canManageSetup = tenant?.permissions.includes("sdk:manage") ?? false;
```

The exact list is important: the current provider may infer broad convenience flags after a context-fetch failure while leaving `permissions` empty. Do not use `tenant.canWrite`, a guessed role list, or `isPlatformAdmin` as authorization for setup mutation. There is no existing `useAuth().hasPermission()` API to call.

At the HTTP boundary, preserve the current `sdk:read` requirement for onboarding reads and `sdk:manage` requirement for onboarding writes. Keep every registry, policy, runtime, credential, and lifecycle operation behind its existing domain permission as well. Setup-management permission does not replace those domain checks.

Implement the access and routing behavior in Section 3.1. Update `welcome-gate.ts` and all affected login/AppShell callers together. Remove infinite loading based solely on a missing `activeOrganization`.

Read-only rendering must not issue mutation requests. In particular, remove or guard the mount-time PATCH of `first_ai_system_id` in guided onboarding. Persist a chosen system or integration only following an explicit authorized selection or successful creation action.

If distinguishing a failed permission request from a valid empty permission list requires a small optional error field in the existing context, add it with its tests. Do not redesign authentication or add a parallel permission service.

### 4.4 Add a truthful, read-only server summary

Extend the existing onboarding response with the following **new contract**. This object is proposed by this specification; it is not already present in the reviewed code.

```json
{
  "summary": {
    "welcome_complete": false,
    "connection": {
      "state": "UNSCOPED",
      "integration_id": null,
      "runtime_identity_id": null,
      "last_observed_at": null
    },
    "verification": {
      "state": "NOT_RUN"
    }
  }
}
```

Implement this as a computed projection in/alongside `OnboardingService`, returned consistently by onboarding GET and PATCH. Do not create a new table. It is read-only: `OnboardingPatchBody` must not accept client-supplied summary or evidence assertions as authoritative state.

Rules:

1. `welcome_complete` comes only from the persisted `welcome_completed_at` value.
2. `connection.state` is `UNSCOPED`, `NOT_OBSERVED`, or `OBSERVED`.
3. Resolve the saved `first_integration_id` through `SdkIntegration.runtime_identity_id` and the existing runtime → agent/version → AI system relationships. It must be consistent with the saved `first_ai_system_id`. Missing, deleted, or inconsistent selections produce `UNSCOPED`; do not choose the first available integration or trajectory.
4. A valid selected chain without a stored heartbeat produces `NOT_OBSERVED`. Return the resolved integration and runtime IDs; keep `last_observed_at` null.
5. A valid selected chain with a server-recorded `last_heartbeat_at` produces `OBSERVED`; return that timestamp. This is evidence of activity at a time, not a freshness or enforcement guarantee. In the `UNSCOPED` response, return null resolved IDs and timestamp rather than presenting a mismatched chain.
6. For this step, `verification.state` is always `NOT_RUN`. Heartbeats, a `SdkIntegration.verification_status` string, a trajectory, `connectSkipped`, legacy `READY`, or manually completed checklist items cannot change it.
7. Do not add a fake verification job or automatically execute a governed action to make the UI appear complete. The later verification phase will extend this contract with actual test evidence.

Render **Verification not run** from this summary. During a rolling upgrade, if the field is absent, render an unavailable/unknown status and offer refresh. Do not infer verification from legacy response fields.

### 4.5 Correct checklist provenance without deleting historical state

Preserve the existing 13 checklist IDs, response field names, persisted completed-step data, and legacy status enum. Existing `READY` records must remain readable. Treat these legacy fields as workflow history, never as a security or production assurance decision.

Add `completion_source` to each checklist item with values `NONE`, `MANUAL`, `RECORD`, or `OBSERVATION`. Give an actual record/observation precedence over a manual flag. Here `MANUAL` means a saved checklist completion flag, including historical flags that older polling code may have written automatically. Render it as **Marked complete**, not “Reviewed by user.” It does not identify an actor, prove human review, or provide an audit attestation. Its purpose is to make the basis of completion visible; it does not authorize anything.

Apply this behavior:

| Step category | Completion rule in the corrected response |
| --- | --- |
| Ordinary setup checklist items | Existing valid configuration records may establish `RECORD`; a permitted historical/manual checklist action may establish `MANUAL`, clearly labeled |
| `first_policies` | Computed record completion requires an unarchived `ContentRule` or `ToolRule`; join its `policy_collection_id` through `PolicyCollection.ai_system_id` to the selected system. A `ControlAssessment` does not establish policy configuration |
| `connect_application` | Only the scoped heartbeat observation above establishes `OBSERVATION`; ignore manual/legacy completion flags for this evidence claim |
| `first_governed_action` | Only an actual `SdkDecisionRecord` linked to the resolved selected integration establishes `OBSERVATION`; label this **Governed decision observed**. A decision elsewhere does not count, and a decision record does not prove a tool action executed |
| `inspect_trajectory` | A new explicit acknowledgment or retained historical flag can establish `MANUAL` / **Marked complete** only. Automatically finding a trajectory does not establish review, and a legacy flag cannot be attributed to a human |
| `test_connection` | No manual flag, heartbeat, or declared verification string establishes completion in this step; remain incomplete until a real verification result is supported |
| `production_readiness` | A stored `READY` value does not establish completion; remain incomplete until explicit readiness evidence is supported in a later phase |

A disabled rule may count as a recorded configuration object if it is unarchived, but never label it active or enforced. If the UI displays a policy-specific status, make disabled and enabled visible. Avoid activating rules as a side effect of checklist evaluation.

For record-based completion, resolve actual records; a saved UUID alone is insufficient. Scope system-specific records to the selected system where applicable. Organization profile and active membership roles remain installation-scoped checks.

Explicitly remove the old inference paths in `_compute_step_completion()`: `record.status == READY` cannot establish `production_readiness`; heartbeat or declared `verification_status` cannot establish `test_connection`; installation-wide decision counts cannot establish selected-system activity. Filtering manual flags alone would leave these incorrect paths in place.

Keep `progress` and `complete` consistent with the corrected `steps[].done`. The checklist cannot reach 13/13 while these verification steps are unsupported. Use welcome completion as the introductory exit condition; never require legacy `complete` to leave setup. Keep unavailable verification steps informational and choose `next_recommended_action` only from currently actionable work. Do not repeatedly direct users to a test the product cannot perform.

Recomputed checklist progress may decrease for existing installations because historical marks no longer establish evidence. Preserve the stored historical values and explain this corrected meaning in the implementation note. Completed installations must remain accessible. Do not synthesize 100% completion to make the introductory wizard finish.

Keep raw historical manual marks in storage for compatibility; they need not count in the corrected evidence steps. New UI actions must not send manual completion for the four evidence-only IDs above. A legacy PATCH containing those flags must not promote the summary or corrected evidence completion.

### 4.6 Fix guided onboarding handlers and polling

Inspect every completion path, not only the visible button:

1. Remove `items[0]` fallbacks from evidence resolution in `hydrateProgressFromOrg()`, initial saved trajectory loading, and connection polling. Exact runtime/context matching is required. A missing saved trajectory stays unavailable until a matching one is explicitly selected or observed.
2. When an authorized user changes system, integration, or runtime selection, clear incompatible dependent selections in both persisted and local progress, using the explicit-null PATCH contract. For example, changing runtime within the same AI system must clear the old integration and trajectory selections. Ignore late responses from the previous selection; use the existing cancellation/request-key pattern or a small equivalent. Apply a returned connection observation only when its runtime/integration IDs match the currently displayed selection, including while a selection save is pending.
3. Polling must be read-only. It may refresh matching observations; it must not call `markStep()` to simulate inspection, connection tests, or verification.
4. Split “Connect later” from completion. Store only deferral/navigation state in the existing wizard progress mechanism; do not PATCH `connect_application`, `test_connection`, `first_governed_action`, or `production_readiness` for a skip.
5. Make stepper presentation depend on explicit completion/deferred state. An index being less than the current screen index is insufficient for a green check.
6. Restrict `finishChecklist()`/generic “Mark finished” behavior to appropriate manual checklist actions. Use “Mark reviewed” for a real user acknowledgment; never use it to claim a technical test passed.
7. Replace the Verify `onFinish` path that writes `status: "READY"` or completes `production_readiness`. If introductory welcome has not been completed, use the existing authorized welcome-complete operation. Otherwise navigation alone is sufficient.
8. Present returned API failures visibly and preserve the user's valid selections. Do not convert a failed request into a successful local completion.

The reviewed guided wizard creates/selects runtime identities and credentials but does not itself select an `SdkIntegration`. Add a small explicit **existing integration selector** in Connect, filtered to the chosen runtime identity. Reuse `listSdkIntegrations`; when touching it, remove its unused organization argument and align its response type with Community JSON. A successful authorized selection saves `first_integration_id` together with the consistent selected system, then refreshes the server summary. Do not auto-create an integration or silently pick the first one. If none is available, keep `UNSCOPED`, explain that an integration must be linked before observations can be shown, and allow **Connect later**. Readers see the selected status without selection controls.

A failed or absent summary request is **Status unavailable / Retry** in the UI. Do not manufacture a successful `NOT_OBSERVED`, `welcome_complete: false`, or `NOT_RUN` assessment after a fetch failure.

Credential issuance remains an explicit authorized action. Retain the current one-time secret handling; do not persist plaintext keys in wizard progress or print them in logs/tests.

### 4.7 Preserve explicit governance lifecycle

In `welcome-wizard.tsx`, remove the implicit `approveSystem()` and `approveVersion()` helpers from the real manual-creation path, including their calls in `saveManual()`. Those helpers currently attempt review/approval and swallow failures.

In `guided-onboarding.tsx`, remove the attempt to transition a newly created AI system to `ACTIVE`. That is not an `AISystemStatus` value in the reviewed domain model.

Keep explicit lifecycle actions supported by the existing services. For example, the version screen already supports Submit for Review (`UNDER_REVIEW`), Return to Draft (`DRAFT`), and Approve Version (`APPROVED`). Only show/enable actions allowed by the current state and actual domain permission. Preserve backend authorization and transition validation.

After a transition, render the returned authoritative state. If it fails, show the server error and keep the previous state. Do not silently approve a version to unblock runtime creation. Preserve all existing runtime prerequisites, including approval of the bound version.

Make partial creation visible. If a system was created but creating its agent or version fails, preserve the created identifiers, explain what remains, and let the user resume/reuse the existing records. Do not report the entire setup as successful or blindly create another system on retry. Use the existing client/services; a new transaction framework is outside this step.

Explicit sample-template seeding is a separate existing flow. Keep it clearly labeled as sample/demo data and retain its existing semantics and tests. Do not apply its approval shortcuts to the user's manually created resources.

### 4.8 Update the small amount of supporting documentation

Add a concise repository note, using the existing documentation structure, covering the instance-scoped client contract, summary semantics, manual-vs-observed checklist completion, and draft/review flow. Explain that finishing welcome is distinct from runtime control verification.

State that durable intake sessions/evidence/jobs are the next implementation step. Do not mark PDF intake, AI classification, policy generation, or production verification as shipped.

## 5. Domain-model boundaries

| Concern | Existing domain/model to reuse | Step 01 responsibility |
| --- | --- | --- |
| Organization identity | `OrganizationGovernanceProfile` | Preserve the singleton profile |
| Setup progress | `OrganizationOnboarding` | Reuse existing fields; add computed response metadata |
| Governance access | Active membership and server permission context | Honor existing permission checks |
| AI inventory | `AISystem`, `Agent`, `AgentVersion` | Create drafts through existing services; preserve explicit review |
| Catalog | Existing resource models and structured workbook importer | Preserve present functionality and supported format |
| RAI policy configuration | `PolicyCollection`, `ContentRule`, `ToolRule` | Recognize actual configuration records; do not substitute assessments |
| Runtime selection | `AgentRuntimeIdentity`, `SdkIntegration` | Resolve the selected relationship chain |
| Observed governed activity | `SdkDecisionRecord` and existing trajectory data | Report matching activity with correct scope |
| Credentials | Existing runtime credential service | Preserve explicit issuance and secret handling |
| Verification | No new persistence in this step | Report `NOT_RUN` until later evidence-backed verification exists |

## 6. Focused regression coverage

Extend existing tests and add small behavior-focused tests where necessary. Reuse fixtures. Test observable outcomes and API calls rather than snapshotting every UI element.

### Backend: `tests/public_product/test_onboarding.py`

- GET still returns the existing 13 IDs and a correctly typed summary; PATCH returns the same projection contract.
- A legacy record with `status = "READY"` and every manual checklist flag does not produce verified/production-ready evidence.
- A heartbeat on the selected valid integration produces `OBSERVED` with its timestamp, but verification remains `NOT_RUN` and `test_connection` remains incomplete.
- A heartbeat or decision on a different integration/system does not satisfy the selected context. Missing/deleted/mismatched selected records produce `UNSCOPED` without crashing.
- A matching decision may establish `first_governed_action`, displayed as **Governed decision observed**; it cannot certify tool execution, control enforcement, or production readiness.
- A `ControlAssessment` alone does not establish computed `first_policies`; an unarchived rule in the selected system's collection does. A rule for a different system does not.
- Manual configuration marks are labeled `MANUAL`; legacy manual evidence marks cannot establish `OBSERVATION` or verification.
- Omitted selected ID fields preserve existing selections; explicit null clears them. A request that changes the system and clears dependent IDs is handled together without leaving incompatible saved evidence.
- Anonymous access remains rejected; a read-only principal cannot PATCH setup; obsolete `/v1/public/onboarding` routes remain unavailable.

### Backend: `tests/public_product/test_welcome.py`

- Welcome completion persists its timestamp and remains idempotent.
- Completing welcome does not create verification evidence, approve user-created resources, or alter runtime lifecycle state.
- Existing explicitly requested demo seeding and backfilled welcome behavior remain supported.

### Frontend

Update `control-plane-ui/tests/unit/welcome-gate.test.ts` to reflect the new routing policy. Add focused tests beside it for the touched components/helpers; proposed names such as `onboarding-summary.test.ts`, `guided-onboarding.test.tsx`, and `welcome-wizard.test.tsx` are new test files, not claims that those files already exist.

Cover these cases:

1. Bootstrap user with platform and setup permissions can open welcome.
2. A reader sees available status with no mutation controls or mount-time PATCH.
3. A platform-only user and a failed context fetch reach a finite state; loading does not redirect.
4. A completed installation stays on its requested normal route. Stale local welcome flags do not override server status.
5. Deferring connection changes navigation without completing evidence; the stepper renders Deferred distinctly.
6. Missing saved trajectory, wrong-runtime trajectory, and a late response after changing selection cannot certify the new selection.
7. Manual creation sends no implicit lifecycle transition requests; drafts remain drafts. A partial failure retains the created IDs for a safe retry.
8. Explicit version review still works; failed transitions show an error; runtime prerequisites remain enforced.
9. Legacy `READY`, all manual flags, and a missing new summary never render verified/production-ready status.
10. Finish setup uses the welcome-complete contract or navigation; it does not write `READY` or complete evidence-only IDs.
11. Existing integration selection is explicit, runtime-filtered, and permission-gated; no available integration produces `UNSCOPED` and permits deferral.
12. Welcome can finish while the verification checklist remains incomplete; unavailable verification is not an exit blocker or an endless next-action prompt.
13. Switching from runtime A to runtime B within the same system clears A's saved integration/trajectory selections; A's heartbeat does not appear as activity for B, including after refresh.

Use PostgreSQL-backed backend fixtures for database behavior. If a focused UI test mocks an API response, report it as a mocked UI test, not proof of backend enforcement.

## 7. Validation commands

Follow the current `DEVELOPMENT.md` and lockfiles. The reviewed UI package specifies Node.js 22+ and pnpm `9.15.0`; backend tests require PostgreSQL and use isolated schemas with Alembic migrations.

From the repository root, run the relevant focused checks:

```bash
uv run pytest tests/public_product/test_onboarding.py tests/public_product/test_welcome.py
pnpm --dir control-plane-ui test:unit tests/unit/welcome-gate.test.ts
pnpm --dir control-plane-ui typecheck
```

Also run the new/modified focused frontend test files through `pnpm --dir control-plane-ui test:unit <test-paths>`. Run the applicable repository lint/format checks on changed files. Use `TEST_DATABASE_URL` for the configured test database as documented; do not invent a SQLite replacement for PostgreSQL-specific behavior.

Manually verify the touched routes once against the running app when the environment supports it: bootstrap admin, read-only member, a completed installation, and deferred connection. Reuse the project's existing browser test tooling if available. Broader CI gates still apply when this change is submitted through the normal repository workflow.

If a command cannot run because a dependency, browser, or database is unavailable, report the exact command and blocker. Do not claim tests passed or weaken authorization/lifecycle behavior to bypass an environment problem.

## 8. Definition of done

- [ ] Authorized Community administrators, including the bootstrap administrator, can discover and open setup.
- [ ] Setup entry uses actual product permissions; normal users are not trapped in an administrator workflow.
- [ ] Loading, denied, and failed fetch states are finite and distinct from incomplete onboarding.
- [ ] Touched onboarding/welcome client methods and props are instance-scoped and accurately typed.
- [ ] Read-only page loads do not mutate setup state.
- [ ] New server summary separates welcome completion, selected runtime activity, and verification.
- [ ] Legacy manual flags, `READY`, and declared verification strings cannot establish technical evidence.
- [ ] Checklist record detection uses the correct domain entities and selected context.
- [ ] Deferral, navigation, completion, and observation have distinct UI states.
- [ ] Manual creation keeps drafts; explicit transitions surface errors and retain backend prerequisites.
- [ ] Existing sample seeding, Catalog workbook import, credentials, and completed installations remain usable within their existing permissions.
- [ ] Focused checks pass, or specific environmental blockers are accurately reported.
- [ ] No document parser, LLM integration, new organization model, session/job schema, or unsupported AI intake feature was introduced.

## 9. Claude completion report

Return a concise implementation report containing:

1. **Implemented behavior:** what changed for an administrator and a reader.
2. **Files changed:** grouped by backend, frontend, tests, and documentation.
3. **Contract changes:** the additive summary and completion provenance, plus any deliberately corrected legacy semantics.
4. **Validation:** exact commands, outcomes, and whether manual/UI checks used a live backend or mocks.
5. **Remaining limits:** verification remains `NOT_RUN`; document intake and AI generation are future work.
6. **Next step:** Step 02 — durable onboarding sessions, source evidence, and background jobs.

Stop after completing this implementation step. Do not begin Step 02 automatically. Follow the user's existing repository workflow for commits and review; this work order does not request a deployment or publication.

## 10. Source references

These are pinned inspection references. The current checkout and its applicable instructions take precedence if the implementation has evolved.

- [Community development and testing](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/DEVELOPMENT.md)
- [Onboarding service and checklist](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/public_product/onboarding.py)
- [Welcome service](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/public_product/welcome.py)
- [Onboarding API and permission dependencies](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/sdk/api.py)
- [Setup and SDK models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/productization.py)
- [Registry models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/registry.py)
- [Runtime identity models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/identity.py)
- [Policy collection and rule models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/policy_engine.py)
- [Lifecycle definitions](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/registry/domain.py)
- [Frontend API contracts](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/lib/public-api.ts)
- [Frontend permission provider](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/providers/permission-provider.tsx)
- [Welcome navigation gate](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/lib/welcome-gate.ts)
- [Welcome wizard](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/components/onboarding/welcome-wizard.tsx)
- [Guided onboarding](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/components/onboarding/guided-onboarding.tsx)
- [Backend onboarding tests](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/public_product/test_onboarding.py)
- [Backend welcome tests](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/public_product/test_welcome.py)
- [Frontend scripts and runtime requirements](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/package.json)
