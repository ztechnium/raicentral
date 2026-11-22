Build AI onboarding as a persistent, reviewable setup workspace for RaiCentral Community Edition. Administrators supply an organization brief and supported files; the system extracts evidence, reconciles existing records, proposes governance configuration, and applies reviewed changes through the existing services.

The first complete release should cover inventory, resource catalog, policy sources, content and tool rules, supported budgets, and connection verification. Preserve the Community Edition’s single-instance model and keep runtime verification separate from configuration generation.

Reviewed baseline: [ztechnium/raicentral at 0c05235](https://github.com/ztechnium/raicentral/commit/0c052350db88c834e79c752284e264a68d7c6210), main branch, 19 November 2026. Findings below come from source and test-code inspection. Runtime behavior and test results remain to be verified during implementation.

# Community scope and product decisions

RaiCentral Community Edition governs one organization per installation. The company profile is `OrganizationGovernanceProfile` with `singleton_key="default"`. The database has no `organizations` table or `organization_id` column, and product requests do not use `X-Organization-ID`. New onboarding sessions belong to the current installation and authenticated users. [Community README](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/README.md) · [Instance data model](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/docs/operations/TENANT_OWNERSHIP_MAP.md)

Use the existing Onboarding navigation and `/app/onboarding` entry. Consolidate the welcome experience into that journey, preserve manual setup, and allow administrators to return later to add systems or review changed policies. New contracts should avoid the frontend’s historical fake organization arguments; cleanup can stay limited to touched onboarding surfaces.

The Community console currently exposes AI Systems, Resource Catalog, Policy Library, RAI Rules, Runtime Identities, Trajectories, Approvals, Risk, FinOps, Team & roles, and related settings. Several broader governance and evaluation backend modules remain, but their standalone screens are excluded from this edition. Their presence in the repository is not a reason to expand the first onboarding release. [Current Community scope](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/docs/releases/SYSTEM_STATUS.md) · [Console navigation](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/lib/nav.ts)

Recommended defaults:

- One installation, many resumable onboarding sessions.

- Prompt, TXT, CSV, XLSX, text-based PDF and DOCX for the first supported input set.

- Scanned documents explicitly marked OCR required until the optional OCR processor is installed and tested.

- Explicitly selected processing provider, with only approved fallback destinations.

- Draft proposals during analysis; no registry or control changes from extraction.

- Review changes before applying them; enable selected controls only through an authorized activation action.

- Keep company onboarding separate from the customer-tenancy model of an integrated SaaS application. Existing delegation concepts can still govern customer scopes without creating RaiCentral organizations.

- Support Arabic and English evidence; preserve original text and distinguish any translation.

# Verified starting point

| Area | Current code | Implication for AI onboarding |
| - | - | - |
| Entry and progress | Welcome wizard, seven-stage guided setup, singleton OrganizationOnboarding | Extend the existing journey; add per-session and per-system progress. |
| Catalog import | Fixed XLSX Catalog sheet, active-sheet fallback, type/key/name headers, model/mcp/tool/service types, 2 MB and 500-row limits | Reuse normalization and domain validation. Build a separate analysis path for arbitrary documents and workbooks. |
| Policy Library | PolicySource, PolicySourceObligation and PolicyRuleLink | Reuse accepted policy destinations; implement actual parsing and obligation extraction. |
| Registry | Systems, agents, versions, resources, version dependencies and risk assessments | Reuse domain services and lifecycle rules. |
| Runtime rules | ContentRule, ToolRule, ToolRuleCondition | Generate typed, disabled configurations after review. |
| Budgets | RuntimeBudget, fixed time windows, hierarchical scope, reservations and reconciliation | Support exact existing semantics; identify calendar, currency and department-group gaps. |
| Providers | General LLM adapters and content-evaluator validation patterns | Add an onboarding-specific typed contract and explicit provider policy. |
| Jobs | BackgroundJob and JobQueue primitives | Add a real onboarding dispatcher, leases, checkpoints, cancellation and recovery. |
| Runtime evidence | Trajectories, decisions, receipts, metering | Use for actual connection and control checks; source uploads require separate evidence records. |

Evidence: [Guided setup](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/components/onboarding/guided-onboarding.tsx); [Catalog import](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/registry/catalog_import.py); [Policy Library service](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/policy/library.py); [Registry models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/registry.py); [Rule models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/policy_engine.py); [Risk and budget models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/risk_intelligence.py); [JobQueue](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/sdk/jobs.py); [Current worker](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/workers/runtime_events.py).

## Implementation constraints that change the plan

1. **The existing importer mutates immediately.** Catalog updates commit during processing, so Analyze files must never call the direct-import endpoint. It cannot provide preview or whole-batch rollback merely by being wrapped in another service. [Importer implementation](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/registry/catalog_import.py)

2. **Policy-source READY does not establish extraction.** The service stores supplied text or a reference; URLs receive metadata and manual text creates one draft obligation stub. General PDF/DOCX parsing and crawling are not performed there. The new source pipeline needs truthful processing states and extraction coverage. [Policy-source creation](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/policy/library.py)

3. **Current progress can overstate verification.** The checklist combines manual flags with computed conditions; heartbeat can satisfy test_connection. The guided UI permits connection deferral and manual READY. Keep those as progress, with verification derived from actual evidence. [Checklist service](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/public_product/onboarding.py)

4. **Existing creation shortcuts need to stay out of the AI flow.** Welcome setup attempts system/version approval transitions and catches errors; guided system creation attempts ACTIVE. Draft creation and actual lifecycle approvals must have separate, visible outcomes. [Welcome lifecycle helpers](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/components/onboarding/welcome-wizard.tsx) · [Guided creation](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/components/onboarding/guided-onboarding.tsx)

5. **Several control defaults are enabled.** ContentRule, ToolRule, RuntimeBudget and RiskMonitorRule default enabled=true. Catalog resources default ACTIVE. Keep unreviewed output in proposal tables, explicitly disable staged controls, and avoid treating resource registration as runtime authorization. [Rule defaults](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/policy_engine.py) · [Budget and monitor defaults](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/risk_intelligence.py)

6. **Provider fallback must be explicit.** The legacy \_provider_candidates function appends OpenAI as a terminal fallback even if another provider was selected. Onboarding must never inherit an implicit cloud destination for internal documents. Existing legacy settings persist key strings; use environment-held secret references initially rather than copying that pattern. [Provider fallback](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/llm_controller.py) · [Legacy settings persistence](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/provider_settings_store.py) · [Secret reference resolver](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/provider_gateway/credentials/secret_resolver.py)

7. **Jobs need an execution path.** JobQueue can enqueue/claim/retry jobs, but rai-worker currently runs approval expiry and webhook delivery. Add an onboarding worker command and deployment service; long LLM calls must not hold database row locks. [Queue primitives](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/sdk/jobs.py) · [Worker implementation](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/workers/runtime_events.py)

8. **Runtime evidence is a different domain.** EvidenceArtifact requires a trajectory. Store uploaded documents and source evidence independently; do not fabricate trajectories to reuse that table. Static assessment findings also remain separate from RuntimeRiskFinding. [Runtime evidence model](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/evidence.py) · [Static assessment models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/registry.py)

# Target user journey

| Workspace section | User outcome |
| - | - |
| Start or resume | Describe the goal, upload information, or continue a saved session. |
| Sources | Understand which files were read, which sheets/pages were covered, and what failed. |
| AI landscape | Confirm systems, owners, resources, existing-record matches and relationships. |
| Governance plan | Review source requirements and proposed rules, budgets, risks and monitoring. |
| Decisions needed | Resolve important ambiguity with evidence and a clear explanation of impact. |
| Review changes | Compare current and proposed values, approve selected changes, or defer them. |
| Results | Inspect actual creations/updates, partial failures and remaining work. |
| Connect and verify | Connect the intended runtime and test selected controls with recorded evidence. |

Use a persistent right-side evidence/assistant drawer and a stable action bar. Conversational edits update visible proposals. Users can revisit sections and leave work pending.

Display independent status tracks: configuration proposed/reviewed/applied; connection pending/connected; control verification untested/passed/failed. A completed manual checklist or a successful heartbeat does not advance the control-verification track.

# Proposed architecture and contracts

Use a dedicated `src/rai_gateway/onboarding/` feature package for orchestration, parsing, evidence, proposal compilation, review and application adapters. Preserve `public_product/onboarding.py` as the compatibility/progress summary while the new session workflow becomes authoritative for its own state.

Suggested new models are implementation proposals, not existing repository entities:

| Record | Essential data |
| - | - |
| OnboardingSession | Objective, initiating user, selected system scope, status, baseline fingerprints, current revision |
| OnboardingSource and source revision | Original storage reference, digest, format, source status, authority metadata, extraction coverage |
| OnboardingAnalysisRun | Source revisions, parser/model/prompt/schema versions, selected provider, stage checkpoints, usage, errors |
| OnboardingEvidenceRef | Source revision, page/sheet/cells/paragraph locator, excerpt and digest |
| OnboardingAssertion | Subject, field/value, units, origin, evidence references, uncertainty and conflict state |
| OnboardingProposal | Operation, target type/ID, before and after values, dependencies, validation, capability status |
| OnboardingDecision | Reviewer, decision or answer, reason, exact proposal/change-set revision |
| OnboardingApplyRun and operation ledger | Stable operation ID, payload digest, result ID, status, attempt history and recovery state |

No new record requires organization_id for this edition. Source permissions still matter within the installation; uploaded policies should not become accessible to every member merely because the product has one organization.

Proposed instance-scoped API family:

| Operation | Proposed route |
| - | - |
| Create/list sessions | POST/GET /v1/onboarding/sessions |
| Read/update session | GET/PATCH /v1/onboarding/sessions/{session_id} |
| Add file or brief | POST /v1/onboarding/sessions/{session_id}/sources |
| Start/cancel analysis | POST /v1/onboarding/sessions/{session_id}/analyze and /cancel |
| Read proposals and questions | GET /v1/onboarding/sessions/{session_id}/proposals and /questions |
| Answer or review | POST /v1/onboarding/sessions/{session_id}/decisions |
| Validate selected revision | POST /v1/onboarding/sessions/{session_id}/validate |
| Apply reviewed revision | POST /v1/onboarding/sessions/{session_id}/apply |
| Read apply outcomes | GET /v1/onboarding/apply-runs/{apply_run_id} |

Keep permissions in the current RBAC system. Existing setup uses sdk\:read/sdk\:manage, registry mutations use registry\:write, policies use policies\:write, and budget/monitor management uses risk\:manage. An onboarding apply operation must check each underlying target permission; sdk\:manage alone is not blanket authority to modify every domain. Recheck the initiating actor’s current membership and rights when a background apply job executes. [Authorization](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/core/authorization.py) · [Setup API](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/sdk/api.py) · [Registry API](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/registry/api.py) · [Policy API](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/policy/api.py) · [Risk API](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/risk_intelligence/api.py)

# Phase 0 Correct the current onboarding behavior

**Outcome:** a clear Community entry flow and trustworthy state model before AI-generated changes are introduced.

Tasks:

1. Define the supported v1 outputs and target adapters. Record unsupported semantics as capability gaps.

2. Add Set up with AI and Continue setup to the dashboard and existing onboarding navigation, subject to permissions.

3. Correct onboarding-specific welcome gating so the bootstrap administrator can discover setup. Do not force ordinary invited readers through administrator setup.

4. Stop the AI path from inheriting automatic approval attempts, ignored lifecycle failures or manual READY as evidence.

5. Distinguish skipped connection, manually completed setup, observed connection and tested controls.

6. Remove synthetic organization arguments from new contracts and touched onboarding clients. Preserve unrelated compatibility code until it needs changing.

7. Preserve existing installations’ welcome-completed state and manual workflow.

8. Define a small acceptance fixture pack: inventory, policy, budget, one contradiction, one existing match, one unreadable source and Arabic/English examples.

**Existing touchpoints:** welcome-gate.ts, public-api.ts, app-shell.tsx, welcome-wizard.tsx, guided-onboarding.tsx, public_product/onboarding.py and corresponding tests.

**Acceptance gate:** an existing installation remains usable; a bootstrap administrator can start AI setup; a reader is not forced into it; connection deferral remains deferred; no new API uses organization IDs or headers.

**Dependencies:** none. Provider policy and proposal/permission contracts should be agreed here so parallel work has stable boundaries.

Evidence: [Current gate](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/lib/welcome-gate.ts) · [Current status calculations](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/public_product/onboarding.py) · [Existing progress tests](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/public_product/test_onboarding.py).

# Phase 1 Add durable sessions and evidence

**Outcome:** uploaded information, analysis state and proposed changes survive refreshes and worker restarts.

Tasks:

1. Add the session, source-revision, analysis-run, evidence, proposal, decision and apply-ledger models with Alembic migrations.

2. Add the instance-scoped API and frontend client. Keep live domain tables separate from analysis state.

3. Implement protected source storage behind a small adapter. A persistent filesystem volume is a practical Community default; include worker access, permissions, backup and restore.

4. Extend BackgroundJob/JobQueue with an onboarding dispatcher. Add worker lease/heartbeat, interrupted-RUNNING recovery, bounded retries, cancellation and visible failed jobs.

5. Persist checkpoints after each source and stage. Store parser/model/prompt/schema versions and input digests.

6. Release database locks before long parsing or LLM work; claim and checkpoint with short transactions.

7. Define who can see sources, analyze, review and apply through existing membership authorization.

8. Add storage/worker configuration to Docker deployment and operations documentation.

**Suggested new paths:** models/onboarding.py, onboarding/api.py, onboarding/service.py, onboarding/storage.py, onboarding/jobs.py, workers/onboarding.py, frontend lib/onboarding-api.ts.

**Acceptance gate:** refresh resumes the same session; killing and restarting a worker resumes without duplicate output; cancellation prevents new stages; access checks apply to source previews and downloads; no registry, rule or budget record is created by analysis.

**Depends on:** Phase 0.

Existing reuse: [BackgroundJob and singleton progress](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/productization.py) · [JobQueue](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/sdk/jobs.py).

# Phase 2 Read sources and configure the AI processor

**Outcome:** supported files and prompts become structured, traceable input.

Tasks:

1. Implement TXT and prompt parsing; CSV and arbitrary multi-sheet XLSX interpretation; text-based PDF and DOCX extraction.

2. Preserve spreadsheet headers, units, merged-cell context, formula expressions and cached values separately. Record excluded or hidden content rather than silently losing it.

3. Preserve PDF page/text regions, DOCX paragraph/table locations, and spreadsheet sheet/cell ranges.

4. Validate format and size while streaming upload; handle corrupt, password-protected and unsupported files without discarding other sources.

5. Show parsed/partial/failed/OCR-required states and extraction coverage. A supported file extension alone is not a successful parse.

6. Add an explicit onboarding provider/model setting, endpoint/credential reference, timeout, generation budget and allowed-fallback list.

7. Reuse suitable provider transports behind a new typed onboarding interface. Test structured extraction on a known fixture, including schema errors and provider unavailability.

8. Keep raw credentials out of source records, model prompts and logs. Avoid legacy automatic provider fallback.

9. Isolate parsing; do not execute macros or document-supplied instructions/URLs.

10. Support manual continuation when AI is unavailable. Optional OCR can use the same parser interface when installed and validated.

**Suggested new paths:** onboarding/parsers/, onboarding/provider.py, onboarding/schemas.py, components/onboarding/source-list.tsx, source-preview\.tsx and processing-status.tsx.

**Acceptance gate:** each readable assertion has an exact locator; multi-sheet data is accounted for; unsupported scans are clearly identified; provider failure cannot route documents to an unapproved endpoint; invalid model output cannot become proposals.

**Depends on:** Phase 1. Parser and provider work can proceed in parallel after their contracts are fixed.

Evidence: [Current parsing dependencies](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/pyproject.toml) · [Provider adapters](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/llm_providers.py) · [Fallback behavior to avoid](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/llm_controller.py).

# Phase 3 Discover and reconcile the AI inventory

**Outcome:** the administrator reviews a proposed AI landscape with existing-record matches.

Tasks:

1. Classify source sections as inventory, architecture, policy, budget, responsibility or supporting information.

2. Extract assertions about systems, business purpose, owners, agents, versions, models, MCP servers, tools, external services and data sources.

3. Distinguish source statements, inferred relationships, explicit user requirements and recommendations.

4. Match existing records using stable keys, known identifiers, confirmed aliases and corroborating context; use fuzzy similarity only to propose a match.

5. Offer create, reuse, update, link, split, merge, exclude and defer choices. A system mentioned as planned is not a deployed system.

6. Resolve shared resources once and link them to the relevant versions. Attach MCP dependencies before their tools where the current service requires it.

7. Preserve unknown facts in staging. Do not translate absent sensitivity information into false, or guess owners and deployed model versions.

8. Propose a clearly labeled initial agent/version only when appropriate; preserve immutable approved configurations by proposing a new version.

9. Ask focused questions with source excerpts and affected proposals. Preserve confirmed matches for future analyses.

10. Keep source analysis read-only with respect to the existing registry.

**Suggested new paths:** onboarding/extraction.py, onboarding/reconciliation.py, onboarding/questions.py, components/onboarding/landscape-review\.tsx and decision-queue.tsx.

**Acceptance gate:** a repeated inventory produces the same matches; shared resources are not duplicated; similarly named distinct systems remain separate; unknown owners remain questions; a proposed version change does not mutate an approved version.

**Depends on:** Phase 2.

Existing reuse: [Registry data model](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/registry.py) · [Registry invariants and dependency ordering](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/registry/service.py).

# Phase 4 Generate governance proposals

**Outcome:** policies and business requirements become valid, explainable proposed controls.

Tasks:

1. Extract individual obligations with source revision, locator, applicability, exceptions and document authority.

2. Map obligations to supported ContentRule or ToolRule/ToolRuleCondition structures in the correct AI system’s PolicyCollection.

3. Resolve actual tool IDs, available parameter paths, conditions and authorized approval roles. Do not fabricate executable fields from prose.

4. Generate supported RuntimeBudget proposals with exact scope, metric, measurement source, window and exceed behavior.

5. Propose baseline RiskAssessment/RiskFinding records as drafts when useful; keep them distinct from observed runtime risk findings.

6. Generate monitoring proposals only when signal, aggregation, scope and response are supported.

7. Classify each requirement as supported, needs configuration, needs integration, organizational/manual, or capability gap.

8. Show source requirement and operational interpretation together. Preserve competing policies and current configuration until an authorized decision resolves precedence.

9. Validate every proposed target through a shared non-mutating domain-validation path. Revalidate again during apply.

10. Keep all output in the proposal store. Record enabled=false explicitly in staged control payloads.

11. Retain generic source-to-field provenance; add existing PolicyRuleLink relationships after accepted policy/rule records exist.

**Initial scope:** company profile suggestions, systems/catalog/dependencies, policy sources and obligations, content/tool rules, supported fixed-window budgets, optional baseline risks and monitor proposals. Hidden Community evaluation/governance screens are not prerequisites.

**Acceptance gate:** every control has evidence or a recommendation label, valid references and an enforceability status; missing currency/scope/time semantics remain blocked; a refund approval threshold cannot become an AI-spend cap; no proposed control activates itself.

**Depends on:** Phase 3.

Evidence: [Source and obligation models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/policy_library.py) · [Executable rules](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/policy_engine.py) · [Budgets and monitors](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/risk_intelligence.py).

## Budget semantics branch

Treat these as explicit domain extensions if the first product release must enforce them:

| Requirement | Current gap | Required implementation |
| - | - | - |
| Calendar month in a business timezone | Only fixed epoch-aligned window_seconds exists | Calendar-period type, timezone, reset boundaries and compatibility with current fixed windows |
| Monetary limits in EGP or another currency | RuntimeBudget has no currency and consumes numeric cost/amount | Defined currency contract, compatible measurements, mismatch behavior and explicit conversion policy if conversion is offered |
| Shared department or selected-system allowance | No department or arbitrary-system-group matcher | Aggregate scope identity, member resolution, one shared usage counter and parent/child behavior |
| Distinct action versus tool-call counting | Both metrics add one for each matching budget check | Confirm calling-path semantics, filter by supported action types, and test retries/holds/failures |
| Trajectory-scoped budget | Enum presence is not supported scope resolution | Implement the matcher/counter or omit the target from the selectable capability set |

For the initial limited release, preserve unsupported requirements as visible proposals and allow only exactly supported limits to be enabled. If monthly departmental budgeting is a launch commitment, this branch becomes a prerequisite for enabling those budgets; replacing a calendar month with a number of seconds is not acceptable.

Use the existing BEST_EFFORT versus MANDATORY measurement behavior deliberately: missing measurements currently skip best-effort checks and deny mandatory checks. Show that choice in business terms. Parent and child budgets both apply; a child allowance does not override a stricter parent. [Budget evaluation](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/risk_intelligence/budgets.py) · [Implemented scope matching](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/risk_intelligence/budget_scope.py)

# Phase 5 Review and apply selected changes

**Outcome:** a reviewer understands and applies a specific configuration revision, with recoverable results.

Tasks:

1. Build a before/after change-set view grouped by system, resource, policy, control and unresolved decision.

2. Show operation dependencies, evidence, impact, validation and the permission required to apply each item.

3. Record approval against an immutable change-set revision/digest. Keep this separate from runtime ApprovalRequest/ApprovalPlan, which are bound to concrete actions.

4. Recheck source/proposal revision, current target fingerprint, actor permissions and lifecycle prerequisites immediately before mutation.

5. Apply in dependency order through target adapters to existing services; create systems/agents/versions as drafts and rules/budgets disabled.

6. Add stable operation IDs and payload digests. A reused key with a different payload must fail.

7. Ensure crash-safe reconciliation between target mutation and operation ledger. Use a transactionally written operation link, an idempotent service operation or a verified stable natural key. A ledger written only after a service commit is insufficient.

8. Where existing internal commits prevent a guarantee, introduce a scoped transaction/idempotency change to that service with regression coverage. Do not promise whole-session atomicity.

9. Record succeeded, failed, blocked and deferred operations and return links to actual persisted records.

10. Retry incomplete work only. Do not delete or roll back unrelated changes or runtime history.

11. When updating an enabled existing control, preserve the current live revision until the reviewed activation step. A staged replacement must not silently modify active behavior. Where replacement requires several writes, implement an atomic activation/replacement operation rather than disabling the current rule first.

**Suggested new paths:** onboarding/apply.py, onboarding/adapters/, onboarding/review\.py, components/onboarding/change-review\.tsx and apply-results.tsx.

**Acceptance gate:** duplicate requests and crash recovery create each target once; a manual edit after review is detected; permission removal blocks queued writes; partial success is shown accurately; generated controls remain inactive until enabled by an authorized action.

**Depends on:** Phases 1, 3 and 4. The generic review/ledger framework can start after Phase 1 and receive real proposal types from Phases 3–4.

Evidence: [Existing service commits](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/registry/service.py) · [Policy mutations](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/policy/service.py) · [Control state defaults](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/policy_engine.py).

# Phase 6 Connect and verify selected controls

**Outcome:** the first application produces evidence that the configured controls behave as intended.

Tasks:

1. Reuse the runtime identity, credential, integration and delegation services after the relevant version/lifecycle requirements are met.

2. Keep credential creation explicit and secret handling outside AI analysis. Reuse the secure issuance surface.

3. Provide application-specific integration guidance and required runtime attributes. Follow the canonical SDK repositories identified by the README; local packages are mirrors.

4. Confirm identity, version, environment, supported intervention points and measurement availability.

5. Generate reviewable verification scenarios from the selected controls.

6. Run scenarios using an isolated test instance/system or a supported test path. Many existing rules are scoped to the AI system, so enabling them can affect several runtimes; do not assume per-environment activation exists. Show the effective scope, then enable production behavior through a separate authorized operation and run applicable smoke checks.

7. Verify allow, deny-before-side-effect, approval hold/resume, budget boundary and missing-measurement behavior.

8. Test delegation scope isolation when the integrated application is a SaaS product.

9. Link observed receipts, trajectories, approval records and metering to the exact tested configuration and time.

10. Display connected and verified independently. A capability declaration or heartbeat is not a passed enforcement test.

**Initial UI:** keep the verification experience inside onboarding and link to existing Runtime Identities, Trajectories, Approvals, Audit Explorer, Risk and FinOps screens. A new standalone enterprise test/evaluation console is outside the initial scope.

**Acceptance gate:** a denied test has evidence that the tool was not invoked; held actions do not run before valid approval; a failed control test leaves the system connected but unverified; successful results identify their exact scope.

**Depends on:** Phase 5 and the required domain capabilities from Phase 4.

Evidence: [Integration records](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/productization.py) · [Canonical SDK guidance](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/README.md).

# Phase 7 Add incremental updates and operational rollout

**Outcome:** onboarding becomes a repeatable maintenance workflow.

Tasks:

1. Reconcile replacement/new documents against current approved configuration and previously confirmed mappings.

2. Retain source revisions, explicit supersession decisions, prior proposal revisions and reviewer history.

3. Invalidate only proposals and approvals affected by new evidence or changed targets.

4. Keep missing inventory rows as observations; do not infer deletion.

5. Add source retention/deletion, export of the reviewed setup and source/worker backup-restore checks.

6. Validate Arabic/English parsing and dense review tables, including RTL handling and keyboard access.

7. Add performance and quality fixtures for realistic batch sizes, processing limits, cost/latency budgets and partial failures.

8. Track time to reviewed setup, accepted/corrected proposals, evidence coverage and time to first verified action.

9. Roll out to controlled installations, verify upgrade behavior and publish supported-format/provider/capability documentation.

10. Expand OCR, URL/connectors, additional budget semantics and richer governance automation as explicit extensions after their required controls and deployment dependencies are implemented.

**Acceptance gate:** a revised policy creates a focused diff; unchanged resources and reviewed decisions are preserved; source supersession is auditable; backup/restore recovers originals and proposal history; failed processing is visible and recoverable.

**Depends on:** Phase 5 for incremental configuration and Phase 6 for operational rollout. OCR or a specific budget extension can run in parallel earlier if it is a required launch capability.

# Release boundaries and delivery order

| Milestone | Included phases | User-visible promise |
| - | - | - |
| Discovery preview | 0–3 | Read supported information, identify systems/resources and explain findings. No live configuration is applied. |
| Reviewed setup generation | 4–5 plus prior phases | Create reviewed inventory and staged governance configuration, with clear unresolved items. |
| First governed application | 6 plus prior phases | Connect one real application and verify selected controls. |
| Repeatable onboarding | 7 | Maintain the setup as policies, budgets and AI systems change. |

Recommended initial demonstration: an inventory with two systems sharing a model; a policy requiring approval for refunds above USD 500; a supported fixed-window usage limit; a calendar-month budget deliberately flagged as a capability gap; and one source conflict. The administrator resolves the conflict, applies the reviewed plan, and verifies an allowed action, a denied action and an approval hold.

Suggested reviewable implementation slices:

1. Community entry flow, state semantics and capability contract.

2. Session/source/proposal persistence and protected file storage.

3. Onboarding worker, provider selection and structured-output contract.

4. File parsers, source preview and extraction coverage.

5. Inventory extraction and existing-record reconciliation.

6. Policy/rule/budget proposal compilation and semantic checks.

7. Review UI, authorization-aware application and crash-safe idempotency.

8. Runtime connection, controlled activation and verification evidence.

9. Source revisions, incremental diffs, recovery and rollout documentation.

Each slice should be independently reviewable, while release promises follow the milestone table. Calendar/currency/group budgets require a dedicated domain slice before those specific limits can be marketed or enabled.

Calendar estimates should be set after Phase 0 confirms the provider, file fixtures and budget semantics included in the first release. The phase gates define completion more reliably than an unvalidated week estimate.

# Regression and acceptance matrix

| Scenario | Required result |
| - | - |
| New bootstrap administrator | AI setup is discoverable; no organization selection is requested. |
| Existing completed installation | Migration preserves normal access and previous setup state. |
| Invited reader | Can only access permitted information; no forced admin flow or unauthorized apply. |
| Analyze an uploaded batch | No target registry/rule/budget mutation occurs. |
| Existing XLSX template | Legacy Catalog/Example behavior remains intact. |
| Multi-sheet arbitrary workbook | All included sheets are accounted for with cell references. |
| One corrupt or scanned source | Other results survive; affected coverage is shown as incomplete/OCR required. |
| Malicious instructions inside a document | No tool execution, permission change or provider-policy change follows. |
| Local provider unavailable | No request goes to an unapproved cloud fallback. |
| Worker interrupted while RUNNING | Lease recovery resumes from a checkpoint without duplicate proposals. |
| Same source uploaded again | Reuse or explicit revision handling; no duplicate inventory. |
| New draft contradicts approved policy | Conflict is visible; current live configuration is preserved. |
| Shared departmental budget | Preserve aggregate meaning; do not multiply it across systems. |
| Unsupported currency or period | Proposal remains blocked from activation. |
| Missing required measurement | Apply the configured unavailable-measurement behavior and show the evidence. |
| Generated control applied | Explicitly disabled/staged until authorized activation. |
| Target changes after review | Reject the stale operation and require review of the affected diff. |
| Permission revoked before queued apply | Recheck rights and block the mutation. |
| Crash after target write | Reconcile the committed operation and avoid a duplicate on retry. |
| Partial batch failure | Show persisted results, blocked dependencies and targeted retry. |
| Heartbeat only | Connected may be shown; control verification remains pending. |
| Denied test action | Record denial and evidence that the side effect did not occur. |
| Static risk from a policy | Draft RiskAssessment/RiskFinding; no fabricated runtime incident or signal. |
| Updated source omits a known system | No automatic deletion. |

Extend relevant existing tests rather than replacing their behavior: [Onboarding progress](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/public_product/test_onboarding.py), [Catalog import](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/registry/test_catalog_import.py), [Policy Library/rules](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/policy/test_unified_rules_and_library.py), and [Budget hierarchy](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/risk_intelligence/test_hierarchical_budgets.py). Add dedicated onboarding fixtures and meaningful end-to-end tests for source processing, review, application and verified runtime outcomes.

# Completion criteria

The feature is complete for its declared release scope when an administrator can supply supported files and a brief, understand the extracted evidence, resolve important ambiguity, review an exact change set, apply it without duplicate or unintended changes, and see which controls have actually been verified.

Broader capabilities remain explicit extensions: calendar and currency-aware budgeting, department/group allocations, safe staged data-access policy creation, full OCR and connectors, and richer evaluation/governance automation. Each extension requires its own semantics, permissions, tests and Community UX decision.
