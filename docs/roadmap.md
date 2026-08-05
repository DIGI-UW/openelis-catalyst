# Catalyst Roadmap

**Status:** Active pathway roadmap; Superset-backed Dashboard Builder MVP selected next
**Product specification:** [`specification.md`](specification.md)  
**Hub contract:** [`med-agent-hub.md`](med-agent-hub.md)

## Roadmap policy

This roadmap replaces the local use of OGC-70 milestone labels M0.0–M5. Those
labels remain historical references in the parent OpenELIS specification. The
accepted query workbench is the shared foundation; later work is organized as
parallel product, data, assistance, evaluation, narrative, and production
pathways rather than one mandatory sequence.

Delivery follows vertical, test-driven slices:

1. Write contract or behavior tests that fail.
2. Implement the smallest complete path that passes.
3. Exercise the real service boundary.
4. Record evidence and remaining limitations before starting the next slice.

Milestones have exit criteria rather than calendar estimates. A milestone is
not complete because its classes, endpoints, or compose services exist.

The selected milestone is the next product investment, not a claim that every
other pathway is blocked or abandoned. A pathway may advance independently
when its own gate is approved. Production security remains future R5 work.

## Current baseline

The repository currently contains:

- a Gateway with governed-preview compatibility APIs and an append-only
  iterative workbench API;
- a runtime PostgreSQL catalog shared by schema guidance, SQL completion,
  Gateway model context and deterministic validation;
- one canonical SQL editor, manual query versions, validation, explicit
  execution, typed results, compact turn history and contextual follow-ups;
- Gateway-owned selectable profiles, prompts, writer/reviewer orchestration,
  deterministic lint/re-lint, and exact model/configuration provenance;
- med-agent-hub as a generic single-role provider/router boundary through
  `POST /v1/hub/generate`;
- a pinned OpenELIS → HAPI FHIR → FHIR Data Pipes → PostgreSQL demo assembly;
- a second, independently switchable data source (OpenMRS HIV/ART, its own
  analytics database and catalog) alongside OpenELIS, targetable per turn
  within one source-agnostic session;
- Gateway, analytics/assembly, UI and browser tests.

It does not yet contain:

- persisted/versioned Dataset, Widget, and Dashboard draft libraries;
- deterministic Superset bundle generation or a pinned local Superset service;
- a table-to-report evidence adapter;
- production authentication/authorization and deployment hardening;
- complete comparative Harness experiments and report scoring.

The original Catalyst-local agent code remains compatibility scaffolding, not
the target topology.

## Development pathways and selected milestone

| Pathway | Current state | Dependency | Next boundary |
| --- | --- | --- | --- |
| Query foundation (R0–R3.1) | Complete and accepted | None | Maintain as the shared product base |
| **Superset-backed Dashboard Builder (D1)** | **Selected next milestone** | Accepted query/workbench foundation only | Implement iterative Dataset/Widget/Dashboard drafts and publish deterministic native bundles to pinned local Superset |
| Data foundation (G2.10) | Implementation candidate; live evidence incomplete | Query foundation | Complete the two-source/lossless contract and acceptance matrix |
| Query assistance (W2) | Planned, not selected | Query foundation plus a new user scope gate | Prove bounded AST-unit repairs with explicit acceptance |
| Evaluation (W3/CVR) | Notebook runner/report parity implemented; broader export/experiments remain | Query foundation; individual experiments may add their own gates | Finish PR #43 release acceptance separately, then expand session export/comparisons as chosen |
| Narrative reporting (R4) | Not started | A governed table; Dashboard MVP is not required | Add evidence-linked summaries with honest grounding states |
| Productionization (R5) | Future | Explicit security/deployment program | Authentication, authorization, data scope, audit, operations |

Dashboard MVP does not wait for G2.10, W2, W3, R4, or R5. Conversely, selecting
D1 does not silently approve those pathways. PR #43 and its final MS-D decision
are validation-report release closeout, not a Dashboard MVP implementation
gate.

## R0 — Local specification baseline

**Status:** Complete

**Goal:** Establish one local product definition and eliminate contradictory
architecture documentation.

### Deliverables

- Canonical product specification.
- Canonical med-agent-hub integration contract.
- Normative local question, engine request, legacy completion, query, preview,
  execute-request, execution-outcome, policy-outcome, and table JSON Schemas.
- This roadmap.
- Concise repository and component documentation linked to the canonical docs.
- Explicit OGC-70 compatibility mapping.
- Explicit current-versus-target implementation status.
- Retired local M0.2 model-scorecard documentation.

### Exit criteria

- Local documentation consistently assigns governed-query profiles, prompts,
  role composition, and orchestration to Gateway and generic role execution to
  Hub.
- Planned profiles are clearly distinguished from profiles available today.
- OGC-70 functionality is classified as retained, reassigned, superseded, or
  deferred.
- The Clinical AI Validation Harness is documented as the umbrella assembly and
  experiment boundary.
- Standalone scorecard documents remain retired; Gateway profile changes are
  evaluated through local golden engineering fixtures and cross-model
  experiments in the harness.

## R1 — Generic med-agent-hub execution foundation

**Status:** Complete for the local governed-query demo

**Goal:** Keep Catalyst query semantics and orchestration in Gateway while
using Hub as the shared provider/router and structured-output transport
boundary.

### Test-first slices

1. Gateway query profiles
   - Test Gemma and bundled Qwen writer-only, same-model checked, full-weight
     writer, and cross-family reviewed profile discovery with exact
     prompt/model/configuration evidence.
   - Serve them through `GET /v1/catalyst/query-options`.
2. Generic role call
   - Test request mapping, structured-output pass-through, timeout,
     cancellation, malformed content, and backend failures.
   - Implement one `POST /v1/hub/generate` call per writer/reviewer invocation.
3. Gateway orchestration
   - Test writer → deterministic lint/correction → optional reviewer →
     deterministic re-lint and final evidence.
   - Keep catalog semantics, output contracts, and workflow out of Hub.
4. Configuration and readiness
   - Prove Catalyst requires no model-provider credentials.
   - Report Hub health and derive live profile availability from Hub's
     versioned, credential-free router catalog.

### Ownership rule

Catalyst owns the governed-query profile ID and its roles, prompts, stages,
sampling settings, response formats, and validation policy. Hub owns
provider/router connectivity and executes only the single role call requested
by Catalyst.

### Exit criteria

- Catalyst health distinguishes process health, Hub transport health,
  router-inventory reachability, and whether any configured profile has every
  required role model.
- Hub is the only provider/router integration service on the target path;
  Gateway is the only Catalyst query orchestrator.
- Existing Gateway callers receive stable errors when Hub, a Gateway profile,
  or a requested model is unavailable.
- Catalyst uses the configured hub; the demo stack verifies that the hub uses
  the local model router.
- Unit, contract and live generic-Hub smoke tests pass.

## R2 — Analytics data and query contract

**Status:** Complete for the seeded OpenELIS demo

**Goal:** Give the Gateway query engine a versioned analytics vocabulary over
OHS FHIR Data Pipes that matches the relations readable by the configured
database role.

### Test-first slices

1. Prove OpenELIS FHIR data can be transformed into the selected analytics
   engine with deterministic seeded fixtures.
2. Define the first semantic-layer view over Data Pipes projections and assert
   its grain, types, terminology and expected rows.
3. Add views one at a time for result facts, volumes, positivity, turnaround
   time and pending work.
4. Publish semantic metadata and combine it with runtime discovery of every
   role-readable relation; validate examples against the live database.
5. Verify incremental refresh and expose source freshness.

### Design gates

- Select and document the first warehouse engine and SQL dialect.
- Separate FHIR Data Pipes per-resource projections from downstream
  cross-resource semantic marts.
- Confirm that MVP fixtures and views contain demo data only.
- Define mandatory date and result-size constraints.
- Include raw FHIR structures or base `clinlims` tables only when the configured
  database role can select them; database grants remain the query boundary.

### Exit criteria

- Every governed semantic view has versioned metadata and seeded expected
  results, and the runtime catalog reports every relation the role can select.
- The catalog is compact enough for the configured model context budget.
- Freshness includes source watermark, pipeline run, completion state and lag.
- Read-only execution credentials cannot access schemas that have not been
  granted to that role.

## R3 — Query-to-table MVP

**Status:** MVP implementation and final live multi-model acceptance complete

**Goal:** Deliver the first useful product slice:
natural-language question → governed query → table.

### External dependency

med-agent-hub must provide the generic `POST /v1/hub/generate` executor and
reach a local router serving the exact models required by the selected
Gateway profile. Gateway owns query planning, generation, deterministic
lint/correction, optional review, and `catalyst.query.v1` finalization.
Contextual follow-up through the reviewed path additionally requires a Gateway
profile with a reviewer role. Hub clinical-answer profiles do not satisfy this
query dependency.

### Test-first slices

1. Query contract
   - Add failing fixtures for valid, ambiguous, unsupported, unsafe and
     out-of-scope questions.
   - Validate the normative Catalyst question, engine request, role output, and
     finalized response JSON Schemas independently of prose.
   - Add API contract tests for ready preview, clarification, unsupported,
     model rejection, Catalyst policy rejection, and Hub-failure responses.
2. Deterministic policy
   - Add failing tests for DDL/DML, multi-statement SQL, relations outside the
     runtime role-readable catalog, dialect mismatch and excessive limits.
   - Implement AST-based validation for the selected dialect as defense in
     depth.
3. Execution
   - Add failing integration tests against seeded analytics data.
   - Implement read-only demo execution with timeout, row and resource limits.
4. Table response
   - Add failing contract tests for typed columns, units, empty results,
     truncation, freshness, warnings, provenance, and non-success execution
     outcomes.
   - Implement and validate the normative `catalyst.table.v1` and execution
     outcome responses.
5. User review
   - Add an end-to-end test for question → query preview → acceptance → table.
   - Validate the normative preview and execute-request schemas.
   - Bind preview ID/digest to the exact displayed question, target, SQL,
     parameters, expected columns and one-time execution. The target
     includes the catalog version.
   - Ensure altered or consumed previews cannot execute. Saved previews do not
     expire.
   - Test execute and polling status codes, same-key replay, active polling,
     unknown IDs, conflict, and failure outcomes.
6. Demo-mode boundary
   - Add full-stack smoke assertions that Hub `LLM_BASE_URL` resolves to the
     local model router and the analytics source uses seeded demo data.
   - Catalyst verifies Hub health and Gateway-profile presence; Hub and Compose
     own model-router connection configuration.
   - Label all UI and API responses as demo behavior without production
     security claims.

### MVP exit criteria

- Gateway profile discovery succeeds with exact writer/reviewer evidence, and
  Hub generic generation is reachable.
- Every role output validates against its configured structured-output schema,
  and Gateway's finalized `catalyst.query.v1` validates against the normative
  local JSON Schema.
- Golden questions return the expected seeded results, not merely plausible
  SQL.
- Disallowed and out-of-scope queries are rejected deterministically.
- Preview acceptance is non-expiring, one-time and revalidated at execution.
- Empty, partial, truncated and failed results are distinguishable.
- Table output includes freshness, schema, query, profile and trace provenance.
- The deployed path uses demo data and local LLMs only.
- No result rows are sent to an external model provider.
- The full path runs through the deployed Catalyst and hub services.

The umbrella Clinical AI Validation Harness owns the sibling Catalyst/Hub pins
and real-path experiment runner. The 2026-08-04 user checkpoint accepted the
final-pin 12/12 real-model matrix, PostgreSQL/gold comparisons, bounded failure
and recovery, keyboard-only path, and actual 200% browser zoom. Local evidence
is generated by `mvp-health.sh`, `test_mvp_live.sh`,
`test_data_pipes_incremental.sh`, and the notebook Playwright project. The
Playwright path now also enforces unobscured keyboard focus and the accepted
200%-equivalent reflow boundary.

## R3.1 — Iterative query notebook

**Status:** Implemented and accepted for the manual MVP

The linear notebook extends R3 without adding chat or branching:

1. Record initial and follow-up turns in the append-only event ledger.
2. Keep one active SQL editor; save contract-valid dirty buffers as human
   versions and retain unresolved buffers only as snapshots.
3. Validate and Run the exact active version, preserving version-labelled stale
   results.
4. Generate one complete successor from the exact editor snapshot and current
   instruction, using at most five prior follow-up instructions.
5. When the selected profile declares a reviewer, invoke it after the writer
   and deterministic lint, re-lint its complete correction, and preserve
   writer/reviewer evidence. The recommended GPU lane uses a different-family
   Qwen reviewer.
6. Restore sessions, versions, executions and compact history without model
   calls.

Final-pin acceptance on 2026-08-04 covered the complete initial → manual edit →
Validate/Run → contextual follow-up → successor → rerun → refresh flow. The
12/12 real-model matrix, independent PostgreSQL/gold comparisons, bounded
failure/recovery, keyboard-only traversal, and actual 200% browser zoom passed.
Model candidates and observed temperature-zero digest variance remain recorded
evidence rather than reproducibility claims.

## D1 — Superset-backed dashboard builder MVP

**Status:** Selected next milestone; D1a technical validation passed 2026-08-05;
explicit user acceptance pending before product code

**Goal:** Implement the supplied iterative Ask → Dataset → Widget → Dashboard
design while keeping Superset as the renderer. Catalyst persists supervised
draft lineage and publishes deterministic native bundles into a shared local
outbox beneath Catalyst-owned, root-gitignored `/runtime/superset/` for clean
bootstrap or explicit running-instance import; publication must leave the target
worktree clean.

**Ask integration boundary:** D1 implements the prototype's Ask shell, fixed
composer, chronological thread, Dataset tile, and review panel while integrating
the accepted query notebook inside them. Profile/model selection, the single
full SQL editor, completion/Format, manual versions, advisory
Validate, explicit Run, visible generation/findings/database/result evidence,
contextual follow-up, timeline, staleness, refresh, and one New session action
remain available through **Save Dataset**. `Available data` becomes the compact,
keyboard-accessible entry to every runtime relation/column and existing source-
browser filter/page/failure/zero-match state. Only a successful Run produces a
Dataset tile; its panel is the sole full bounded typed-result/warning/empty/
truncation/provenance surface, so no duplicate inline result table is introduced.
No example prompts, second editor, or automatic execution are introduced.

### Checkpoints and task slices

D1a was reopened because the earlier four-slice plan did not yet encode the
pinned importer, bounded-result, single-source/layout, accepted Ask, or live
deployment contracts precisely enough. The T158+ decomposition preserves the
older gate IDs, so the order below—not numeric sorting—is normative.

The remediated checkpoint has zero unresolved CRITICAL/HIGH findings. Fresh
remote comparison proves both branches are zero commits behind their current
`origin/main`; all eight Dashboard Builder contract mirrors are byte-identical;
the seven JSON Schemas and positive/negative fixtures validate; and T137, T158,
and T159 are complete. D1a now waits only for the explicit user decision in T138.

| Checkpoint | Entry | Testable exit evidence | Pause rule |
| --- | --- | --- | --- |
| **D1a — grounded contracts** | Both feature branches based on current `main`; accepted query-workbench baseline | T137 → T158 → T159 → T138; reconciled API/bundle/pointer/receipt/per-Dashboard-last-verified/event/acceptance schemas; bounded-result and exact `dataSourceId` + `catalogVersion` rules; stable logical-ID slug; scoped failure/recovery; preimplementation PCCP; byte-identical contract copies; valid JSON Schemas; zero unresolved CRITICAL/HIGH findings | After T138, pause for explicit user acceptance; no product code or D1b work starts before it |
| **D1b — Superset runtime/import** | User accepts D1a | T139–T144 and T160–T165; Catalyst-owned `/runtime/superset/` gitignore boundary and clean-target guard; pinned Superset 6.1.0/driver; clean fixture import for all five visualization families; standalone Python-3.10 importer/state scripts with no Catalyst-package import, Gateway-CI discovery, `rfc8785` parity, and pinned-container smoke; persistence/read-only access; restart/no-op/lock/credential matrix; scoped failure handling; validated per-Dashboard last-verified projection and full Superset-local metadata DB/home-volume reset/reimport recovery with no asset-selective delete, ORM/REST mutation, or automatic retry | Stop on schema, image/driver, transaction, permission, projection, canonical-JSON, clean-target, or verification drift before builder implementation |
| **D1c — builder backend/export** | D1b passed | T145–T149 and T166–T173; red-first immutable storage/routes, lossless execution adapter/compiler, visualization, serializer, and publication tests; exact source+catalog locking; deterministic full-width layout; root-wrapped byte-identical ZIP; stable Superset UUID and `catalyst-<lowercase-dashboard-id>` slug; exact child reuse/change behavior; real round trip; zero post-execution model/DB calls | Stop on API, manifest, identity, or source-lineage drift before UI integration |
| **D1d — integrated product UX** | D1c passed | T150–T154 and T174–T179; accepted Ask characterization remains green; one active SQL editor; Available data and sole full Dataset result panel; Dataset/Widget/Dashboard libraries and publication controls; scoped recovery; desktop/390×844/320-CSS-px/actual-200%-zoom and keyboard matrix | Pause for user UX acceptance before the definitive live run |
| **D1e — deployed MVP acceptance** | User accepts D1d | T180–T182 → T155–T157; validate structured `query_turn`/`query_version`/`query_execution` plus builder event emission and fixed six-step `orderedWorkflow` before the real writer/reviewer run; initial Run → Save Dataset v1 while current → contextual follow-up → rerun → Save Dataset v2 while successor current; heterogeneous Widgets; imported stable slug URL; PostgreSQL reconciliation; repetition/failure/recovery; schema-valid `run_manifest.json`, `events.jsonl`, and `acceptance.json`; green CI and final user acceptance | D1 remains open until the deployed dashboard and evidence are inspected and accepted |

### Exit criteria

- A user can promote a successful execution through saved Dataset, Widget and
  multi-widget Dashboard drafts whose Widgets share one exact `dataSourceId`
  plus `catalogVersion` and occupy deterministic full-width rows, publish/
  download a native bundle, and restore immutable history after refresh without
  model calls or query re-execution.
- Identical inputs produce byte-identical ZIPs and every asset traces through
  deterministic logical/version UUIDs to query, execution, source/catalog,
  typed parameters/schema, a canonical bounded-result digest, and actor kind.
  Every ZIP is rooted at `catalyst_dashboard_<dashboard UUID>/`; its
  asset-content digest covers the ordered native YAML members and excludes the
  manifest and ZIP metadata. The logical Catalyst Dashboard ID deterministically
  yields the Superset UUID and `catalyst-<lowercase-dashboard-id>` slug.
- Clean boot imports the selected bundle; one explicit command updates a running
  Superset instance. Catalyst persists exactly `Draft`, `Bundle ready`,
  `Imported`, or `Import failed`; only the importer records the last two, and
  `Importing` remains transient process/log state. Preflight and transactionally
  rolled-back CLI failures preserve the last verified Dashboard; a failed
  post-import verification disables Open/current-success. Recovery first
  validates the per-Dashboard last-verified projection, then fully resets only
  the Superset-local metadata database/home volumes and reimports that bundle;
  missing/corrupt projection data stops before reset, and asset-selective delete,
  direct ORM/REST mutation, automatic rollback, and automatic retry are
  prohibited. Recovering verified A leaves desired B selected in `current.json`
  and `import_failed`, with bootstrap/retry suppressed until explicit retry or a
  new publication.
- Acceptance emits schema-valid `acceptance.json` plus versioned
  `run_manifest.json` and `events.jsonl` containing structured `query_turn`,
  `query_version`, `query_execution`, builder/publication/import/reconciliation/
  accessibility/recovery/acceptance events, and the fixed six-step
  `orderedWorkflow`.
- Superset renders values independently reconciled to PostgreSQL, and changed
  source queries produce explicit stale state without rebinding saved drafts.
- Superset REST API publication, embedded viewing, cross-system undo/
  reconciliation, model visualization calls, narratives, sharing, scheduling,
  automatic refresh, authorization and production deployment remain deferred.

## Narrative reporting pathway (R4) — Evidence-linked summaries

**Status:** Not started

**Goal:** Turn an accepted table into an evidence-linked narrative without
changing the query or execution boundary.

This remains a local demo using demo data and local LLMs.

### Profile setup

- Default: `single-e4b-checked`.
- Optional deeper report: `team-med-checked`.
- Low-level experiment legs are not product APIs.

### Test-first slices

1. Define evidence records for table rows, aggregates, filters, units and source
   freshness.
2. Record whether every emitted citation resolves to bounded inline evidence or
   a shipped, versioned Catalyst analytics `ContextSource`.
3. Relay provisional `answer_done`, optional provisional
   `answer_validation`, `indepth_pending`, `indepth_done` or `indepth_error`,
   and terminal `done`.
4. Test oversized tables, mandatory evidence overflow, cancellation and
   rejected in-depth output.
5. Present report stages without hiding the table or rewriting hub verdicts.

### Exit criteria

- Only governed query results enter report context.
- Result evidence remains in the local demo model path.
- Validation and grounding states are displayed honestly; `unchecked` output
  is not presented as validated.
- Hub trace and Catalyst trace IDs are correlated.
- Report failure never invalidates or hides a successful table.

## Productionization pathway (R5) — Security and supported deployment

**Status:** Future

**Goal:** Convert the local demo into a security-reviewed OpenELIS deployment.

### Deliverables

- Authentication and OpenELIS RBAC.
- Tenant, facility and row-level scope independent of generated SQL.
- PHI classification and provider-routing policy.
- Service authentication, trusted endpoints and network egress policy.
- Warehouse encryption, access, residency, retention and deletion controls.
- Durable audit and protected trace storage.
- Threat modeling, secrets lifecycle and key rotation.
- Capacity-tested rate, row, statement and concurrency policy.
- Operational readiness and degraded-state runbooks.
- OpenELIS UI integration or an explicitly supported sidecar UI.
- Export behavior if approved for the first supported release.

### Exit criteria

- Security tests cover privilege escalation, scope removal, prompt injection,
  unsafe SQL, PHI disclosure and evidence exfiltration.
- Operators can identify data, hub, model-router and execution failures
  separately.
- Upgrade and rollback preserve profile and contract compatibility.

## Clinical AI Validation Harness transition

**Status:** Umbrella pins, real-path runner, judging, and mixed-family report
publication implemented. PR #43's final MS-D acceptance remains release
closeout; broader session export and comparative experiments remain optional
evaluation-path work.

The
[Clinical AI Validation Harness](https://github.com/pmanko/clinical-ai-validation-harness)
owns the sibling Catalyst and Hub pins. Catalyst keeps only component tests and
local smoke evidence; cross-model experiments belong in the harness.

### Current implementation and evaluation-path work

- Maintain the implemented real adapter path:
  `Harness → Catalyst ↔ med-agent-hub`, with Catalyst separately executing
  against the analytics source.
- Migrate or deduplicate the local golden-query corpus.
- Add query-to-table scenarios, including absent data, ambiguity, unsafe
  requests and temporal filters.
- Record component SHAs, data/view versions, Gateway query-profile IDs,
  role-model evidence, and trace IDs (plus Hub report-profile IDs after R4).
- Score structured-query correctness and result-table correctness separately.
- Add report grounding, abstention, safety and temporal scoring when R4 is
  available.
- Maintain the implemented manifests, JSONL traces, result records, judging,
  mixed-family publishing, and reviewable reports.
- Add one-click workbench session export only if that separately scoped W3
  pathway is selected.
- Add dashboard artifact validation after D1 exists; it is not part of PR #43.
- Make harness evidence the release gate for later profile or model changes.

The initial suite is engineering evidence, not a claim of clinical validation.
Broader notebook/dashboard scenarios and comparative experiments remain future
evaluation work and do not block D1 implementation.

## Dependency summary

```text
R0 local specs → R1 generic Hub execution → R2 analytics contract
  → R3 query-to-table MVP → R3.1 iterative query notebook (accepted base)
       ├→ D1 supervised dashboard MVP (selected next)
       ├→ G2.10 multi-source/lossless data foundation
       ├→ W2 targeted query assistance
       ├→ W3/CVR evaluation and comparative experiments
       ├→ R4 evidence-linked narrative reporting
       └→ R5 production security and supported deployment
```

Only the accepted R3/R3.1 base blocks D1. The sibling branches above are
parallel pathways with independent approval and acceptance gates.
