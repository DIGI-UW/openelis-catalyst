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
  Med-Agent Hub grounding and deterministic validation;
- one canonical SQL editor, manual query versions, validation, explicit
  execution, typed results, compact turn history and contextual follow-ups;
- Hub-discovered selectable profiles with writer/reviewer model and prompt
  provenance;
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
- Canonical med-agent-hub client contract.
- Normative local question, hub request, completion, query, preview,
  execute-request, execution-outcome, policy-outcome, and table JSON Schemas.
- This roadmap.
- Concise repository and component documentation linked to the canonical docs.
- Explicit OGC-70 compatibility mapping.
- Explicit current-versus-target implementation status.
- Retired local M0.2 model-scorecard documentation.

### Exit criteria

- Local documentation contains no claim that Catalyst should own the final
  model/team orchestration.
- Planned profiles are clearly distinguished from profiles available today.
- OGC-70 functionality is classified as retained, reassigned, superseded, or
  deferred.
- The Clinical AI Validation Harness is documented as the umbrella assembly and
  experiment boundary.
- Catalyst-local model scorecards are retired because hub profiles own model
  composition; local golden queries remain engineering fixtures and cross-model
  experiments live in the harness.

## R1 — med-agent-hub client foundation

**Status:** Complete for the local demo

**Goal:** Replace Catalyst-local inference ownership with a small, reliable hub
client while preserving the current public Gateway boundary.

### Test-first slices

1. Profile discovery
   - Failing tests for unavailable hub, malformed model discovery, and missing
     required profile.
   - Implement `GET /v1/models` discovery and readiness reporting.
2. Blocking profile call
   - Failing contract tests for request mapping, timeout, cancellation,
     structured hub errors, and trace propagation.
   - Implement `POST /v1/chat/completions` client behavior.
3. Staged profile call
   - Failing parser tests for hub SSE event order, heartbeats, terminal errors,
     cancellation, and `done`.
   - Implement transparent staged-event relay.
4. Configuration cleanup
   - Failing config tests proving Catalyst no longer requires provider or model
     credentials.
   - Remove Catalyst-local Gemini/LM Studio selection from the target path.

### Ownership rule

Catalyst may select an approved profile ID. It must not select the profile's
internal model roles, prompts, stages, sampling settings, or validation policy.

### Exit criteria

- Catalyst health distinguishes process health, hub health and required-profile
  readiness.
- The hub is the only inference and model-orchestration service on the target
  path.
- Existing Gateway callers receive stable errors when the hub or profile is
  unavailable.
- Catalyst uses the configured hub; the demo stack verifies that the hub uses
  the local model router.
- Unit, contract and live hub-client smoke tests pass.

## R2 — Analytics data and query contract

**Status:** Complete for the seeded OpenELIS demo

**Goal:** Give the hub query profile a versioned analytics vocabulary over OHS
FHIR Data Pipes that matches the relations readable by the configured database
role.

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
- The catalog is compact enough for the hub context budget.
- Freshness includes source watermark, pipeline run, completion state and lag.
- Read-only execution credentials cannot access schemas that have not been
  granted to that role.

## R3 — Query-to-table MVP

**Status:** Implementation complete; final live multi-model acceptance pending

**Goal:** Deliver the first useful product slice:
natural-language question → governed query → table.

### External dependency

med-agent-hub must advertise at least one available Catalyst query profile that
returns `catalyst.query.v1` and owns query planning, generation, review and
repair complexity. Contextual follow-up additionally requires a
revision-capable writer/reviewer profile. Existing clinical answer profiles do
not satisfy this dependency.

### Test-first slices

1. Query contract
   - Add failing fixtures for valid, ambiguous, unsupported, unsafe and
     out-of-scope questions.
   - Validate the normative Catalyst question, hub request,
     completion-envelope, and response JSON Schemas independently of prose.
   - Add API contract tests for ready preview, clarification, unsupported,
     hub rejection, Catalyst policy rejection, and hub-failure responses.
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
   - Add full-stack smoke assertions that hub `LLM_BASE_URL` resolves to the
     local model router and the analytics source uses seeded demo data.
   - Catalyst verifies hub/profile readiness; hub and compose own model-router
     configuration.
   - Label all UI and API responses as demo behavior without production
     security claims.

### MVP exit criteria

- Required hub profile discovery succeeds.
- The profile advertises `catalyst.query.v1`, and its OpenAI-compatible
  completion content validates against the normative local JSON Schema.
- Golden questions return the expected seeded results, not merely plausible
  SQL.
- Disallowed and out-of-scope queries are rejected deterministically.
- Preview acceptance is non-expiring, one-time and revalidated at execution.
- Empty, partial, truncated and failed results are distinguishable.
- Table output includes freshness, schema, query, profile and trace provenance.
- The deployed path uses demo data and local LLMs only.
- No result rows are sent to an external model provider.
- The full path runs through the deployed Catalyst and hub services.

The umbrella Clinical AI Validation Harness now owns the sibling Catalyst/Hub
pins and real-path experiment runner. Final live acceptance remains a merge
checkpoint; local evidence is generated by `mvp-health.sh`, `test_mvp_live.sh`,
`test_data_pipes_incremental.sh`, and the notebook Playwright project.

## R3.1 — Iterative query notebook

**Status:** Implemented; focused live path passed, full acceptance pending

The linear notebook extends R3 without adding chat or branching:

1. Record initial and follow-up turns in the append-only event ledger.
2. Keep one active SQL editor; save contract-valid dirty buffers as human
   versions and retain unresolved buffers only as snapshots.
3. Validate and Run the exact active version, preserving version-labelled stale
   results.
4. Generate one complete successor from the exact editor snapshot and current
   instruction, using at most five prior follow-up instructions.
5. Always invoke the different-family reviewer for revision-capable profiles,
   re-lint its complete correction and preserve writer/reviewer evidence.
6. Restore sessions, versions, executions and compact history without model
   calls.

The reviewed stack has produced and executed a complete contextual successor
that preserved the base query and added only descending observed-date ordering.
Both Gemma 4 12B writer and Qwen 2.5 14B reviewer invocations recorded
temperature zero and DRY multiplier zero; an independent PostgreSQL check
confirmed the matching row count and data range. Exit still requires the full
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
- Record component SHAs, data/view versions, hub profile IDs and trace IDs.
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
  → R1 hub client
  → R2 analytics contract
  → R3 query-to-table MVP
       ├→ R3.1 iterative query notebook → harness experiments
       ├→ R4 evidence-linked narrative
       └→ R5 future production security and supported deployment
```
