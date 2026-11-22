# RaiCentral AI Onboarding — Step 03: Document Extraction and Explicit AI Configuration

> **Implementation work order for Claude.** Implement this step in the existing RaiCentral repository. Deliver the parsers, provenance contracts, worker integration, provider configuration/test flow, UI, migrations, and focused tests described below. Return implementation results rather than another plan.

## 1. Outcome, baseline, and boundaries

**Outcome:** saved onboarding documents become readable, traceable source material. An administrator can inspect extracted text and tables, open the exact source location behind an excerpt, resolve parsing problems, and explicitly configure/test the AI connection that later proposal generation will use.

This is **implementation Step 03**, corresponding to **Phase 2 — File/text parsing and explicit AI-provider configuration** in the roadmap.

| Previous/next step | Responsibility |
| --- | --- |
| Step 01 | Community entry, permissions, truthful progress, and explicit lifecycle approval |
| Step 02 | Durable sessions, immutable originals, input revisions, storage, and recoverable jobs |
| **Step 03** | Deterministic extraction, real source locators, extraction review, and explicit AI setup |
| Step 04 | Inventory discovery, reconciliation, and reviewable AI-system/catalog proposals |

Repository: https://github.com/ztechnium/raicentral

Reviewed `main`: **`0c052350db88c834e79c752284e264a68d7c6210`**, checked on **19 November 2026**. The revision remains unchanged from the earlier briefs; this does not establish that Steps 01–02 are implemented or merged. Source inspection and primary parser documentation informed this work order; application tests were not executed while writing it.

Read the applicable `AGENTS.md`, inspect the current branch and working tree, and read:

- `raicentral-ai-onboarding-step-01-foundations.md`
- `raicentral-ai-onboarding-step-02-sessions-sources-jobs.md`

Integrate with their actual implementation. Preserve unrelated work and report prerequisite gaps. Do not reset the checkout to the reviewed revision.

### Scope decision

Extraction is deterministic and works with AI disabled. The only external model call introduced here is an explicitly requested **synthetic connection test**. No uploaded source or organization prompt is sent to a model in this step.

Do not implement semantic document classification, inventory proposals, RAI-rule generation, budget generation, embeddings/vector search, crawling, domain application, automatic approvals, or runtime verification. OCR execution is deferred; scanned content must receive an honest needs-attention outcome.

The coding agent must not create dummy AI systems, runtimes, trajectories, or evaluator configurations to make extraction/provider testing work.

## 2. Existing code: reuse and avoid

Paths are relative to the repository root. Pinned references appear at the end.

| Existing code | Verified behavior | Implementation decision |
| --- | --- | --- |
| `registry/catalog_import.py` | Requires the structured Catalog schema and immediately upserts resources; uses `openpyxl` | Preserve it separately. General extraction must not invoke it |
| `policy/library.py::PolicyLibraryService.create_source()` | Stores supplied text/metadata and creates governance state; manual text can create an obligation stub | Do not use it as an onboarding document parser |
| `pyproject.toml` | Includes `openpyxl`; does not include `pypdf` or `python-docx` at the reviewed revision | Add required parser dependencies and update the lockfile |
| `services/llm_controller.py::_provider_candidates()` | Adds OpenAI as a terminal fallback | Keep onboarding outside this execution path |
| `services/llm_providers.py` | Uses ambient settings/defaults; the OpenAI adapter applies `prompt.format(...)` | Do not format uploaded text as a template or inherit provider defaults |
| `services/provider_settings_store.py` | Stores settings, including API-key values, as strings and mutates global runtime settings | Do not store new onboarding secrets here or change runtime defaults |
| `provider_gateway/credentials/secret_resolver.py` | Provides a low-level resolver; only `ENVIRONMENT` is installed by default | Reuse after checking an approved onboarding-only credential alias |
| `credentials/isolated_resolver.py` and `materialize.py` | Can fall back to settings when runtime context or endpoint values are absent | Do not call these as a pre-runtime onboarding shortcut |
| `provider_gateway/transport.py::UpstreamTransport` | Bounded HTTP transport, timeout handling, no redirects, configurable retries | Reuse appropriate low-level behavior with explicit configuration |
| `provider_gateway/production/upstream_guard.py` | URL/DNS checks use existing gateway/MCP destination policy | Add the onboarding destination boundary; transport alone does not authorize a URL |
| Step 02 onboarding models/services/worker | Original revisions, manifests, receipts, leases, source permissions | Extend these foundations; do not add a second queue or file store |

Existing runtime credential references and governance evidence are separate domains. Step 03 should not invent runtime ownership for intake files or assume cloud-secret backends exist merely because their enum values exist.

## 3. Extend Step 02 without confusing stages

Add `EXTRACT_SOURCES` to session run stages and the internal job type **`ONBOARDING_EXTRACT_SOURCES`**. Preserve `PREPARE_SOURCES` / `ONBOARDING_PREPARE_SOURCES` as the original-byte preparation operation.

An extraction run must reference an exact successful preparation run for the same session and current input revision. Freeze the extraction plan and source manifest at enqueue time.

### 3.1 Make current-run selection stage-aware

Step 02 uses a monotonically assigned `run_number`. With multiple stages, resolve current results by **stage + input revision + run number**, with the required parent relationship. A later extraction run must not replace the preparation run when checking preparation eligibility.

Keep the one-active-session-run constraint across stages. Source/options edits remain blocked during active preparation or extraction; users cancel first. Provider connection tests are separate from session runs and do not lock source editing.

Expose separate summary sections:

| Section | What it reports |
| --- | --- |
| `preparation` | Whether the exact originals/current input manifest were prepared |
| `extraction` | Current extraction execution state, per-source outcomes, coverage, and attention count |
| `ai_configuration` | Disabled/unconfigured, configured but untested, test pending, last test passed/failed/unknown/stale |

Keep existing Step 02 fields readable with their documented meaning. Its `READY_FOR_ANALYSIS` value meant sources were saved/prepared; it must not become proof of parsed content or a tested model. Use the new summaries for richer UI and future generation prerequisites.

### 3.2 Parsing options are versioned inputs

Add validated `parsing_options` and `parsing_options_version` to logical sources. The initial user overrides are bounded text/CSV options: encoding, delimiter, and header mode. Do not accept arbitrary parser classes, command-line arguments, URLs, or executable options.

Encoding overrides apply only to appropriate uploaded `FILE` text/CSV/Markdown originals. Step 02 `TEXT` sources, including saved prompts and pasted text, are always exact UTF-8 and must remain UTF-8; do not reinterpret them through a legacy encoding override.

Changing options increments the session `input_revision` and editable `row_version`, using the Step 02 concurrency/idempotency rules. It does not modify original bytes or source-revision digests. Freeze actual options and their version/digest in every new run manifest.

Version the manifest format. Preserve existing Step 02 manifests/digests as historical version 1; new manifests include source parsing-option snapshots. An older preparation run lacking these snapshots remains readable but requires a new preparation before extraction. Do not rewrite historical hashes or silently reinterpret their proof.

Parser upgrades or deployment-limit changes also affect extraction identity. They do not change the user's original source input revision; a new extraction plan/fingerprint distinguishes the new processing. A matching current preparation can be reused when its source/options manifest still matches.

## 4. Extraction data model and publication

These are proposed additions to the Step 02 onboarding model module. Adapt names only when the current repository already provides an equivalent contract.

| Model | Required data |
| --- | --- |
| `OnboardingSourceExtraction` | UUID; immutable source-revision FK and original digest; extractor identity/dependency versions; extraction schema/normalization version; effective-options snapshot/digest; unique extraction-identity digest; source outcome; coverage and warning metadata; creation time |
| `OnboardingExtractedBlock` | UUID; extraction FK; unique ordinal within extraction; block kind; immutable extracted text; optional typed cell data; native locator; content digest; bounded warning codes |
| `OnboardingRunSource` | One row per manifest source revision per extraction run; nullable extraction FK; pending/final outcome; safe error code; coverage/counts; timestamps |

Extend `OnboardingRun` with the preparation-parent reference and immutable extraction-plan snapshot/digest. Reuse `BackgroundJob` for execution authority and Step 02 mutation receipts. Register new models with Alembic and add migrations against the actual current head.

### 4.1 Full extraction identity

Use this complete identity for cache/retry reuse:

```text
source_revision_id + original_sha256
+ extractor implementation/build version + relevant dependency versions
+ extraction schema/normalization version
+ effective parsing options and resource/coverage policy digest
```

Effective options include defaults as well as user overrides: decoding, CSV policy, hidden-sheet treatment, parser mode, limits, and supported story-part policy. Do not cache only by filename, source ID, or file hash. Do not deduplicate across independently authorized source revisions/sessions.

Record an outcome for every manifest source, including empty, unsupported, OCR-dependent, and failed inputs. Missing outcome rows never count as successful coverage.

Create the run-source accounting rows when the extraction run is enqueued. Before a run becomes terminal, finalize any unattempted remainder as a run-only `NOT_PROCESSED` outcome with a safe reason such as `RUN_OUTPUT_LIMIT`, `RUN_CANCELLED`, or `RUN_ABORTED`. Do not leave pending source rows under a terminal job or cache an unattempted source as a parser failure.

### 4.2 Content storage and immutable results

Store extracted blocks in PostgreSQL using bounded Text/JSON fields and pagination. Keep original bytes in the Step 02 store. Avoid duplicating the full original into block JSON or introducing a vector database.

A source extraction and its blocks publish atomically after the worker checks current lease ownership, input revision, stage, run number, and source binding. Completed per-source artifacts may be reused after a worker restart, but they do not make a session ready on their own.

Final run publication/closure is atomic and checks the complete manifest. Artifacts from a cancelled/superseded run remain historical; a valid current run must explicitly reuse them and record its own outcomes before they contribute to current readiness.

Do not persist transient storage/IO failure as a permanently reusable successful extraction. Permanent format/coverage results can be reused only under an identical identity. Changing parser code, limits, or options must permit a genuinely new attempt.

### 4.3 Source outcomes versus job execution

| Source outcome | Meaning |
| --- | --- |
| `COMPLETE` | Supported text/table extraction finished without a known coverage gap under the declared parser policy |
| `PARTIAL` | Some usable content exists, but known pages/parts/regions were not extracted |
| `NEEDS_OCR` | Image/scanned content requires recognition that this step does not perform |
| `NEEDS_INPUT` | Encoding/dialect/options or an empty/no-usable-content condition needs user attention |
| `UNSUPPORTED` | The document requires an unsupported feature, such as the deferred encrypted-document workflow |
| `FAILED` | Parsing/integrity/resource processing failed with a safe reason code |

A job may be `SUCCEEDED` because it finished recording all source outcomes while the session still needs attention. “Complete” describes supported extraction, not the truth, compliance, or completeness of the organization's policies.

Future generation eligibility must require a current successful extraction run, its exact current preparation parent, every included source accounted for, and acceptable coverage. In this step, do not add a bypass that automatically treats partial/OCR-dependent sources as complete. Users can replace, fix options, or explicitly exclude a source and prepare/extract the revised inputs.

## 5. Format-specific parser requirements

Implement a pure parser registry under the onboarding module. Parsers consume an authorized immutable source revision and frozen options; they return bounded blocks, coverage, and diagnostics. They never call providers or domain mutation services.

Add `pypdf`, `python-docx`, and `defusedxml`; reuse `openpyxl` and Python's `csv`. Pin compatible versions through the repository lockfile. Official references below explain API behavior; do not assume a documentation site's displayed version is the installed version.

### 5.1 PDF

Use `pypdf` page by page. Preserve physical page boundaries and one-based page numbers. Extract text as observed by the parser; do not guess table structure or claim reliable visual reading order for every PDF. The project documents both extraction limitations and large decompressed content streams. [PDF extraction reference](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)

Record per-page coverage. Image-only/scanned pages needing recognition produce `NEEDS_OCR`; a mixed document with usable and unprocessed pages produces `PARTIAL`. A genuinely empty document is `NEEDS_INPUT` / `NO_USABLE_TEXT`, not automatically an OCR case.

For encrypted/password-required documents, return an explicit unsupported/password-required reason and guidance to provide a permitted searchable replacement. Do not collect passwords, decrypt opportunistically, send the file to an external OCR/model service, or fabricate missing text. [PDF encryption reference](https://pypdf.readthedocs.io/en/stable/user/encryption-decryption.html)

### 5.2 DOCX

Use document-order paragraph/table iteration and recurse into table cells; do not concatenate all paragraphs followed by all tables. Preserve nested structure and handle merged cells without duplicating the same underlying content. [Document API](https://python-docx.readthedocs.io/en/latest/api/document.html) · [Table API](https://python-docx.readthedocs.io/en/latest/api/table.html)

Support the main document body and nested tables. Include distinct header/footer parts where supported, with explicit part locators and no duplication of shared parts. Detect/report omitted footnotes, endnotes, comments, text boxes, tracked revisions, or other unsupported parts containing content. Known omissions must appear in coverage rather than silently producing `COMPLETE`.

Use document-part plus structural paragraph/table/cell paths as locators. Do not invent Word page numbers without a rendering/layout process. Report tracked-change ambiguity instead of silently deciding which policy wording is authoritative.

### 5.3 XLSX

Read all worksheets, including hidden/very-hidden sheets, under an explicit fixed coverage policy. Preserve sheet index/name, visibility, row/column coordinates, relevant hidden-row/column flags, cell type, number format, and merge anchors. If any region is omitted or exceeds limits, record the gap.

Preserve formula text separately from its stored cached value. `data_only=True` exposes saved values rather than recalculating formulas; use the formula and value views deliberately. Mark a cache `MISSING` or `PRESENT_UNVERIFIED`; never substitute zero for a missing value. [Workbook reader](https://openpyxl.readthedocs.io/en/stable/api/openpyxl.reader.excel.html) · [Formula behavior](https://openpyxl.readthedocs.io/en/stable/simple_formulae.html)

Never execute macros, recalculate formulas, follow external workbook links, or create registry records. Keep numeric-looking string identifiers as strings. Do not infer currencies, business units, owners, or headers as established facts. Preserve number-format metadata and expose values as stored/extracted, without claiming to reproduce Excel's full visual formatting.

Read-only workbooks must be closed. Validate apparent sheet dimensions and bound actual iteration, including empty cells; a misleading dimension must not trigger an enormous scan or silently hide content. Do not blindly forward-fill merged blanks. [Read-only mode and dimensions](https://openpyxl.readthedocs.io/en/stable/optimized.html)

### 5.4 CSV

Use Python's `csv` reader with appropriate newline handling and string-preserving values. Handle quoted delimiters and multiline records. Keep positional column indexes even when names are missing or duplicated. [CSV reference](https://docs.python.org/3.12/library/csv.html)

Use strict UTF-8/BOM handling by default. Provide an explicit, validated Windows-1256 override for Arabic legacy exports; never silently decode arbitrary bytes as Latin-1 or drop invalid characters. Allow only bounded supported delimiters such as comma, semicolon, tab, and pipe.

Heuristics may propose a delimiter, but ambiguity returns `NEEDS_INPUT`. Default header mode preserves every record; using the first row as headers requires an explicit option. Do not discard a row merely because a heuristic guessed it was a header.

Record logical record number, column index, and physical line span where tracked. Formula-like strings remain literal strings, including values beginning with `=` or `+`.

### 5.5 TXT, Markdown, prompt, and pasted text

Decode strictly, segment deterministically, preserve line numbers, and retain exact extracted block strings. The original bytes remain immutable. If normalizing line endings/BOM handling for extracted text, record the transformation and version it; do not normalize away meaningful Unicode or Arabic diacritics.

Markdown remains source text. Do not execute HTML, fetch image links, follow URLs, or turn instructions embedded in a document into agent authority. Preserve braces and other syntax literally; no `.format()` or template evaluation of source content.

### 5.6 Arabic and mixed-language behavior

Preserve Arabic letters, diacritics, punctuation, Arabic/Western digits, numbered clauses, and embedded English names. Do not reverse strings or translate/transliterate during extraction. Expose extraction problems; do not have an LLM repair text while presenting the repair as original evidence.

Use direction-aware rendering (`dir="auto"` and bidi isolation for labels/locators) while retaining logical text order. Test Arabic/English tables, emoji, combining characters, multiline cells, and a PDF with challenging reading order.

## 6. Provenance and citation contract

Every block must resolve to:

1. The original source and exact source revision/digest.
2. The extraction identity and parser/options versions.
3. A native source locator.
4. The exact stored extracted text, and an optional validated range within it.

| Format | Native locator |
| --- | --- |
| PDF | Physical page number, starting at 1 |
| DOCX | Document part and structural paragraph/table/row/cell path |
| XLSX | Sheet index/name and exact cell or explicitly bounded range |
| CSV | Logical record/column indexes and physical line span |
| Text/Markdown/prompt | Original logical line range under the recorded newline policy |

Do not manufacture PDF bounding boxes, Word pages, or spreadsheet column names. Display one-based human coordinates; separately define text offsets as **zero-based, start-inclusive/end-exclusive Unicode code-point offsets in the exact stored block text**.

Those offsets are not original-file byte positions, PDF coordinates, or JavaScript UTF-16 indexes. The server should return the excerpt itself. Frontend highlighting must use the same coordinate system; test emoji and Arabic combining marks.

Example citation response shape, using placeholder values rather than fixture claims:

```json
{
  "source_revision_id": "uuid",
  "source_sha256": "original-digest",
  "extraction_id": "uuid",
  "block_id": "uuid",
  "locator": {
    "type": "XLSX_CELL",
    "sheet_index": 1,
    "sheet_name": "AI Inventory",
    "cell": "B7"
  },
  "text_range": {"start": 0, "end": 7, "unit": "UNICODE_CODE_POINT"},
  "excerpt": "Support",
  "is_current_input": true
}
```

Validate all IDs, membership, range bounds, and publication state server-side. Historical/excluded-source citations remain accessible to authorized users but are clearly marked historical; they cannot silently become current input.

A quote is an exact excerpt of stored extraction output. The UI must distinguish that from original binary bytes or a claim that the parser perfectly reproduced a complex document's visual layout.

## 7. Parser execution, resource bounds, and recovery

Run parsing in a **killable child process**, not a thread whose timeout leaves work running. Keep lease renewal and cancellation in the parent onboarding worker. Give the child only the authorized original and minimal parser configuration; do not inherit provider secrets or expose network/tool capabilities.

Bound CPU/wall time, address-space/memory, emitted output, and temporary disk. Use the deployment platform's supported process limits and verify they are active. Terminate and reap the parser process on cancellation, ownership loss, or timeout. A lease expiring must not leave an uncontrolled parser behind.

Initial configurable defaults, proposed for implementation:

| Boundary | Default |
| --- | --- |
| Parser wall time per source | 120 seconds |
| Parser memory per source | 512 MiB |
| PDF pages | 300 |
| OOXML ZIP entries / total expanded bytes | 10,000 entries / 100 MiB |
| OOXML individual expanded member / ratio | 25 MiB / 100:1 |
| XLSX worksheets | 50 |
| Tabular records/rows and columns | 10,000 rows per sheet/CSV; 200 columns |
| Cells visited / nonempty cells per source | 200,000 / 50,000 |
| Serialized extracted output per source / run | 16 MiB / 64 MiB |

Make limits part of the effective extraction fingerprint and documentation. Reject or report explicit incomplete/resource outcomes when hit. Do not silently truncate content and mark it complete. Avoid huge single blocks; segment with precise locators/ranges without losing or repeating text.

Preflight OOXML archives under limits; never unpack arbitrary member paths into the filesystem. Install `defusedxml`, disable external entity/network resolution, and reject unsafe XML constructs. The openpyxl documentation specifically identifies XML expansion protection as an additional requirement. [openpyxl security](https://openpyxl.readthedocs.io/en/stable/)

Verify original byte size/digest before parser or cache use. Parser code/options on the worker must match the frozen plan; deployment disagreement returns an explicit mismatch error, not execution under another parser configuration.

Retain Step 02's short transactions, conditional lease renewal, bounded retries, and final ownership checks. Known bad input/resource outcomes should not be retried indefinitely. Transient storage failures can retry within the existing policy; retry must not duplicate published blocks.

Cache source results only through a fenced publication transaction. Enforce unique extraction identity and block ordinals in the database. Incremental source completion may survive a crash, but final session readiness requires the current completed run and all its explicit outcomes.

## 8. Explicit onboarding AI configuration

### 8.1 Deliberately bounded provider support

Implement one initial adapter: **`OPENAI_COMPATIBLE_CHAT`**, using non-streaming Chat Completions protocol and bounded structured responses. This is a protocol choice, not selection of OpenAI as the organization's provider. The endpoint and model must be explicitly configured.

Do not expose native Anthropic, Gemini, Vertex, Bedrock, or other options as working onboarding integrations unless their exact adapters and tests are actually implemented. Existing runtime adapter names do not establish that support for this new workflow.

Support explicit `JSON_SCHEMA` or `JSON_OBJECT` mode only when allowed by the configured target/model. Prefer schema mode where supported. JSON object mode supplies JSON syntax constraints and still requires local schema validation; it is not equivalent to provider-enforced schema adherence. Unsupported mode must fail without automatic downgrade. [Protocol reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) · [Structured-output modes](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)

No default provider/model, ambient-key lookup, fallback provider, automatic mock, or curated model-list fallback is permitted in the onboarding path.

### 8.2 Deployment-approved targets and secrets

Define a deployment-managed target registry, for example `ONBOARDING_AI_TARGETS_JSON`. Each target binds a stable key to:

- Protocol and exact approved base URL.
- Allowed model identifiers and supported request/structured-output options.
- An onboarding-only credential alias and operator-supplied credential version.
- Explicit timeout, output, TLS, and destination policy bounds.

The backend maps the alias to an allowlisted environment secret reference using the existing low-level resolver. Do not accept an arbitrary environment-variable name from the UI, and never fall back to `LLM_PROVIDER_API_KEY` or another runtime credential.

An environment-backed credential is the initial Community implementation. Raw keys are provisioned through deployment configuration, not submitted through the new form or stored in `ProviderSetting`, profile rows, browser storage, receipts, logs, or jobs. Operators must advance the credential version on rotation. Do not claim unimplemented Vault/cloud-secret support.

Expose only sanitized target choices, model options, and destination labels. These are deployment-configured choices, not live model discovery. An admin cannot redirect an approved credential by entering another URL in the onboarding form.

### 8.3 Configuration and probe models

| Proposed model | Required data |
| --- | --- |
| `OnboardingAIConfiguration` | Singleton key; integer configuration version; enabled flag; selected target key/model; allowed structured-output mode and bounded request options; updater/timestamps. No raw secrets |
| `OnboardingProviderProbe` | UUID; monotonic probe number; configuration version; immutable nonsecret effective target/configuration snapshot and fingerprint; credential alias/version; nonce; unique job FK; nullable dispatch-start timestamp; outcome/safe error metadata; usage/latency if available; nullable ended timestamp |

Initial state is disabled/unconfigured. Saving configuration is explicit, increments its version, and invalidates current-test matching for the old version. Saving does not make an external request.

Enforce unique probe numbers/job bindings and one active probe per configuration-version/target-fingerprint pair with database constraints and a short configuration-row lock. Probe outcome, job terminal state, and probe `ended_at` finalize atomically, including reaper/cancellation paths. A newer configuration can be tested while an older dispatched probe is being terminalized; the old result remains historical.

Fingerprint the effective deployment target policy as well as database configuration: protocol, approved endpoint, model/request options, credential alias/version, and network policy. API and worker must agree on this fingerprint before secret resolution/network activity. Configuration changes in environment/deployment must not be certified by an old database-version-only test.

Current AI setup uses the latest relevant probe by deterministic probe number for the exact current configuration/policy fingerprint. A delayed old success cannot overwrite a newer failed/unknown retest. Historical test results remain visible as history, not current availability guarantees.

### 8.4 Adapter and outbound contract

The adapter receives a fully resolved immutable configuration and explicit messages/schema. It must not call the legacy provider factory, candidate chain, runtime-context fallback resolver, or global settings mutator.

Reuse low-level HTTP infrastructure only with explicit TLS verification, no redirects, bounded response/time limits, and no automatic retry for probes. Validate the final URL against the selected target after combining base path/API path. Reject URL credentials, fragments, unexpected query credentials, metadata destinations, and unauthorized host/address changes.

Private/self-hosted endpoints require an explicit deployment-approved host/network policy. Do not relax global MCP settings to make onboarding connect. Retain meaningful DNS/address checks for the actual request, including IPv6 and rebinding cases; an allowlisted label alone is not sufficient outbound validation.

Do not pass source text through `.format()` or an executable template. Validate the returned bounded JSON with the actual local schema. Refusal, missing/truncated output, wrong shape, extra fields, and provider errors are explicit failures; never repair them with another provider.

Record actual returned usage when available. Missing usage is unknown, not zero. Do not create customer budget rules, runtime billing receipts, or invented cost estimates for a connection test.

## 9. Worker-based synthetic provider test

Add internal job type **`ONBOARDING_TEST_PROVIDER`** to the onboarding worker. Its binding is probe → job → frozen configuration, not session → input manifest. Do not create a fake session or block source extraction while testing a provider.

The test runs through the same worker-side resolution/adapter path that later analysis will use. An API-container-only test cannot establish that the worker has the credential or same target settings.

### Test flow

1. An authorized administrator explicitly clicks **Test AI connection** on an enabled saved configuration.
2. Require expected configuration version/target fingerprint and an idempotency key. Commit the probe, job, and receipt together. Concurrent clicks for the same configuration reuse one active probe; a new key after completion requests a deliberate retest.
3. The worker validates its exact probe/job association and effective target fingerprint before resolving a secret. Unknown/disabled/missing/mismatched configuration fails without provider fallback.
4. Send only a fixed synthetic instruction and a fresh nonce. For example, require exactly `{"ok": true, "nonce": "<this probe's nonce>"}` with a matching local schema and no additional fields.
5. In JSON object mode, explicitly instruct JSON output. In schema mode, send the supported schema contract. Validate schema, boolean type, nonce, completeness, and refusal handling locally in both cases.
6. Publish outcome and safe metrics under lease ownership and snapshot checks. Only the latest matching probe can contribute to current AI-setup status.

Use a small bounded output allowance (default 256 tokens), a 30-second overall request deadline, and a 64 KiB response limit. Define the target's supported output-token parameter explicitly; do not silently switch model/request dialects. No uploaded file, extracted block, source filename, or organization prompt enters the test request.

### Ambiguous remote outcomes

Provider calls can incur work even when the response is lost. Persist `dispatch_started_at` under current lease ownership before sending. Set probe jobs to one attempt and transport retries to zero.

The dispatch-marker transaction must lock/recheck the current configuration as well as the job: still enabled, matching configuration version and effective target fingerprint, valid probe/job binding, current unexpired lease, and no cancellation/supersession. Treat marker commit as the dispatch authorization point. Later configuration changes invalidate current-status publication and fence stale results, but cannot guarantee stopping a request already authorized/dispatched.

On timeout/crash after the dispatch marker, report `OUTCOME_UNKNOWN`; do not automatically resend. The marker means dispatch might have happened, not that the provider definitely received it. On a failure before dispatch, record a safe pre-dispatch failure. The reaper must terminalize expired probe jobs and close their probe records so they cannot remain stuck.

The user can explicitly retest with a new key. Cancellation/supersession must fence late results. Reject the reserved internal job type through the generic jobs API and verify the persisted probe/job association in the worker.

Test success means the configured target/model/credential returned the required synthetic response at the recorded time. It does not establish document-analysis quality, compliance, runtime control verification, or future service availability.

## 10. API and permission additions

Use the Step 02 router and versioned mutation conventions. Continue metadata-only access with `sdk:read`; extracted content, blocks, source options, citations, and sensitive warnings inherit `onboarding:sources:read` plus the relevant read/manage permission.

Add **`onboarding:providers:manage`**, seeded initially for `organization_admin`, for configuration changes and live test requests. Require it alongside `sdk:manage`. Do not give every source reader/provider tester access to arbitrary deployment credentials. Existing installations must receive the grant through the normal seed/upgrade mechanism.

Proposed endpoints under `/v1/productization/onboarding`:

| Method/path | Behavior |
| --- | --- |
| `GET /capabilities` | Supported intake/extraction formats, parser-profile fingerprint, bounded parsing options, and sanitized feature availability; no secrets |
| `PATCH /sessions/{session_id}/sources/{source_id}/parsing-options` | Validate options, check expected row version, advance input/options versions, audit the change |
| `POST /sessions/{session_id}/extract` | Validate current inputs and preparation parent; enqueue/reuse exact extraction plan |
| `GET /sessions/{session_id}/extractions` | Paginated extraction/outcome summaries, permission-aware |
| `GET /sessions/{session_id}/extractions/{extraction_id}/blocks` | Authorized paginated blocks and native locators |
| `GET /sessions/{session_id}/citations/{block_id}` | Authorized citation resolution with validated optional start/end code-point offsets |
| `GET /ai-configuration` | Sanitized current status; detailed editable fields only to provider managers |
| `PUT /ai-configuration` | Explicit version-checked save; no external call |
| `GET /provider-targets` | Approved sanitized choices for provider managers |
| `POST /ai-configuration/test` | Enqueue/reuse the synthetic probe; return `202` for active work |
| `GET /ai-configuration/tests` and `/ai-configuration/tests/{probe_id}` | Sanitized test history/detail with authorization |

Reuse existing run polling/cancel/retry for extraction. Add a permission-checked probe-cancel operation if cancellation is exposed in the provider UI; do not send a provider probe through a session-run endpoint.

Extraction requests include `expected_row_version`, `requested_input_revision`, `preparation_run_id`, and the expected parser-profile fingerprint. Keep the Step 02 idempotent-replay precedence: an identical committed key returns the original resource identity; a new request checks current versions under the session lock.

Resolve an active/current matching extraction run by exact plan fingerprint. A changed parser/options plan creates a new run, even when original bytes match; it is not a retry of a historical incompatible plan. Keep superseded retry requests from replacing newer results.

Configuration/test requests use their own `expected_configuration_version` and target fingerprint. They do not advance source input or session row versions. Changes to provider setup must not invalidate already extracted content.

Reject mismatched nested IDs and expose no source contents, storage keys, credentials, or raw upstream bodies through generic job results/errors or lower-permission endpoints. Audit content access, configuration changes, extraction requests, and probes with safe identifiers and reason codes.

## 11. UX screens and interaction details

Extend the Step 02 intake workspace using the existing design system. Add no fake working feature or generated-item count.

### Screen A — Extracted-source overview

After sources are prepared, show **Extract text and tables**. Display each included source's actual state: Text extracted, Partially extracted, Needs OCR, Needs parsing options, Unsupported, or Failed.

Show real counts and coverage, such as extracted pages versus total pages or worksheets processed, without interpreting them as policy coverage. Separate worker activity from source quality. A completed worker with partial results shows **Needs attention**.

Allow source replacement/exclusion through Step 02. Changing parsing options should explain that the originals are retained and a new preparation/extraction is needed. Do not silently rerun with guessed options.

### Screen B — Source preview and citations

Use a source/part navigator and a paginated preview: PDF pages, DOCX structural sections/tables, worksheet tabs with cell coordinates, or text lines. Display original revision, extraction version, and warnings in a compact details area.

Clicking a citation opens the exact stored block/range and its native locator. Mark historical/excluded input clearly. Workbook cells display formula and cached value distinctly when applicable. Do not pretend to render a faithful original PDF/Word page when only text extraction is implemented.

Support Arabic and mixed direction text without reversing source strings. Keep filenames/locators isolated from adjacent text. Source contents must not appear for metadata-only readers.

### Screen C — Parsing issues

Offer concrete actions matched to the reason: choose encoding/delimiter/header mode, provide a searchable PDF, replace a damaged/encrypted file, or exclude an irrelevant source. Keep the original available through authorized download.

Do not offer a working OCR button, automatic AI repair, or password submission unless that feature is actually implemented. No silent content truncation or skip-and-success behavior.

### Screen D — AI setup

Provider managers choose an approved target, explicit model, allowed structured-output mode, and bounded settings. Show the destination label and credential-configured status, never the key. Missing deployment targets/secrets produce an actionable configuration state.

Use separate **Save configuration** and **Test AI connection** actions. Explain beside Test that it sends a short synthetic request and may use provider tokens; uploaded documents are not sent. Do not test on page load or save.

Show last test time, exact configuration version, selected model/target, outcome, latency, and actual usage when returned. A stale test is not a green current badge. An unknown remote outcome needs an explicit retest; it is not silently retried.

Parsing remains available when AI is disabled, unconfigured, or unavailable. Future Generate proposals becomes a real action in Step 04; it is not enabled in this step.

## 12. Ordered implementation tasks

1. **Confirm prerequisites and contracts.** Read Steps 01–02, current onboarding services, queue fencing, mutation receipts, source permissions, and app/router registration. Identify integration gaps without overwriting existing work.
2. **Add schema and stage-aware projections.** Add extraction/block/run-source/config/probe models, source options, manifest versioning, parent references, and relevant indexes/constraints. Preserve historical runs. Register models in Alembic.
3. **Implement pure parsers and isolation.** Add dependencies/lockfile, typed results/locators, process supervision, resource limits, and synthetic format fixtures.
4. **Implement extraction orchestration.** Extend the existing worker, freeze plans, validate originals, reuse exact results, publish with fences, account for all sources, and preserve correct terminal/cancel/retry behavior.
5. **Implement citation/content APIs.** Enforce permissions, membership, pagination, bounds, historical labels, and code-point ranges.
6. **Implement explicit AI configuration.** Add approved target settings, onboarding-only secret aliases, configuration permissions, immutable snapshots, and the bounded adapter. Do not alter legacy provider defaults.
7. **Implement synthetic probes.** Use the worker, exact bindings, idempotency, dispatch markers, latest-probe ordering, safe outcomes, and no automatic remote retry.
8. **Build the minimal UI.** Wire extraction overview, preview/issues, and provider setup to real APIs. Keep loading/error/read-only/stale states complete.
9. **Wire deployment and validate.** API/worker share parser versions, policies, and required configuration. Parser children do not receive provider secrets. Update worker/deployment/env docs, test collection, and focused regression coverage.

Suggested new modules within the Step 02 package: `parsers/`, `extraction.py`, `citations.py`, `ai_configuration.py`, `ai_adapter.py`, and `provider_probe.py`. These are proposed names; use equivalent existing structure where appropriate.

## 13. Required regression coverage

### Parser fixtures and provenance

- PDF text/page citation; scanned PDF; mixed pages; genuinely blank PDF; encrypted/malformed document; page/stream resource limits.
- DOCX paragraph–table–paragraph order, nested/merged cells, header/footer parts, tracked-change and unsupported-part warnings.
- XLSX multiple/hidden sheets, hidden rows/columns, merged cells, numeric-looking string IDs, formulas with present/missing caches, misleading dimensions, and bounded scanning.
- CSV quoted newlines, escaped delimiters, duplicate headers, preserved first row, ambiguous delimiter, UTF-8 BOM, Windows-1256 override, and invalid encoding.
- Text/Markdown/prompt with braces, HTML/link syntax, Arabic diacritics, mixed English/Arabic, emoji, and combining characters.
- Citation excerpt equals the exact block slice; offset tests distinguish Unicode code points from UTF-16; invalid/cross-session references are rejected.
- ZIP/XML expansion, parser timeout/memory/output limit, and cancellation terminate/reap the child while the parent remains usable.

Use small synthetic fixtures with documented expected output. Do not depend on customer documents or external providers for ordinary CI.

### Worker, versioning, and authorization

- Stage-aware current-run selection retains the correct preparation parent after extraction runs exist.
- Every manifest source has an explicit outcome; job success with partial source coverage cannot establish readiness.
- Cancelled, aborted, or aggregate-limit-stopped runs finalize unattempted source rows as `NOT_PROCESSED`; no pending rows remain under a terminal job.
- Same extraction identity produces one published result; parser/dependency/options/limit changes cannot reuse an incompatible cache.
- Replacing a source or editing options invalidates current extraction, preserves history, and requires the matching new preparation.
- A cancelled/stale lease owner cannot publish or update readiness; partial cache artifacts do not make a cancelled run current.
- Transient failures can retry; permanent/resource outcomes do not create infinite retries.
- Independent PostgreSQL sessions prove committed persistence and uniqueness. Test migrations against their actual immediate predecessor.
- Source/metadata permission separation applies to blocks, citations, warnings, generic jobs, archived sources, and all new routes.
- No extraction path creates registry, Policy Library, rule, budget, approval, credential, or runtime-evidence records.

### Provider configuration and tests

- Missing/disabled/unsupported config causes no network request and no fallback.
- Save config performs no provider call and exposes no raw secret/reference leakage.
- Target/credential-purpose mismatch, arbitrary secret reference, unapproved endpoint, redirect, metadata IP/host, IPv6/private-address policy, and DNS-change cases fail safely.
- API/worker target-policy mismatch fails before credential resolution/network.
- Synthetic request contains no uploaded source, extracted block, or organization prompt.
- JSON object/schema modes are explicit; wrong nonce/type/schema, truncation, refusal, and invalid JSON are failures without repair/fallback.
- Concurrent Test clicks reuse one active probe; deliberate retest is ordered; an older delayed pass cannot supersede a newer failure/unknown result.
- Dispatch-marker timeout/crash is `OUTCOME_UNKNOWN` without automatic resend; expired probe jobs terminalize.
- Configuration/credential-policy revisions invalidate old test matching without invalidating extraction.
- Disabling/changing configuration before dispatch-marker authorization prevents the old test from sending; later changes prevent stale current-status publication.
- Mock transport tests are reported as mocked; a live smoke result is claimed only if actually executed against an explicitly configured target.

## 14. Validation commands and rollout checks

Follow the current lockfiles, `DEVELOPMENT.md`, and repository CI requirements. Example focused commands after adding the proposed tests:

```bash
uv run pytest tests/onboarding -q
uv run pytest tests/registry/test_catalog_import.py tests/policy/test_unified_rules_and_library.py -q
uv run pytest tests/provider_gateway/test_upstream_security.py tests/provider_gateway/test_workload_credential_isolation.py -q
uv run pytest tests/db/test_migrations.py -q
pnpm --dir control-plane-ui typecheck
pnpm --dir control-plane-ui test:unit tests/unit/onboarding-extraction.test.tsx tests/unit/onboarding-ai-configuration.test.tsx
```

Frontend test filenames above are proposed additions; use the actual files created. Ensure new tests are included in normal Makefile/CI collection. Run applicable changed-file lint/format and build checks.

Verify a controlled end-to-end walkthrough with the real worker: upload synthetic policy/inventory sources, prepare, extract, inspect a source citation, change an option/revision, and confirm old results remain historical. Prove extraction works without any AI credential.

A live provider probe requires an explicitly configured approved target and credential. Ordinary CI uses mocked transport. If live testing or a database/browser check is unavailable, report the exact blocker; do not fabricate success or use another provider as a substitute.

Update deployment documentation for parser dependencies/process limits, identical API/worker fingerprints, approved target settings, credential-version rotation, and synthetic-test usage. Do not log or save keys while demonstrating configuration.

## 15. Definition of done and Claude report

- [ ] Supported saved formats produce bounded, immutable extracted blocks with real locators.
- [ ] Scanned, partial, empty, malformed, encrypted, and ambiguous inputs have truthful outcomes.
- [ ] Arabic/mixed-language text and code-point citations behave correctly.
- [ ] Source/options/parser revisions and stage-aware parent relationships prevent stale readiness.
- [ ] Parser children are bounded and cancelled workers cannot publish.
- [ ] Extracted content inherits source authorization and stays out of generic job/log projections.
- [ ] AI configuration is explicit, independent of runtime settings, and contains no raw secrets.
- [ ] Worker-side synthetic probes use exact config snapshots and handle unknown outcomes without automatic replay.
- [ ] The UI presents actual extraction and test states without generated-domain or production-verification claims.
- [ ] Migrations, dependencies, deployment wiring, and focused tests are complete or accurately blocked.

Return a completion report with: user-visible behavior; files changed; migration/dependency versions; parser support and known coverage limits; API/permission changes; actual test commands/results; whether provider validation was mocked or live; any Step 01–02 prerequisite gap; and a short walkthrough of a citation resolving to its exact source revision.

Stop after Step 03. **Step 04 will use these extracted blocks and the explicitly configured AI adapter to generate and reconcile reviewable inventory proposals.** Follow the established repository review workflow; this work order does not request production deployment or publication.

## 16. Source references

### Reviewed repository revision

- [Structured Catalog importer](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/registry/catalog_import.py)
- [Policy Library service](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/policy/library.py)
- [Dependencies](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/pyproject.toml)
- [Legacy LLM execution and fallback](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/llm_controller.py)
- [Legacy provider factory/adapters](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/llm_providers.py)
- [Legacy provider settings store](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/services/provider_settings_store.py)
- [Low-level secret resolver](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/provider_gateway/credentials/secret_resolver.py)
- [Runtime-context credential fallback](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/provider_gateway/credentials/isolated_resolver.py)
- [Credential materialization](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/provider_gateway/credentials/materialize.py)
- [HTTP transport](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/provider_gateway/transport.py)
- [Existing upstream validation](https://github.com/ztechnium/raicentral/blob/0c052350db88c834e79c752284e264a68d7c6210/src/rai_gateway/provider_gateway/production/upstream_guard.py)

Primary parser/protocol references are linked beside the requirements they support. They inform library behavior; the work order's resource limits, workflow, models, permissions, and release boundaries are proposed product/engineering decisions.
