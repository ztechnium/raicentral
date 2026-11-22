# Onboarding Step 03 — Document extraction and explicit AI configuration

**Status:** Implemented (Phase 2 of the onboarding roadmap)

Saved onboarding documents become readable, traceable source material. Extraction is
deterministic and works with AI disabled. The only model call introduced here is an explicitly
requested **synthetic connection test**: no uploaded file, extracted block, filename or
organization prompt is ever sent to a model in this step.

## What users see

- After **Prepare sources**, **Extract text and tables** runs a second background stage.
- Each included source shows its own state: *Text extracted*, *Partially extracted*, *Needs OCR*,
  *Needs parsing options*, *Unsupported*, *Failed* or *Not processed*, with real coverage (for example
  "3 of 4 pages extracted"). A finished worker with problem sources reads **Needs attention**.
- **Preview text** pages through the exact stored blocks with native locators; **Cite excerpt**
  resolves a code-point range to the exact source revision, digest and locator.
- Parsing issues show concrete next steps (choose encoding or delimiter, provide a searchable PDF,
  replace or exclude). There is no OCR button, password prompt or AI repair.
- `/app/onboarding/ai-setup` configures and tests the AI connection. Parsing never depends on it.

## Stages and runs

`EXTRACT_SOURCES` / `ONBOARDING_EXTRACT_SOURCES` are added next to `PREPARE_SOURCES`. An extraction
run names the exact successful preparation run it extends (`preparation_run_id`), freezes an
extraction **plan** (parser fingerprint, limits digest, per-source effective options and identity)
and its digest, and creates one `onboarding_run_sources` row per manifest source up front.

- Current-run selection is **stage-aware** (stage + input revision + run number + parent). A later
  extraction run never replaces the preparation run for preparation eligibility.
- One active run per session across stages; edits are blocked while one is active (cancel first).
  Provider tests are not session runs and never lock source editing.
- The session view adds `preparation`, `extraction` and `ai_configuration` sections. The Step 02
  `view` (`READY_FOR_ANALYSIS`) still only means "sources saved/prepared". Generation eligibility
  needs `extraction.coverage_acceptable`: current run, exact preparation parent, every source
  accounted for, every source `COMPLETE`.
- Manifests are versioned: v1 (Step 02) stays readable and unchanged; v2 adds each source's
  parsing-option snapshot. A v1-only preparation requires a new preparation before extraction.
- Before a run is terminal, every unattempted source is finalized `NOT_PROCESSED`
  (`RUN_CANCELLED`, `RUN_ABORTED`, `RUN_OUTPUT_LIMIT`); no pending rows remain under a terminal job.

## Extraction identity and immutability

Cache reuse requires the whole identity: source revision id + original SHA-256 + extractor build +
dependency versions + schema/normalization versions + effective options (defaults included) + limits
digest. Parser upgrades, option changes or limit changes therefore produce a new extraction.
Results are never shared across sessions or revisions. Blocks are immutable and stored in
PostgreSQL (`onboarding_extracted_blocks`, unique ordinal per extraction). Each source publishes
atomically under the lease/ownership/input-revision/stage fences. Transient or unattributable
failures (`PARSER_CRASHED`, missing/changed originals) are recorded on the run only and never cached
as a reusable success.

## Parsers (`rai_gateway/onboarding/parsers/`)

| Format | Library | Locator | Notes |
| --- | --- | --- | --- |
| PDF | pypdf | physical page (1-based) | Text as observed; scanned → `NEEDS_OCR`; mixed → `PARTIAL`; blank → `NEEDS_INPUT`; encrypted → `UNSUPPORTED` |
| DOCX | python-docx | part + paragraph/table/row/cell path | Document order, nested/merged cells once, distinct headers/footers; footnotes, endnotes, comments, text boxes and tracked changes are reported as gaps (`PARTIAL`) |
| XLSX | openpyxl | sheet index/name + cell | All sheets incl. hidden; formula text and cached value kept separate (`PRESENT_UNVERIFIED`/`MISSING`, never zero); strings stay strings; no recalculation, macros or external links |
| CSV | stdlib `csv` | record/column + physical lines | Strict UTF-8/BOM; explicit Windows-1256 override; ambiguous delimiter → `NEEDS_INPUT`; first row preserved unless asked |
| TXT / Markdown / prompt / pasted text | — | line range | Strict decode, LF + BOM normalization recorded; braces/HTML/links stay literal |

Parsing options (`PATCH …/sources/{id}/parsing-options`) are bounded: `encoding`
(`utf-8`, `windows-1256`; uploaded files only), `delimiter`, `header_mode`. Changing them advances
`input_revision` and `row_version`, never touches original bytes or digests, and requires a new
preparation and extraction.

**Known coverage limits:** no OCR; no PDF layout/reading-order guarantee or bounding boxes; no Word
page numbers; Word comments/footnotes/endnotes/text boxes/tracked changes are reported, not
extracted; XLSX is read from stored values (no recalculation); legacy `.doc`/`.xls` are unsupported.

## Isolation and limits

Parsing runs in a **killable child process** (`python -m rai_gateway.onboarding.parsers.child`)
with a minimal environment (no provider keys, database URL or proxies). The parent keeps lease
renewal and cancellation; on timeout, output overflow, cancellation or ownership loss it kills and
reaps the child. On POSIX the child applies `RLIMIT_AS`/`RLIMIT_CPU` and reports whether they are
active; **on Windows memory/CPU rlimits are not enforced** (wall time and output bounds still are).
Production runs the Linux image.

| Setting (all `ONBOARDING_*`) | Default |
| --- | --- |
| `PARSER_WALL_SECONDS` / `PARSER_MEMORY_BYTES` | 120 s / 512 MiB |
| `PDF_MAX_PAGES` | 300 |
| `OOXML_MAX_ENTRIES` / `_MAX_TOTAL_BYTES` / `_MAX_MEMBER_BYTES` / `_MAX_RATIO` | 10,000 / 100 MiB / 25 MiB / 100:1 |
| `XLSX_MAX_SHEETS` | 50 |
| `TABULAR_MAX_ROWS` / `_MAX_COLUMNS` | 10,000 / 200 |
| `MAX_CELLS_VISITED` / `_MAX_NONEMPTY_CELLS` | 200,000 / 50,000 |
| `EXTRACT_OUTPUT_BYTES_PER_SOURCE` / `_PER_RUN` | 16 MiB / 64 MiB |

Limits are part of the extraction fingerprint. The API and worker must use identical values: the
worker compares the plan's fingerprint and limits digest with its own and fails with
`PARSER_PROFILE_MISMATCH` instead of parsing under another configuration. Hitting a limit yields an
explicit `PARTIAL`/`FAILED` outcome with a recorded gap, never silent truncation. OOXML archives are
preflighted (entry count, expansion size/ratio, path safety) and parsed with `defusedxml`.

## Provenance and citations

`GET …/citations/{block_id}?start&end` returns the source revision and SHA-256, the extraction
identity, the native locator, the exact excerpt and `is_current_input` / `historical`. Offsets are
**zero-based, start-inclusive/end-exclusive Unicode code points of the stored block text** (not bytes,
PDF coordinates or UTF-16 indexes). IDs, session membership and range bounds are validated
server-side; historical and excluded-source citations stay readable and are marked historical.

## AI configuration

One adapter, `OPENAI_COMPATIBLE_CHAT` (non-streaming Chat Completions, `JSON_SCHEMA` or
`JSON_OBJECT`). There is no default provider/model, ambient key, fallback or mock.

- **Targets** are deployment-approved: `ONBOARDING_AI_TARGETS_JSON` (a JSON list) binds a key to
  protocol, exact `https` base URL, allowed models and structured modes, token parameter,
  onboarding-only credential alias and `credential_version`, timeout and network policy. Invalid
  entries (non-https, URL credentials/query, metadata hosts, other protocols) are dropped.
- **Credentials**: alias `X` resolves to env var `ONBOARDING_AI_KEY_X` through the existing
  low-level resolver. The form cannot name an env var or URL, and `LLM_PROVIDER_API_KEY` is never
  used. Rotate by changing the secret and bumping `credential_version`.
- **Save** (`PUT /ai-configuration`) is version-checked and makes no external call.
- **Test** (`POST /ai-configuration/test`) creates one probe + `ONBOARDING_TEST_PROVIDER` job in a
  single transaction. Concurrent clicks reuse the active probe; a new key after completion is a
  deliberate retest. The worker validates the probe/job/config binding and that its effective target
  fingerprint equals the frozen one **before** resolving a secret, validates and **pins** the
  destination (rejects metadata, loopback, private, link-local, multicast, reserved and IPv4-mapped
  addresses unless the target explicitly allows private networks; metadata is never allowed), then
  commits `dispatch_started_at` (the authorization point) after re-locking configuration and job.
  It sends once (no retries, no redirects, TLS verified, 64 KiB response cap, 30 s deadline) and
  validates the reply locally (schema, nonce, refusal, truncation).
- A lost response after the marker is `OUTCOME_UNKNOWN` and is never resent; expired probe jobs are
  terminalized by the reaper. Current status uses the latest probe for the exact configuration
  version **and** effective policy fingerprint, so older/stale tests never read as current.
- A passed test means only that the configured target/model/credential returned the synthetic
  response at that time. Usage is recorded when returned; missing usage is unknown, not zero.

## API and permissions

New under `/v1/productization/onboarding`: `GET /capabilities`, `GET|PUT /ai-configuration`,
`GET /provider-targets`, `POST /ai-configuration/test`, `GET /ai-configuration/tests[/{id}]`,
`POST /ai-configuration/tests/{id}/cancel`; and under `…/sessions/{id}`:
`PATCH /sources/{sid}/parsing-options`, `POST /extract`, `GET /extractions`,
`GET /extractions/{eid}/blocks`, `GET /citations/{block_id}`.

`onboarding:providers:manage` (seeded for `organization_admin`, also needs `sdk:manage`) gates
configuration, targets, tests and cancellation. Extracted content, blocks, citations, options and
warnings need `sdk:read` + `onboarding:sources:read`; metadata-only readers see counts and states
only. Generic job listings never show payloads, results, errors or keys for internal jobs.

## Operating it

`rai-worker onboarding` handles all three job types. Keep `ONBOARDING_AI_TARGETS_JSON` and the
parser limits identical for the API and worker (Compose passes them to both). Parser children never
receive provider secrets. Backup/restore is unchanged from Step 02 (database + source volume).

## Not in this step

OCR execution, semantic classification, inventory/governance proposals, budgets, embeddings or
vector search, crawling, domain application, approvals, runtime verification. Step 04 uses these
extracted blocks and the configured adapter to generate reviewable inventory proposals.
