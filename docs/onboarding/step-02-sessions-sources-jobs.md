# Onboarding Step 02 — Durable sessions, sources and jobs

**Status:** Implemented (Phase 1 of the onboarding roadmap)

An authorized administrator can create an onboarding session, upload supported documents, save a
text prompt, leave, and resume later. Original inputs and every revision persist. A real
background worker runs **Prepare sources**, which checks that the saved originals still exist,
match their recorded size and SHA-256, and are within limits. It does not read their meaning,
call an AI provider, or write catalog, registry, policy, budget, credential or approval records.

## What users see

1. Onboarding → **Prepare onboarding sources** → `/app/onboarding/sessions`.
2. **New session** (a server mutation; opening the page creates nothing).
3. Add files and/or a prompt. Files are saved as-is. The prompt is saved only when you press
   **Save prompt**.
4. **Prepare sources** → Queued → Checking saved sources → **Sources saved**.
5. Leave at any time. Everything is reconstructed from the server on reload.

After success: *"Your selected files and prompt are saved and ready for analysis."* No analysis,
classification, confidence or verification claims are shown.

Session views: `DRAFT`, `QUEUED`, `PREPARING`, `READY_FOR_ANALYSIS`, `PREPARATION_FAILED`,
`ARCHIVED`. Editing a source after success advances `input_revision` and returns to `DRAFT`; the
old result stays in run history. Inputs are locked while a run is queued or running; use
**Cancel preparation** first. Excluding a source and archiving a session are reversible and keep
all revision history.

## Permissions

| Operation | Needs |
| --- | --- |
| List sessions, sanitized progress and counts | `sdk:read` |
| Names, titles, prompt text, revision history, manifests, original bytes | `sdk:read` + `onboarding:sources:read` |
| Create/edit/archive, change sources, prepare/retry/cancel | `sdk:manage` + `onboarding:sources:read` |

`onboarding:sources:read` is seeded for `organization_admin` and `rai_officer`. It is added to
existing installations through the normal role seed on the next permission lookup, and appears in
`/v1/auth/context`. Metadata-only readers get generic labels ("Session 1a2b3c4d", "Source 1"),
sizes, media families, states and timestamps — never file names, titles, prompt text, storage
keys or paths. Content reads are audited; audit targets hold only IDs and reason codes.

## API

Prefix `/v1/productization/onboarding/sessions` (legacy `/v1/productization/onboarding` and welcome
routes are unchanged).

| Route | Purpose |
| --- | --- |
| `POST ""`, `GET ""`, `GET /{id}`, `PATCH /{id}` | Create, list (`archived=`, `limit`, `offset`), view, rename |
| `POST /{id}/archive`, `/restore` | Lifecycle (archive needs no active run) |
| `POST /{id}/sources/files` | Upload one file (multipart: `file`, `expected_row_version`) |
| `POST /{id}/sources/text` | Prompt (`USER_PROMPT`, at most one included) or supporting text |
| `POST /{id}/sources/{sid}/revisions/file` · `/text` | Replacement revision of the same kind |
| `PATCH /{id}/sources/{sid}` | Include / exclude |
| `GET /{id}/sources/{sid}/revisions` · `/{rid}/content` | History · exact original (attachment, `nosniff`) |
| `POST /{id}/prepare` | Create or reuse the run (202 while active, 200 if already succeeded) |
| `GET /{id}/runs`, `/{id}/runs/{rid}` | Run history/detail |
| `POST /{id}/runs/{rid}/cancel`, `/retry` | Cancel active work · explicit linked retry |

Contract rules: every existing-session mutation sends `expected_row_version` (`409
REVISION_CONFLICT` on mismatch); create/upload/revision/prepare/retry require a bounded
`Idempotency-Key` (replays return the original identity; a reused key with a different request is
`409 IDEMPOTENCY_CONFLICT`; an identical committed retry is resolved before the stale version is
checked); prepare/retry also send `requested_input_revision`. Errors are
`{"error": {"code", "message", "retryable", "details?"}}` with codes such as `INPUT_REQUIRED`,
`UNSUPPORTED_MEDIA`, `SOURCE_TOO_LARGE`, `SESSION_LIMIT_EXCEEDED`, `PREPARATION_ACTIVE`,
`RETRY_REQUIRED`, `RUN_SUPERSEDED`, `INPUTS_CHANGED`, `STORAGE_UNAVAILABLE`. Session and run
responses are `Cache-Control: no-store`.

The generic `POST /v1/productization/jobs` rejects the reserved `ONBOARDING_` job-type and
`onboarding:` idempotency-key namespaces, and generic job listings never show payloads, results,
errors or keys for internal jobs.

## Settings (`.env.example`)

| Setting | Default |
| --- | --- |
| `ONBOARDING_SOURCE_STORAGE_ROOT` | `./data/onboarding-sources` (`/var/lib/rai/onboarding-sources` in the image) |
| `ONBOARDING_MAX_FILE_BYTES` | 25 MiB |
| `ONBOARDING_MAX_SOURCES_PER_SESSION` | 20 included sources (prompt included) |
| `ONBOARDING_MAX_SESSION_BYTES` | 200 MiB retained, including older/excluded revisions |
| `ONBOARDING_MAX_TEXT_CHARS` | 50,000 |
| `ONBOARDING_UPLOAD_TIMEOUT_SECONDS` | 600 |
| `ONBOARDING_ORPHAN_MIN_AGE_SECONDS` | 86,400 (must exceed the upload timeout) |
| `ONBOARDING_JOB_LEASE_SECONDS` / `_RENEW_SECONDS` / `_MAX_ATTEMPTS` | 60 / 15 / 5 |

Invalid combinations fail at startup. Supported originals: PDF, DOCX, XLSX, CSV, TXT, Markdown.
Type is decided from extension, declared MIME and bounded signature/container checks, never the
browser MIME alone. Nothing is parsed or executed. Request bytes and duration are bounded by an
ASGI middleware **before** multipart parsing, regardless of `Content-Length`.

## Worker and storage

```bash
uv run rai-worker onboarding --once        # one bounded pass, then exit
uv run rai-worker onboarding --interval 2  # repeat until SIGTERM
uv run rai-worker onboarding --healthcheck # exit 0 while the loop is alive
```

`rai-worker runtime-events` is unchanged. Compose runs `rai-onboarding-worker` from the same image,
the same database and the same `onboarding_sources` volume as the API; it starts after the API is
healthy (migrations done) and has its own healthcheck. The volume is owned by the non-root
`appuser`. The PostgreSQL row is the durable queue: jobs survive API restarts and a stopped
worker. Execution is **at least once with ownership-checked publication**: claims carry a lease
token, expired leases are reclaimed with a new token, a stale owner cannot renew, complete, fail,
or publish, repeated crashes dead-letter and close the run, and cancel is terminal.

Storage is a private filesystem adapter behind a small interface. Keys are server-generated,
originals are never overwritten, paths are contained under the root with symlinks rejected, and
the worker removes only stale staging files and finalized objects with no revision row (re-checked
immediately before deletion). It is not S3/MinIO; the global `OBJECT_STORAGE_BACKEND` flag is not
used.

## Failure states

| Condition | Result |
| --- | --- |
| Storage not writable on upload | `503 STORAGE_UNAVAILABLE`, nothing saved |
| Original missing / digest or size changed | Run fails `SOURCE_MISSING` / `SOURCE_INTEGRITY_FAILED`; never "ready". Restore the file, then **Retry** |
| Temporary storage/database error | Scheduled retry with backoff; run stays active until attempts are exhausted |
| Worker not running | Run stays **Queued** ("Waiting for a worker") |

## Backup and restore

Back up PostgreSQL **and** the source volume together — see
[Backup, restore, and disaster recovery](../operations/BACKUP_RESTORE_DR.md).

## Not in this step

Text/PDF/DOCX/XLSX extraction, OCR, AI provider configuration, classification, generated catalog
entries, proposed rules and budgets, approval automation and runtime verification. Step 03 builds
extraction and AI-provider configuration on top of these saved inputs.
