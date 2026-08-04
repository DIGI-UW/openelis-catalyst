# Catalyst Roadmap

**Status:** Active local roadmap  
**Product specification:** [`specification.md`](specification.md)  
**Hub contract:** [`med-agent-hub.md`](med-agent-hub.md)

## Roadmap policy

This roadmap replaces the local use of OGC-70 milestone labels M0.0–M5. Those
labels remain historical references in the parent OpenELIS specification.

Delivery follows vertical, test-driven slices:

1. Write contract or behavior tests that fail.
2. Implement the smallest complete path that passes.
3. Exercise the real service boundary.
4. Record evidence and remaining limitations before starting the next slice.

Milestones have exit criteria rather than calendar estimates. A milestone is
not complete because its classes, endpoints, or compose services exist.

R0–R4 target a local demo using demo data, med-agent-hub, and local LLMs.
Production security is future R5 work.

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

- a table-to-report evidence adapter;
- production authentication/authorization and deployment hardening;
- complete comparative Harness experiments and report scoring.

The original Catalyst-local agent code remains compatibility scaffolding, not
the target topology.

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

The previously reviewed stack produced and executed a complete contextual
successor that preserved the base query and added only descending observed-date
ordering.
Both Gemma 4 12B writer and Qwen 2.5 14B reviewer invocations recorded
temperature zero and DRY multiplier zero; an independent PostgreSQL check
confirmed the matching row count and data range. That evidence predates the
Gateway-owned orchestration refactor and is retained as history, not acceptance
of the current pins. Exit still requires a current-pin rerun, the full
scenario/accessibility matrix, user checkpoint, and a record of any output
nondeterminism under the same sampling configuration.

## R4 — Evidence-linked narrative with grounding states

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

## R5 — Future production security and supported deployment

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

**Status:** Umbrella pin and initial real-path runner implemented; comparative
experiments continue in the harness roadmap.

The
[Clinical AI Validation Harness](https://github.com/pmanko/clinical-ai-validation-harness)
owns the sibling Catalyst and Hub pins. Catalyst keeps only component tests and
local smoke evidence; cross-model experiments belong in the harness.

### Current implementation and next work

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
- Produce the harness's manifests, JSONL traces, result records and reviewable
  reports.
- Make harness evidence the release gate for later profile or model changes.

The initial suite is engineering evidence, not a claim of clinical validation.
Broader notebook scenarios and comparative experiments remain future work.

## Dependency summary

```text
R0 local specs
  → R1 generic Hub execution
  → R2 analytics contract
  → R3 query-to-table MVP
       ├→ R3.1 iterative query notebook → harness experiments
       ├→ R4 evidence-linked narrative
       └→ R5 future production security and supported deployment
```
