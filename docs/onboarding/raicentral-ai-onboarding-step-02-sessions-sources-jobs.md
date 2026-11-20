# RaiCentral AI Onboarding — Step 02: Durable Sessions, Sources, and Jobs

> **Implementation work order for Claude.** Implement this step in the existing RaiCentral repository, including migrations, API, worker, minimal UI, deployment wiring, and focused tests. Complete the bounded change described here. Return implementation results rather than another plan.

## 1. Outcome and place in the roadmap

**Outcome:** an authorized organization administrator can create an onboarding session, upload supported documents, add a text prompt, leave the application, and resume later. Original inputs and their revisions persist. A real background worker checks the saved inputs, recovers from interruptions, and reports its actual progress.

This is **implementation Step 02**, corresponding to **Phase 1 — Durable sessions, evidence, and jobs** of the roadmap.

- **Step 01:** Community entry, permissions, truthful progress, and explicit lifecycle review.
- **Step 02:** persistent intake workspace, original sources, version history, and recoverable preparation jobs.
- **Step 03:** document/text extraction and explicit AI-provider configuration.
- Later steps: inventory proposals, governance proposals, reviewed application, and runtime verification.

The background operation implemented here is **Prepare sources**. It checks that the selected original files/text are saved, accessible, within supported limits, and unchanged. It does not interpret their business meaning.

### Baseline and prerequisite

| Item | Value |
| --- | --- |
| Repository | https://github.com/ztechnium/raicentral |
| Edition | Community Edition: one governance organization per installation |
| Reviewed branch and commit | `main` at `0c052350db88c834e79c752284e264a68d7c6210` |
| Specification date | 19 November 2026 |
| Prior work order | `raicentral-ai-onboarding-step-01-foundations.md` |
| Review method | Source inspection; no application tests were executed while writing this specification |

The inspected `main` revision is unchanged from the Step 01 brief. This document does **not** assert that Step 01 has been implemented or merged. Check the current working branch, applicable `AGENTS.md` instructions, and `git status`. Integrate with the Step 01 behavior actually present, preserve unrelated changes, and identify any prerequisite gap in the completion report. Do not reset the checkout to the inspection revision.

### Scope boundary

Deliver storage and resumable workflow foundations. Do not add PDF/DOCX text extraction, spreadsheet mapping, OCR, crawling, embeddings, RAG, LLM calls, classification, generated AI systems, proposed rules, budget generation, approval automation, or bulk domain writes.

Keep the existing welcome/checklist and guided runtime onboarding working. Preserve the existing structured Catalog workbook importer as a separate operation: it already writes catalog records, whereas this new intake workspace only stores sources.

## 2. Repository findings and reuse decisions

The following are verified existing components. New names introduced later in this document are proposed implementation contracts.

| Existing component | Location | Decision for this step |
| --- | --- | --- |
| `BackgroundJob` | `src/rai_gateway/models/productization.py` | Reuse this job table; add lease/ownership fields through Alembic |
| `JobQueue` | `src/rai_gateway/sdk/jobs.py` | Extend the database queue; keep PostgreSQL as job authority |
| Job status enum | `src/rai_gateway/sdk/domain.py` | Preserve existing statuses; add cancellation support if required by the contract below |
| Worker entry point | `src/rai_gateway/workers/cli.py`; `workers/runtime_events.py` | Add onboarding dispatch while preserving `runtime-events` and existing command compatibility |
| Generic jobs API | `src/rai_gateway/sdk/api.py` | Protect internal onboarding job types and prevent content exposure through generic job serialization |
| Request transaction handling | `src/rai_gateway/core/database.py` | Reuse explicit transactions and separate worker sessions; `get_db()` commits successful requests |
| Router assembly | `src/rai_gateway/services/llm_controller.py` | Register the new router here; `main.py` reuses this application |
| Membership authorization | `src/rai_gateway/core/authorization.py` | Reuse server permissions derived from active memberships |
| Role permission seeds | `src/rai_gateway/services/admin_console.py` | Add the narrow source-content permission to existing role definitions |
| Administrative audit | `src/rai_gateway/models/database.py::AdminAudit` | Record safe administrative metadata, without original source content |
| Runtime evidence | `src/rai_gateway/models/evidence.py::EvidenceArtifact` | Do not reuse: it requires a runtime trajectory |
| Governance policy sources | `src/rai_gateway/models/policy_library.py::PolicySource` | Do not create from intake: it is existing governance-library state |
| Object-storage settings | `src/rai_gateway/core/config.py` | Settings exist, but inspection found no reusable S3/MinIO original-file adapter |
| Schema registration | `alembic/env.py` | Import the new model module; schema changes are Alembic-only |
| Container deployment | `Dockerfile`; `docker-compose.yml`; `docker-compose.dev.yml` | Add a worker service and a persistent source volume shared with the API |

The existing queue uses `FOR UPDATE SKIP LOCKED`, retry scheduling, and a globally unique idempotency key. It does not currently reclaim abandoned `RUNNING` jobs or fence a stale worker's completion. The current `rai-worker runtime-events` processes approval/outbox work; it does not consume these new onboarding jobs.

**Storage decision:** implement a private filesystem adapter for this Community step, with a persistent volume shared by the API and worker. Keep a small adapter interface for a future object-store implementation. Do not claim existing S3/MinIO support or silently reinterpret the global `object_storage_backend="postgres"` setting as implemented file storage.

## 3. User journey and status language

1. Open Onboarding and choose **Prepare onboarding sources**.
2. Create a session with a short title.
3. Add files, a text prompt, or both. File-only and prompt-only sessions are valid.
4. Review saved inputs and their revision history.
5. Click **Prepare sources**.
6. See Queued → Checking saved sources → Sources saved.
7. Leave and resume from the server-persisted session at any point.

After successful preparation, use this copy:

> **Sources saved**
> Your selected files and prompt are saved and ready for analysis.

Keep business analysis, generated-item counts, and verification badges absent until those capabilities exist. Do not show “Analysis complete,” fabricated classifications, confidence scores, generated catalog entries, or production-readiness claims.

### Session and run states

Persist the session lifecycle as `OPEN` or `ARCHIVED`. Derive the current session view from its current `input_revision` and associated run; do not maintain a second independent copy of every job status.

| Session view | Meaning |
| --- | --- |
| `DRAFT` | Inputs can be edited; no successful preparation exists for the current input revision |
| `QUEUED` | A preparation job exists durably and is waiting for a worker |
| `PREPARING` | A worker currently owns the preparation job |
| `READY_FOR_ANALYSIS` | Preparation succeeded for the exact current input revision |
| `PREPARATION_FAILED` | The current preparation terminated with a permanent error or exhausted retries |
| `ARCHIVED` | Session is archived and read-only |

A cancelled run remains `CANCELLED` in history, while the editable session returns to `DRAFT`. Editing a source after a successful run advances `input_revision` and returns the view to `DRAFT`; the old result stays in history.

Input edits are locked while a run is queued or running. Offer **Cancel preparation** before editing. Keep this concurrency policy simple in Step 02, and still reject stale worker publication as defense against races.

## 4. Proposed code layout and data model

Use the repository's module conventions. These paths are proposed additions, not existing files:

```text
src/rai_gateway/onboarding/
  __init__.py
  api.py
  schemas.py
  service.py
  repository.py
  storage.py
  preparation.py
src/rai_gateway/models/onboarding.py
src/rai_gateway/workers/onboarding.py
tests/onboarding/
control-plane-ui/lib/onboarding-api.ts
control-plane-ui/components/onboarding/intake/
```

Keep modules small; combine files when that fits the existing style. Do not move the legacy `public_product/onboarding.py` service or create a second job-queue implementation.

### 4.1 New tables

| Proposed model/table | Required responsibilities and fields |
| --- | --- |
| `OnboardingSession` / `onboarding_sessions` | UUID; title; lifecycle; integer `row_version`; integer `input_revision`; creator/updater and timestamps |
| `OnboardingSource` / `onboarding_sources` | UUID; session FK; immutable kind `FILE` or `TEXT`; immutable role `USER_PROMPT` or `SUPPORTING_MATERIAL`; `included` flag; current revision reference; creator/timestamps |
| `OnboardingSourceRevision` / `onboarding_source_revisions` | UUID; source/session association; revision number; original display filename/title; declared and detected media family; opaque storage key; SHA-256 of original bytes; exact byte length; creator/received timestamp |
| `OnboardingRun` / `onboarding_runs` | UUID; session FK; monotonically assigned per-session `run_number`; stage `PREPARE_SOURCES`; input revision; immutable input manifest and manifest digest; unique FK to `BackgroundJob`; retry-of run reference when applicable; created timestamp; nullable `ended_at` |
| `OnboardingMutationReceipt` / `onboarding_mutation_receipts` | Durable idempotency scope, key digest, request fingerprint, resulting resource kind/ID, actor, and timestamp; unique scope/key digest pair |

The receipt table is local to this feature. It avoids inventing separate ad hoc idempotency mechanisms for session creation, file upload, text revisions, and preparation requests. Store identifiers and digests, never complete sensitive request/response bodies.

`OnboardingRun` supplies the immutable input snapshot and session association. `BackgroundJob` remains the authority for execution state, attempts, scheduling, lease, safe errors, and results. Do not add an `onboarding_jobs` table duplicating it.

Use actual foreign keys, uniqueness/check constraints, and indexes. Required invariants include:

- Positive row/revision numbers and nonnegative byte counts/input revisions.
- Unique `(source_id, revision_number)` and unique finalized storage keys.
- A source's current revision belongs to that source and session.
- `USER_PROMPT` requires kind `TEXT`; at most one included prompt source per session.
- A run's job belongs to that run alone.
- Unique `(session_id, run_number)`, allocated under the session lock; determine the current run by this ordering for the current input revision, not timestamp ties.
- At most one active run per session, enforced by a PostgreSQL partial unique index on the session where `ended_at IS NULL`.
- Run/job terminal state and `ended_at` are finalized together. Retry scheduling and lease reclamation leave the run active.
- Nested API lookups validate session → source → revision and session → run membership; a valid UUID from another session is not interchangeable.

Use UUIDs and timestamp conventions consistent with the existing models. Do not add `organization_id` or introduce a SaaS tenant model. Sessions are shared with authorized users of this installation; `created_by` is attribution, not an undocumented private-workspace boundary.

### 4.2 Immutable input revisions

Store the prompt as a `TEXT` source with role `USER_PROMPT`. Store pasted supporting text as `TEXT` / `SUPPORTING_MATERIAL`. Keep prompt text out of ordinary session metadata.

A file replacement or text edit creates a new immutable source revision. Preserve the previous bytes, digest, author, and timestamp. The source points to its current revision. An explicit exclusion changes `included` and increments the session input revision; it does not erase historical evidence.

For text, encode the exact accepted string as UTF-8 and hash/store those bytes. Do not silently trim or normalize the stored original. Reject empty/whitespace-only inputs as a validation decision. Normalized/extracted text belongs to a later processing step.

Every source addition, replacement, prompt edit, inclusion, or exclusion increments `input_revision`. A title-only session edit or archive/restore changes `row_version` without changing the source input revision. Source mutations also increment `row_version`. Run creation, cancellation, retry, worker claims, progress, and finalization do not change the session's editable `row_version`; the active-run guard independently controls whether editing is allowed.

### 4.3 Manifest and provenance

When preparing, freeze a canonical manifest of the included current source revisions in a single transaction. Use stable ordering and serialization for its digest. An example entry is:

```json
{
  "source_id": "server-generated-uuid",
  "source_revision_id": "server-generated-uuid",
  "kind": "FILE",
  "role": "SUPPORTING_MATERIAL",
  "sha256": "digest-of-original-bytes",
  "byte_length": 12345,
  "media_family": "PDF"
}
```

Do not include raw prompt text, document text, local paths, or storage credentials in job payloads. Keep the manifest on the run; the job payload needs only validated run/session IDs and input/manifest version references.

Step 02 provenance is a source revision, digest, receipt metadata, and **whole-source** locator. Do not invent PDF pages, spreadsheet cells, DOCX sections, or extracted quotes. Step 03 will add those locators after real extraction.

## 5. Permissions and content boundaries

Reuse the existing exact permission list and `require_permission()` behavior. A platform-role flag does not grant product access.

Add **`onboarding:sources:read`** to the existing permission seeds for `organization_admin` and `rai_officer`. Preserve other role grants. This dedicated permission matters because existing `sdk:read` and `sdk:manage` are also granted to users who do not automatically have access to confidential source contents.

| Operation | Required permissions |
| --- | --- |
| List sessions and inspect sanitized progress/counts | `sdk:read` |
| Read source names, revision details, prompt bodies, manifests with sensitive metadata, or original bytes | `sdk:read` and `onboarding:sources:read` |
| Create/edit/archive/restore an intake session; change sources; prepare/retry/cancel a run | `sdk:manage` and `onboarding:sources:read` |

Requiring source access for all new intake mutations is an intentional Step 02 boundary. It does not change permissions for the legacy checklist or unrelated SDK integration management.

Use the existing seeding/upgrade mechanism so already installed administrators receive the new grant; do not support fresh databases only. Verify the exact permission appears in `/v1/auth/context` after refresh.

Apply redaction at serialization boundaries. Metadata-only readers receive counts, sizes, media families, states, timestamps, and generic labels such as “Source 1.” Do not expose file names, user-entered titles, prompt snippets, original bytes, storage keys, filesystem paths, or download URLs through a lower-permission response. Session titles may also contain confidential text; use a generic session label for metadata-only readers.

Authenticated original download must pass the same authorization and membership checks as content viewing. Use attachment disposition and `X-Content-Type-Options: nosniff`; do not mount the storage root as a public static directory. Keep tokens out of URLs. Render user-supplied text as text, without executing HTML or file contents.

Audit mutations and original-content access through the existing administrative audit mechanism. Record actor, action, session/source/revision/run IDs, and safe reason codes. Keep raw input, filename-bearing sensitive excerpts, and provider secrets out of audit targets, ordinary logs, job errors, and telemetry.

## 6. API and concurrency contracts

Add a dedicated router under **`/v1/productization/onboarding/sessions`**, registered in `services/llm_controller.py`. Preserve the existing `/v1/productization/onboarding` GET/PATCH and welcome routes.

### 6.1 Routes

Paths below are relative to the new sessions prefix. New route names are specifications, not claims that they already exist.

| Method/path | Behavior |
| --- | --- |
| `POST /` | Create a session with a title; return IDs, versions, and empty-input state |
| `GET /` | Paginated, filtered session list; default excludes archived sessions |
| `GET /{session_id}` | Current session view, versions, source summaries, run summary, allowed actions |
| `PATCH /{session_id}` | Update session title with optimistic concurrency |
| `POST /{session_id}/archive` and `/restore` | Change lifecycle; archive requires no active run |
| `POST /{session_id}/sources/files` | Upload one file and create its first revision |
| `POST /{session_id}/sources/text` | Create a prompt or supporting-text source |
| `POST /{session_id}/sources/{source_id}/revisions/file` or `/text` | Create a replacement revision of the same source kind |
| `PATCH /{session_id}/sources/{source_id}` | Include/exclude the source; no in-place original-content edits |
| `GET /{session_id}/sources/{source_id}/revisions` | Authorized revision history |
| `GET /{session_id}/sources/{source_id}/revisions/{revision_id}/content` | Authorized exact original bytes/text |
| `POST /{session_id}/prepare` | Create or reuse a preparation run and its durable job; return `202` for active work |
| `GET /{session_id}/runs` and `/{session_id}/runs/{run_id}` | Paginated run history/detail with permission-aware projections |
| `POST /{session_id}/runs/{run_id}/cancel` | Cancel active work; terminal state cannot later become success |
| `POST /{session_id}/runs/{run_id}/retry` | Explicit retry of failed/dead-letter/cancelled work, creating a linked new run/job |

Avoid accidental trailing-slash redirects in the final client/server contract. Validate bounded titles, content lengths, enum values, pagination, and allowed state transitions. Return consistent structured errors containing a safe code, message, and retryability where relevant.

Suggested errors include `INPUT_REQUIRED`, `UNSUPPORTED_MEDIA`, `SOURCE_TOO_LARGE`, `SESSION_LIMIT_EXCEEDED`, `REVISION_CONFLICT`, `PREPARATION_ACTIVE`, `IDEMPOTENCY_CONFLICT`, `STORAGE_UNAVAILABLE`, and `SOURCE_INTEGRITY_FAILED`. A rejected request must leave no partially visible source revision or preparation job.

### 6.2 Optimistic concurrency and idempotency

Return the session `row_version` in JSON. Require an explicit `expected_row_version` for existing-session mutations, including uploads, preparation, and lifecycle changes: a JSON field for JSON requests, or a multipart form field for file requests. A mismatch returns `409 REVISION_CONFLICT`; a missing required field returns `422`. Do not use a strong HTTP ETag derived only from this version, because the combined session view also includes independently changing job progress. Return session/run polling responses with `Cache-Control: no-store`.

Prepare and retry bodies also carry `requested_input_revision`. Include that value in the logical request fingerprint. A late click from an old input revision must not start work on newly edited inputs.

For create/upload/revision/prepare/retry requests, require a bounded `Idempotency-Key`. Scope and fingerprint it server-side, store only its digest in the mutation receipt, and commit that receipt with the mutation.

Use an actor-and-operation scope for session creation, and a session/actor/operation/target scope for existing-session requests. Derive actor and ownership from authentication/database records, not a client-supplied user or organization identifier. Identical preparation across different keys or actors is still reconciled by the session/run rules below.

Rules:

1. Check authorization first. Never use a known key or content hash as an access credential.
2. The same scoped key and same logical request returns the same resulting resource identity without another mutation, even if the original response was lost.
3. The same key with different content or operation returns `409 IDEMPOTENCY_CONFLICT`.
4. An already committed identical retry is resolved before rejecting its now-stale `expected_row_version`. Do not include this concurrency precondition itself in the logical request fingerprint. Replay returns the original resource identity/history without resetting current source pointers or presenting an old result as current readiness.
5. For file uploads, compute the fingerprint from the actual streamed bytes plus relevant metadata and operation; do not trust a client-provided checksum. Clean up the duplicate request's temporary bytes.
6. Use database uniqueness and transaction-safe conflict handling. A pre-insert lookup alone is insufficient under concurrent requests.
7. Intentional replacement uses a new key and creates a new revision. Identical files in different sessions remain separately authorized source records. Do not implement global content deduplication in this step.

Lock the session row for the short final mutation/snapshot transaction. Recheck its lifecycle, active-run guard, expected version, source ownership, and quota there. Streaming file data must not hold a session row lock for the entire upload.

After authorization and any identical-key replay, lock the session and verify `expected_row_version` and `requested_input_revision` against current values. Then reuse a matching active/current-successful run or create a new one. Because run-only transitions do not advance the editable row version, simultaneous preparation requests for the same inputs can converge without bypassing stale-input protection. Bind the second request's receipt to the reused run.

Failed/cancelled work requires the explicit retry operation. A retry creates a new job with fresh attempt accounting, a new run number, and `retry_of_run_id`, preserving the old run's history. Parallel retries of the same failed run reuse that linked new active run. A retry of an older failed run after a later run supersedes it must return `409 RUN_SUPERSEDED` and the current run reference; do not enqueue extra work after a newer success.

Retry is valid only when the current input revision still matches the original manifest. If inputs changed, require a new prepare request for the new snapshot. Serialize retry selection under the session lock so parallel clicks cannot create multiple active runs.

### 6.3 No implicit application-domain writes

Source intake and preparation must not call catalog import, registry creation, policy publication, budget creation, credential issuance, approval, or runtime verification services. No write to the legacy welcome completion/readiness fields is required.

The separate mapping is deliberate:

| Input or operation | Writes now | Later mapping |
| --- | --- | --- |
| Company policy PDF/DOCX | Source and immutable revision | Reviewed policy source/obligation/rule proposals |
| AI inventory spreadsheet | Source and immutable revision | AI system, agent/version, and catalog proposals |
| Organization prompt | Versioned text source | Analysis instructions constrained by platform rules |
| Prepare sources | Run, job, safe audit metadata | Enables later parsing/analysis |
| Replace/exclude source | New revision or input-manifest change | Later re-analysis and proposal reconciliation |

Treat all uploaded text, including prompts, as untrusted input data. Future workers must not treat embedded instructions as authority to bypass platform permissions, review, or domain validation.

## 7. Original-source storage

### 7.1 Initial supported intake

Accept **PDF, DOCX, XLSX, CSV, TXT, and Markdown** originals. Store their real bytes without parsing document text or running workbook formulas/macros. Reject unsupported formats with a clear message. Legacy `.doc`/`.xls`, archives, executable files, images/OCR, and password-protected-document handling are not promised by this step.

Use extension, declared MIME, and bounded file-signature/container inspection to identify the accepted media family. Do not rely solely on the browser MIME or filename. Minimal container validation is permitted; it is not document extraction. Never decompress unbounded archive content. Later extraction may still reject an encrypted, malformed, or otherwise unsupported document; successful storage is not a claim that it is readable or safe to execute.

Proposed configurable defaults, chosen for this step:

| Limit | Default |
| --- | --- |
| Maximum original file | 25 MiB |
| Maximum included sources per session | 20, including a prompt source |
| Maximum retained original bytes per session | 200 MiB, including older/excluded revisions |
| Maximum text input | 50,000 Unicode characters, also bounded by encoded byte size |
| Maximum upload/finalization duration | 10 minutes |
| Minimum age before orphan cleanup | 24 hours; must exceed the maximum upload/finalization duration |

Name and document the corresponding settings in `core/config.py` and `.env.example`. Validate them at startup. Keep this intake's limits independent of the current Catalog importer limits.

Enforce actual request-body byte and duration limits **before unbounded multipart parsing/spooling**, using a bounded ASGI/request layer or equivalent supported ingress enforcement. `UploadFile` handler chunk checks alone may run after multipart spooling. Treat `Content-Length` only as an early rejection hint; count actual received bytes, allow bounded multipart overhead, bound part/file counts, and reject extra file parts on the one-file endpoint. Retain per-file streaming/digest checks as a second boundary.

### 7.2 Filesystem protocol

Add a small `OnboardingSourceStore` interface for stage/write, finalize, open/read, and controlled orphan cleanup. Implement the local adapter now. Use a dedicated setting such as `ONBOARDING_SOURCE_STORAGE_ROOT`; do not select an unimplemented backend from the existing global object-storage flags.

For each upload or text revision:

1. Check authentication, source permissions, basic request validity, and session existence.
2. Stream into a generated private staging name. Enforce bytes and timeout while reading; compute SHA-256 over the exact original bytes.
3. Perform bounded media checks. Reject oversized or unsupported input without publishing a source revision.
4. In a short database transaction, resolve idempotency, lock/revalidate session state and quota, and allocate source/revision IDs.
5. Durably finalize the original using a server-generated opaque key and atomic publication on the same filesystem. Do not overwrite an existing original. Flush file/directory data as needed for the durability guarantee.
6. Insert the source revision, advance current input/version pointers, write audit metadata and the mutation receipt, and commit together.
7. Return success only after durable finalization and database commit succeed.

The existing request dependency can support this boundary, but do not rely on a test's uncommitted session or an HTTP response already sent before commit failure is known. Keep commit ownership explicit and consistent; do not scatter inner commits across repositories.

Store keys generated by the server. User filenames are display metadata, never filesystem paths. Enforce root containment, reject symlink/path traversal access, and use private permissions suitable for the service user. Do not expose storage keys in API JSON.

### 7.3 Failure reconciliation and retention

Filesystem and database updates are not one transaction. Handle each failure boundary:

| Failure | Required result |
| --- | --- |
| Interrupted streaming | No visible revision; stale partial file is eligible for cleanup |
| Final original exists but database commit fails | No visible source; unreferenced original is eligible for cleanup after the grace period |
| Metadata commit succeeds but response is lost | Identical retry returns the same source/revision |
| Bytes become missing or fail their digest later | Preparation reports an integrity/storage failure; never claim current sources are ready |
| Session/source is excluded or archived | Keep referenced revisions; omit excluded sources from future manifests |
| A historical run references an older revision | Preserve the exact referenced original |

Provide a bounded reconciliation/cleanup function callable from the onboarding worker. It may remove only stale staging files or finalized objects in this adapter's private namespace with **no committed revision reference**. Recheck references immediately before deletion and respect the upload duration/grace guarantees. Never remove files merely because they are old, share a digest, or belong to an archived session. Keep cleanup away from runtime evidence and unrelated storage.

“Exclude source” and “Archive session” are reversible workflow actions. Explain that revision history is retained. Do not label them permanent deletion. Retention/purge policies for referenced originals are a later capability, not a hidden automatic cleanup rule.

## 8. Durable worker and job execution

### 8.1 Queue and lease extension

Reuse `BackgroundJob` and `JobQueue`. Add nullable fields such as `worker_id`, `lease_token`, `lease_expires_at`, and `heartbeat_at`. Add an index suitable for eligible/expired onboarding work. Preserve existing generic job callers and stored rows.

Define one internal job type, **`ONBOARDING_PREPARE_SOURCES`**. The new worker claims only its supported types. Lease recovery in this step must not unexpectedly reclaim unrelated historical jobs whose handlers have not adopted the new lease contract.

Use configurable defaults of a 60-second lease, renewal at most every 15 seconds during work, and five attempts. Maintain bounded retry backoff using the existing queue conventions. Classify errors: temporary storage/database failures may retry; unsupported input or digest mismatch is a terminal validation/integrity error unless a new user action changes the inputs.

Execution semantics are **at least once with idempotent, ownership-checked publication**. Do not promise exactly-once execution.

### 8.2 Required execution algorithm

1. **Enqueue atomically:** lock/check the session, freeze its manifest, create the run and job, create the mutation receipt and audit record, then commit. The database row is the durable queue; no separate broker dispatch is needed.
2. **Claim briefly:** use `FOR UPDATE SKIP LOCKED` for eligible supported work, assign a new random lease token, increment the attempt count, set expiry, and commit before reading original files.
3. **Validate binding:** resolve the server-created run and session. The run must link to this exact job, stage, input revision, and manifest. Reject injected/orphan jobs.
4. **Check sources:** open only the manifest's immutable revisions, verify original existence, exact size/digest, accepted media metadata, and limits. Do not parse business content or call an AI provider.
5. **Renew conditionally:** update a lease only while status/owner/token still match and the lease remains valid. An expired owner cannot renew itself after another worker has claimed the work.
6. **Publish conditionally:** in one short transaction, lock in a consistent order, recheck current input revision, lifecycle, current run number, run/job association, current lease ownership, unexpired lease, and non-cancelled status. Publish safe result counters, terminal job status, run `ended_at`, and audit metadata together.
7. **Recover abandoned work:** expired onboarding leases become reclaimable. A reclaimed attempt gets a fresh token. Crashed attempts count toward the limit; repeated crashes eventually reach a terminal dead-letter state with the run closed.

Keep request and worker SQLAlchemy sessions separate. Do not pass request sessions or ORM instances into background threads. Do not hold database locks while streaming/hash-checking documents. Use a consistent session/job lock order for finalization, cancellation, and input changes to avoid deadlocks.

Use ownership conditions for every worker-owned progress, completion, failure, renewal, and retry update. A stale worker must be unable to overwrite a newer result, append duplicate success audit records, or reopen a cancelled run.

### 8.3 Cancellation and progress

Add `CANCELLED` to the shared job status enum if absent. For cancellation, lock the session/job, conditionally make the active job terminal, invalidate its lease, and set run `ended_at` in the same transaction. The worker must stop on its next ownership/cancellation check; late completion is rejected.

Progress contains real stages and counters such as `sources_checked` / `sources_total`, not a timer-based percentage. A safe success result can contain input revision, manifest digest, source counts, checked byte count, and check completion time. Never include source text, full file names, local paths, or classified business output in generic job JSON.

A queued job remains queued if no worker is running. The UI may say **Waiting for a worker**; do not report a worker outage unless the server has evidence of one.

### 8.4 Close the generic enqueue bypass

The existing generic `POST /v1/productization/jobs` accepts arbitrary job types and payloads. Reject the reserved `ONBOARDING_` namespace there. Only the new authorized session service may create these jobs.

The worker must additionally verify its persisted run/job binding. API filtering alone is insufficient. Also review generic job list serialization: source contents, paths, detailed file errors, and secrets must remain absent even when a user with only `sdk:read` calls that route.

Namespace global job idempotency keys, for example `onboarding:prepare:<run-id>`. Enforce uniqueness conflicts transaction-safely. Do not key globally by file hash or allow an onboarding retry to resolve to an unrelated job.

## 9. Minimal frontend screens

Add these proposed routes within the current application layout:

- `/app/onboarding/sessions` — session list.
- `/app/onboarding/sessions/[sessionId]` — intake workspace.

Their source files belong under `control-plane-ui/app/(app)/app/onboarding/sessions/`. Keep the existing `/app/onboarding` guided setup route. Add a permission-aware entry link/card there; do not redirect existing guided users automatically.

### Screen A — Session list

Show session label, current stage, source count, updated time, and Resume. Provide New session for authorized intake managers and an Archived filter. Metadata-only readers receive generic labels and read-only progress.

Include loading, empty, forbidden, unavailable/Retry, and archived states. Session creation is a server mutation; opening the page must not create records.

### Screen B — Inputs workspace

Show session title, saved-state indicator, files, text prompt, and Prepare sources. Use an explicit **Save prompt** action, so closing an unsaved edit cannot be mistaken for server persistence. Warn about unsaved local edits when navigating away using existing UI conventions.

Upload one file per request; a multi-file picker can send sequential requests so each uses the returned session version. Show per-file upload progress/error and **Saved**, not analyzed. Do not enqueue preparation until the user clicks Prepare sources.

For each authorized source row, show filename/title, media family, size, revision number, save time, and Include/Exclude. Offer Replace and Revision history. A hash match may show “Same content already added” within the authorized session, but must not silently remove an intentionally separate source.

Keep the existing Catalog import separate and clearly named. Uploading an inventory spreadsheet here must not create any catalog or AI-system records.

### Screen C — Source history

Show immutable revisions with creator/time, size, and current/excluded markers. Offer authorized download of an exact original. For text, show the exact saved text safely; document rendering and extracted previews are deferred.

Do not add rollback-to-revision behavior in this step unless it is implemented as an explicit new current-input selection with the same concurrency rules. Simple history plus replacement is sufficient.

### Screen D — Preparation status

Show Queued, Checking saved sources, Sources saved, retry scheduled, failed, or cancelled using actual server state. Poll the run endpoint while active, with bounded backoff and cleanup on unmount. SSE/WebSockets are unnecessary for this step.

During active preparation, lock input edits and show Cancel preparation. Retry creates a linked new run. After success, show the prepared input revision; replacing a file or editing the prompt changes the workspace back to Draft.

Handle `REVISION_CONFLICT` by preserving the user's unsaved text, refreshing server versions, and asking the user to reconcile. Do not silently overwrite a colleague's changes or blindly retry a conflicting mutation with a fresh version.

After reload, reconstruct all saved state from server APIs. Local state can hold unsaved edits and upload progress; it is not the authority for sources, run status, permissions, or readiness.

## 10. Ordered implementation tasks

### 10.1 Schema, enums, and registration

Create the new onboarding models and bounded schemas. Extend `BackgroundJob` lease metadata and status handling. Add Alembic migration(s) against the **actual current head**, register the model module in `alembic/env.py`, and preserve all existing data. Do not use `create_all()` or hard-code an obsolete migration predecessor.

Add constraints/indexes for ownership, revisions, receipts, and active runs. Define upgrade/downgrade behavior for this migration without attempting to reverse the repository's intentionally irreversible single-organization migration.

### 10.2 Authorization and storage

Seed the source-content permission for existing and fresh installations. Implement metadata/content serializers and authorized content access. Implement the private storage adapter, limits, and cleanup behavior, with temporary filesystem tests.

### 10.3 Session/source services and HTTP contracts

Implement explicit transaction boundaries, version/precondition checks, immutable revisions, include/exclude, idempotency receipts, and input manifests. Register the router in `services/llm_controller.py`. Keep existing onboarding endpoints backward compatible.

### 10.4 Queue ownership and preparation worker

Extend queue operations without changing unrelated worker semantics. Implement run binding, lease renewal/recovery, terminal publication, cancel/retry, safe progress, reserved job types, and bounded cleanup. Avoid FastAPI `BackgroundTasks`, in-process threads, or browser-local queues as durability mechanisms.

### 10.5 UI and deployment

Add the minimal screens and typed client using existing auth/retry conventions. Wire the worker and source volume into supported Compose deployment and update the environment/development instructions.

### 10.6 Validation and documentation

Run the focused tests below, preserve legacy setup coverage, and document the new endpoints, limits, permission, worker command, operational failure states, and backup/restore procedure.

## 11. Deployment requirements

Extend the existing CLI to support these **new commands** while preserving `rai-worker runtime-events`:

```bash
uv run rai-worker onboarding --once
uv run rai-worker onboarding --interval 2
```

`--once` performs a bounded pass and exits. Continuous mode repeats bounded work, handles termination gracefully, and leaves unfinished claims recoverable through leases. Use the same packaged backend image for the new worker service.

Deployment acceptance requirements:

- API and worker use the same database and the same persistent onboarding-source volume/root.
- Schema migration completes before the new worker consumes jobs.
- The volume is writable by the existing non-root `appuser`; do not solve ownership with world-writable permissions.
- Override the Docker image's inherited HTTP healthcheck for the worker. It does not serve the API port. Supply a meaningful worker check or explicitly disable that inherited probe and document monitoring.
- Validate source storage at feature/worker startup and return clear unavailability when writes cannot be persisted. Avoid reporting queued success for a storage failure during upload.
- Keep queued jobs recoverable if the API restarts or the worker is temporarily stopped.
- Update backup/restore guidance to include both PostgreSQL metadata and original-source storage. A database-only restore cannot recreate original file bytes.

Do not introduce Celery, a new broker, or a cloud object-store dependency for this step. Redis remains available for its current responsibilities.

## 12. Required regression coverage

Use focused behavior tests. Concurrency and commit durability need real PostgreSQL sessions, not only mocks or one request session reused by the test client.

### 12.1 Migrations and persistence

- Fresh upgrade creates the new schema; upgrade preserves legacy onboarding and generic jobs.
- Test this migration's downgrade/upgrade against its immediate predecessor in an isolated schema. The existing downgrade-to-base test intentionally expects failure at the single-organization migration; do not rewrite that history.
- Save sources and jobs, close the request session, and read them from a separate session/worker. Verify after a simulated application restart.
- A rolled-back source/run transaction leaves no visible job or revision; an orphan file is handled by cleanup.

### 12.2 Sources and transactions

- Exact byte/text round-trip, byte length, digest, and immutable revisions.
- Prompt-only, file-only, and mixed sessions; empty input is rejected at prepare time.
- Same key/body replay returns the same identity; different body/key conflict is rejected; concurrent identical requests create one logical mutation.
- Two concurrent uploads cannot bypass source-count or retained-byte quotas.
- Stale `expected_row_version` cannot overwrite another user's input; an identical committed retry still succeeds. A late prepare/retry with an old requested input revision cannot process new inputs.
- Replacing/excluding a source invalidates current preparation and preserves old manifests/revisions.
- Path traversal, symlink escape, oversize bodies, unsupported formats, missing originals, and digest mismatches do not produce ready status.
- Chunked uploads, missing/false `Content-Length`, and extra multipart file parts cannot bypass limits before handler-level staging.
- Cleanup removes only eligible unreferenced objects; referenced historical revisions survive exclusion/archive.

### 12.3 Jobs and recovery

- Two workers cannot both own the same eligible claim.
- A committed queued job is processed without another HTTP request or broker dispatch.
- A crashed worker's lease expires and work is reclaimed with a new token; attempts are bounded.
- The stale owner cannot renew, complete, fail, update progress, or publish success after losing ownership.
- Cancelling during work remains terminal even if the worker finishes later.
- Cancel, edit inputs, and prepare again: the old run cannot publish into the new input revision.
- Concurrent prepare/retry requests converge on one active run.
- Job completion and run closure are atomic; dead-letter work closes the active-run guard so users are not permanently locked out.
- Generic job enqueue rejects internal types; injected jobs without valid bindings cannot process sources.
- Preparation does not call an AI provider or change AI systems, catalog resources, rules, budgets, approvals, credentials, or runtime evidence.

### 12.4 Permissions and UI

- Test metadata-only readers, source-authorized managers, technical owners without source access, platform-only users, and expired/revoked memberships.
- Check every list/detail/content/download/history/error/job path for unauthorized content leakage.
- Reject cross-session source/revision/run IDs even within the same installation.
- Verify reload/resume, saved-vs-unsaved prompt state, per-file errors, active-input locking, cancellation, retry, and stale-version reconciliation.
- No fake AI classification or verification status appears after source preparation.
- Legacy Step 01 entry, checklist, draft lifecycle, and Catalog import behavior remain intact.

The repository's standard test override can expose uncommitted objects through the same `db_session`. Add explicit independent-session durability checks rather than treating that as persistence proof. Do not replace PostgreSQL-specific migration/concurrency tests with SQLite.

## 13. Validation commands and completion gate

Use the current lockfiles and `DEVELOPMENT.md`. Suggested focused commands from the repository root, after implementing the proposed files:

```bash
uv run pytest tests/onboarding tests/sdk/test_wp10_sdk.py tests/public_product/test_onboarding.py tests/public_product/test_welcome.py -q
uv run pytest tests/db/test_migrations.py -q
pnpm --dir control-plane-ui typecheck
pnpm --dir control-plane-ui test:unit tests/unit/onboarding-intake.test.tsx
```

`tests/onboarding` and `onboarding-intake.test.tsx` are proposed additions; use the actual added focused test filenames. Add migration-specific tests to the invoked suite as appropriate. Run changed-file Ruff/format checks and the repository's required build/CI gates.

Check `Makefile` and CI test collection: the current `test-unit` target explicitly lists directories, so a new `tests/onboarding` directory must be added where necessary. Do not leave the feature's tests outside routine validation.

Run the new worker's `--once` mode against a controlled test/development database containing a real prepared-input job. Verify the saved originals through the authorized API. If a database, browser, or container dependency is unavailable, report the exact blocked command and do not claim the test passed.

### Definition of done

- [ ] Sessions, original files, prompt revisions, and preparation runs survive restart.
- [ ] Every run points to an immutable manifest and exact original digests.
- [ ] Metadata access does not grant confidential source-content access.
- [ ] Uploads, concurrency, idempotency, and filesystem/DB failure boundaries are handled explicitly.
- [ ] Real workers recover abandoned jobs and reject stale publication.
- [ ] Cancel/retry and terminal run closure cannot permanently lock a session.
- [ ] Minimal UI supports creation, input revision/history, preparation, and resume.
- [ ] Worker, migrations, persistent volume, ownership, healthcheck, and backup instructions work together.
- [ ] Existing Community setup and generic job behavior remain compatible.
- [ ] No AI/domain generation or production-verification claim was added.

### Claude completion report

Return:

1. What an administrator and a metadata-only reader can now do.
2. Changed files grouped by schema/backend, worker/storage, UI, tests, and deployment/docs.
3. Actual new migration IDs and predecessor; new settings, permission, and routes.
4. Worker and storage configuration, plus how restart/retry/cancellation were verified.
5. Exact validation commands and outcomes, distinguishing real database checks from mocks.
6. Any Step 01 integration gap or remaining environmental blocker.
7. A short live walkthrough: create session → upload/add prompt → prepare → reload → inspect saved result.

Stop after Step 02. **Step 03 will implement extraction and AI-provider configuration on top of these saved inputs.** Follow the user's established repository review workflow; this work order does not request production deployment or publication.

## 14. Pinned source references

These references ground the existing-code findings. Proposed tables, routes, permissions, settings, and commands above still require implementation. The current checkout and its applicable instructions take precedence if code has evolved.

- [Job table and legacy onboarding](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/productization.py)
- [Existing database job queue](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/sdk/jobs.py)
- [Generic jobs API](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/sdk/api.py)
- [Worker entry point](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/workers/cli.py)
- [Existing runtime-events worker](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/workers/runtime_events.py)
- [Transactions and schema ownership](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/core/database.py)
- [Router registration](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/llm_controller.py)
- [Authorization](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/core/authorization.py)
- [Role permission seeds](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/admin_console.py)
- [Existing storage configuration](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/core/config.py)
- [Runtime evidence models](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/evidence.py)
- [Policy Library source model](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/policy_library.py)
- [Administrative audit model](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/models/database.py)
- [Alembic model registration](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/alembic/env.py)
- [Migration tests](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/db/test_migrations.py)
- [Shared PostgreSQL test fixtures](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/tests/conftest.py)
- [Test targets](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/Makefile)
- [Container image](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/Dockerfile)
- [Compose deployment](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/docker-compose.yml)
- [Development Compose](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/docker-compose.dev.yml)
- [Existing structured Catalog upload](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/control-plane-ui/components/onboarding/catalog-upload.tsx)
