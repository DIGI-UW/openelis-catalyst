# Catalyst Product Specification

**Status:** Iterative query notebook MVP accepted; Dashboard MVP selected next
**Scope:** `DIGI-UW/openelis-catalyst`  
**Deployment mode:** Local demo with demo data and local LLMs  
**Supersedes locally:** The standalone-agent architecture inherited from OGC-70  
**Last updated:** 2026-08-04

## Product statement

Catalyst is a supervised dashboard creator for OpenELIS reporting. It turns a
laboratory user's natural-language question into a governed query, executes
that query against an analytics data source, and returns a typed table. The
user can then promote an executed result into a manually configured, versioned
dashboard artifact. Evidence-linked narrative reporting remains a separate,
optional pathway.

For governed query generation, Catalyst is an **orchestrating client of
med-agent-hub**. Gateway owns the selectable query profiles, role-to-model
mapping, prompts, structured-output formats, deterministic lint/correction,
optional review, and evidence assembly. med-agent-hub provides the shared
provider/router boundary and executes one model role at a time through
`POST /v1/hub/generate`. Planned narrative reports may use Hub-owned product
profiles, but that future flow is separate from the implemented query engine.

The accepted product foundation is:

> Question → generated query → manual versions → execution → contextual
> follow-up → complete successor query

The selected next product milestone is:

> Executed Query vN → Dashboard Draft v1 → manual table/chart configuration →
> saved dashboard version

Multi-source hardening, automated query repair, experiment export, narrative
reports, and production security continue as parallel pathways. They can
improve the dashboard later but do not block the single-source Dashboard MVP.

## Document authority

This file is the canonical local product and architecture specification.

- [`roadmap.md`](roadmap.md) defines the parallel development pathways, selected
  milestone, dependencies, and exit criteria.
- [`med-agent-hub.md`](med-agent-hub.md) defines the hub client contract.
- [`../AGENTS.md`](../AGENTS.md) contains development and test instructions,
  not product architecture.
- The original
  [OGC-70 specification](https://github.com/DIGI-UW/OpenELIS-Global-2/tree/develop/specs/OGC-070-catalyst-assistant)
  remains useful history. The compatibility mapping below records what this
  specification retains, reassigns, or defers.

The current implementation provides the query-to-table path in Gateway,
analytics, med-agent-hub, and the React sidecar. Original RouterAgent,
CatalystAgent, SchemaAgent, and SQLGenAgent code remains legacy compatibility
scaffolding.

## Goals

1. Produce correct, reviewable tabular answers to common laboratory reporting
   questions.
2. Keep governed-query profiles, prompts, role composition, validation stages,
   and their versioned evidence together inside Catalyst Gateway.
3. Ground generation in the runtime-discovered PostgreSQL relations and columns
   that the configured read-only database role can actually query.
4. Keep database credentials and query execution outside the LLM boundary.
5. Preserve source, query, schema, and data-freshness provenance for every
   result.
6. Let a user turn an executed result into a manually configured, versioned
   dashboard artifact without losing its source lineage.
7. Support an evidence-linked report with explicit grounding states, generated
   from an already governed result table.
8. Emit session, turn, version, model, prompt, validation, and execution evidence
   that maps cleanly into the Clinical AI Validation Harness.

## Non-goals

- Adding Catalyst-specific query semantics or multi-step orchestration back to
  med-agent-hub.
- Letting med-agent-hub connect directly to the analytics database.
- Using external model providers in the demo path.
- Processing production patient data or claiming production security.
- Granting database access beyond the configured PostgreSQL role.
- Building multi-widget layouts, report scheduling, sharing, automatic refresh,
  publication, or cross-report memory in Dashboard MVP.
- Automatic query execution, branching from arbitrary old versions, or a
  general-purpose chat interface in the notebook MVP.

## System boundaries

| System | Owns | Does not own |
| --- | --- | --- |
| OpenELIS | Source laboratory data; future production identity and permissions | LLM orchestration |
| OHS FHIR Data Pipes | FHIR extraction, incremental synchronization and per-resource Parquet or database projections | Cross-resource business metrics or user-facing query workflow |
| Analytics PostgreSQL store | FHIR projections, semantic marts, role-level grants and runtime schema metadata | LLM orchestration |
| Catalyst | Query profiles, prompts, role composition, runtime catalog, structured query orchestration, deterministic diagnostics, append-only notebook and dashboard state, read-only execution, query evidence and typed table response | Model-provider credentials or router implementation |
| med-agent-hub | Generic single-role generation, model-provider/router connection, shared transport controls, and future report product profiles | Catalyst query profiles, catalog semantics, query orchestration, database credentials or SQL execution |
| Catalyst/OpenELIS UI | One active SQL editor, manual versions, validation, execution, follow-up composer, timeline, table rendering, and supervised dashboard configuration | Hidden model orchestration |
| Clinical AI Validation Harness | Pinned umbrella assembly, scenarios, comparison runs, scoring and reviewable evidence | Production request serving |

## Target architecture

```text
OpenELIS FHIR
  → OHS FHIR Data Pipes
  → per-resource analytics tables
  → PostgreSQL projections and semantic marts readable by the Catalyst role
  → Catalyst runtime catalog/context adapter and governed-query engine
      → med-agent-hub generic role generation → local model router
      → Catalyst deterministic lint → optional review → deterministic re-lint
  → append-only query notebook and local read-only demo execution
  → typed table
  → optional med-agent-hub report profile
```

The analytics engine and SQL dialect must be explicit in every query request.
FHIR Data Pipes can support more than one warehouse engine; generated SQL is
not assumed to be portable across Spark SQL, PostgreSQL, DuckDB, or other
targets.

## Catalyst-facing demo API

The query-to-table UI uses the implemented Catalyst API below.

### Submit a question

`POST /v1/catalyst/queries`

Request:
[`contracts/catalyst-question-request-v1.schema.json`](contracts/catalyst-question-request-v1.schema.json)

Responses:

| HTTP | Body | Meaning |
| --- | --- | --- |
| `201` | `catalyst.preview.v1` | Query is ready for review and acceptance |
| `200` | Non-`ready` `catalyst.query.v1` | Clarification, unsupported, or rejected outcome; rejected generation may include a diagnostic candidate that is never executable |
| `400` | Request validation error | Malformed demo question request |
| `422` | `catalyst.policy.outcome.v1` | The query engine finalized `ready`, but Catalyst execution policy rejected it |
| `502` | Hub integration error | Hub unavailable, incompatible, or invalid response |

### Execute an accepted preview

`POST /v1/catalyst/previews/{previewId}/execute`

Request:
[`contracts/catalyst-execute-request-v1.schema.json`](contracts/catalyst-execute-request-v1.schema.json)

Responses:

| HTTP | Body | Meaning |
| --- | --- | --- |
| `200` | `catalyst.table.v1` | Stored successful result, including same-key replay |
| `202` | `catalyst.execution.outcome.v1` with `in_progress` | Same-key execution is active; poll |
| `400` | Request validation error | Path and body preview IDs differ or request is malformed |
| `404` | Execution outcome with `not_found` | Preview is unknown |
| `409` | Execution outcome with `conflict` | Preview already consumed or idempotency key conflicts |
| `502` | Execution outcome with `failed` | Demo analytics execution failed; create a new preview |

### Poll or replay execution

`GET /v1/catalyst/executions/{previewId}?idempotencyKey={key}`

The endpoint returns the same stored `200` table, `202` in-progress outcome, or
non-success outcome associated with that key. An unknown pair returns the
versioned `404` `not_found` outcome. Polling never starts a query.

The current `/v1/chat/completions` endpoint remains a legacy compatibility
surface during migration. It is not the normative query-to-table API.

### Iterative workbench

The notebook UI uses the workbench API as its primary surface:

- `GET /v1/catalyst/workbench/catalog` returns the runtime schema guide and
  completion catalog from the same database-role-visible relations supplied to
  the models and validator.
- `POST /v1/catalyst/workbench/sessions` creates an isolated session and records
  the initial requested and terminal turn.
- `GET /v1/catalyst/workbench/sessions/{sessionId}` restores the current query,
  editor snapshot, validation, execution and provenance without inference.
- `GET /v1/catalyst/workbench/sessions/{sessionId}/turns` returns the compact
  chronological timeline.
- `POST /v1/catalyst/workbench/sessions/{sessionId}/turns` generates one complete
  successor query from the exact active editor snapshot and instruction.
- Version, validation, execution and generation-evidence routes store manual
  edits, run exact query versions and expose typed writer/reviewer evidence.

Turn requests bind the current stored version/digest and exact editor
snapshot/digest. Stale lineage returns `409 stale_query_version`; only one
generation may be active per session. A failed generation records raw typed
evidence and leaves the preceding query editable. `New session` is the boundary
for unrelated work.

### Data sources

`GET /v1/catalyst/data-sources` lists every registered data source
(`catalyst.data-sources.v1`: `defaultDataSourceId` plus each source's `id`,
`label`, and `available`). Each source is its own analytics database and
catalog; a source registered but not yet provisioned (its catalog file
absent) lists `available: false` and cannot be targeted.

Any workbench request that creates or targets state — session creation, a
turn, or a `dataSourceId`-taking GET (`/v1/catalyst/dataset`,
`/v1/catalyst/workbench/catalog`) — accepts an optional `dataSourceId`. A
session is source-agnostic: the source targeted by its most recent turn
(falling back to the session's initial source) is the source the next
untargeted turn inherits, so "adapt this query to the other data source"
works mid-session without starting over. Catalog staleness
(`409 stale_catalog_version`) is judged per source, against the baseline that
source was last seen at in this session — switching sources never trips a
false conflict on first use.

## Primary workflow: query to table

1. The demo UI creates a session from the user's question.
2. Catalyst resolves the runtime analytics catalog, schema version, SQL
   dialect, and data-freshness metadata.
3. Catalyst selects a Gateway-owned profile and assembles the exact
   question/catalog/policy context plus the profile's prompt, model,
   structured-output format, and sampling knobs.
4. Gateway invokes med-agent-hub's generic `POST /v1/hub/generate` once per
   model role. It parses the writer's complete candidate, runs deterministic
   contract/SQL/semantic lint and bounded correction, optionally asks the
   configured reviewer to approve or return a complete correction, then
   deterministically re-lints any correction and finalizes
   `catalyst.query.v1`.
5. If status is `needs_clarification`, Catalyst returns the clarification
   without SQL. If status is `unsupported` or `rejected`, Catalyst returns a
   stable non-executable outcome with the contract `message`. A rejected
   generation may retain its candidate and deterministic findings for manual
   diagnosis, but it cannot be accepted or run. Only `ready` continues.
6. Catalyst applies the execution-boundary policy to the finalized query. A
   failure returns `catalyst.policy.outcome.v1`; no model result bypasses this
   deterministic boundary.
7. Catalyst records the selected model output as an append-only query version.
8. The latest turn owns the single SQL editor. A dirty, contract-valid editor
   buffer is saved as a human version before Validate, Run or follow-up; an
   unresolved buffer remains an input snapshot without being promoted.
9. Validate produces deterministic diagnostics. Run executes the exact visible
   query version against the local analytics store with read-only credentials,
   timeout and row limits; validation is advisory in this research workbench.
10. Catalyst returns typed columns, rows, truncation status, freshness and
    provenance. Later edits or successors mark those results stale rather than
    hiding them.
11. A follow-up uses the exact active SQL/parameters, current instruction,
    bounded instruction history and only exact-digest validation/execution
    summaries. The writer returns a complete successor; when the selected
    Gateway profile includes a reviewer, it may approve or return one complete
    correction, which Gateway re-lints.

The hub never receives database credentials and never executes the query.

## Selected next workflow: executed result to dashboard

Dashboard MVP starts from one successful, non-stale query execution. It does
not generate or execute another query.

1. The user chooses **Create dashboard** from the result labelled with its
   exact query version.
2. Catalyst creates a draft bound to the session, query version and digest,
   execution, data source/catalog version, typed result schema and result
   digest. `resultDigest` is SHA-256 over RFC 8785 JSON Canonicalization Scheme
   bytes of the stored successful `catalyst.table.v1` payload.
3. The draft contains exactly one user-selected presentation: the existing
   table, a bar chart, or a line chart when the result's typed columns support
   that choice.
4. The user manually configures the title, selected columns or axis bindings,
   labels and sort order, previews the result, and explicitly saves a version.
5. Every save appends an immutable dashboard version with author and source
   provenance. Reload restores the latest saved version without a model call or
   query re-execution.
6. A later query edit, successor, or execution never silently rebinds the
   dashboard. Catalyst keeps the saved artifact visible and marks its source as
   stale when the active workbench state has moved on.

Dashboard MVP is deliberately narrow: one artifact, one table or chart, manual
configuration, local persistence and refresh restoration. It excludes
multi-widget layouts, model-generated visualization specifications, result
narratives, sharing, scheduling, automatic refresh, export/publication,
authorization and production deployment.

## Secondary workflow: table to report

This workflow also uses the local demo hub and local LLMs. It is limited to
demo data until future production security work is complete.

1. Catalyst converts the governed table into compact evidence records with
   stable source identifiers.
2. Catalyst calls a report profile on med-agent-hub.
3. The hub owns context selection, answer generation, review, grounding,
   citations, and in-depth stages.
4. Catalyst relays staged events and final provenance to the UI.

The demo configuration must point med-agent-hub at the local model router.

## Query profiles and Hub execution

Gateway defines the implemented governed-query profiles in
`catalyst-gateway/src/catalyst/query_profiles.py`:

| Profile | Roles | Purpose |
| --- | --- | --- |
| `catalyst-query-gemma-4-12b-q4` | Gemma 4 12B Q4 writer | **Product default.** CPU-oriented writer-only demo lane |
| `catalyst-query-gemma-4-12b-q4-checked` | Gemma 4 12B Q4 writer and same-model reviewer | Optional CPU-oriented self-check |
| `catalyst-query-gemma-4-12b` | Gemma 4 12B writer | Full-weight writer-only lane |
| `catalyst-query-gemma-4-12b-qwen2.5-14b-checked` | Gemma 4 12B writer and Qwen 2.5 14B reviewer | Recommended GPU cross-family review lane |
| `catalyst-query-qwen-coder-1.5b` | Qwen 2.5 Coder 1.5B Q4 writer | Bundled writer-only fallback lane |

`GET /v1/catalyst/query-options` exposes this Gateway-owned registry to the UI,
including profile labels, writer/reviewer model IDs, stages, and exact
credential-free profile evidence. Per-turn profile switching is allowed and
fully recorded. Every listed profile can generate a contextual revision; that
capability does not depend on having a reviewer. A profile without
`query_review` is writer-only; a reviewed profile adds the reviewer after the
writer/deterministic-lint stage.

Gateway derives profile availability from the versioned, credential-free
router catalog served by Hub. A profile is advertised as available only when
every exact writer/reviewer alias is advertised; an unavailable selection is
rejected before model invocation or session/event creation, without
substitution. Inventory failure fails closed. Availability is a point-in-time
observation, so a backend change between discovery and invocation still
surfaces as a recorded generation/backend failure.

Both roles use `temperature: 0` and `dry: 0`. Gateway sends the latter to Hub as
`dry_multiplier: 0`; declared role knobs and effective per-invocation
configuration are retained in workbench evidence. These settings prevent a
prose-oriented repetition penalty from corrupting repeated SQL identifiers,
but they are not treated as reproducibility proof.

The pinned Hub is intentionally domain-agnostic for this flow. Gateway sends
one model, message list, optional JSON `response_format`, temperature, DRY
multiplier, and token cap to `POST /v1/hub/generate`; Hub returns the selected
model ID and assistant content. Hub owns provider/auth/timeout transport and
the model-router connection, not Catalyst query semantics or profile policy.

Planned narrative reports remain separate: `single-e4b-checked` and
`team-med-checked` are Hub product profiles whose Catalyst integration is
deferred to R4.

## Query contract

Initial queries use
[`contracts/catalyst-query-request-v1.schema.json`](contracts/catalyst-query-request-v1.schema.json).
Contextual revisions use
[`contracts/catalyst-query-request-v2.schema.json`](contracts/catalyst-query-request-v2.schema.json)
with the versioned revision-context, editor-snapshot and workbench-turn
contracts. They bind the current instruction, target/catalog, policy,
correlation IDs, current version/digest, exact editor SQL/parameters/digest,
bounded instruction history and matching validation/execution summaries. They
exclude result rows, credentials, raw traces, historical SQL copies and
unrelated sessions. Gateway selects the role-specific model-backend
`response_format` and passes it through Hub's generic executor.

The normative finalized-query schema is
[`contracts/catalyst-query-v1.schema.json`](contracts/catalyst-query-v1.schema.json).
The earlier remote-profile completion envelope remains documented by
[`contracts/catalyst-query-completion-v1.schema.json`](contracts/catalyst-query-completion-v1.schema.json).
The implemented in-process Gateway engine does not use that outer envelope:
each generic Hub role call returns assistant content, which Gateway parses
against the role's structured-output schema before it finalizes
`catalyst.query.v1`. Catalyst never extracts executable query instructions from
surrounding prose.

The implemented `catalyst.query.v1` response contains:

- `contractVersion`
- `deploymentMode`: `demo`
- `status`: `ready`, `needs_clarification`, `unsupported`, or `rejected`
- `question`: exact original request text
- `target` with data source, catalog version, approved view set and SQL dialect
- `sql`, `parameters`, and `expectedColumns` only when status is `ready`
- `clarification` when status is `needs_clarification`
- `message` when status is `unsupported` or `rejected`
- `diagnosticCandidate` on rejected generation or review outcomes when model
  output is available; it is explicitly non-executable and may contain the
  parsed candidate or raw output plus deterministic attempt findings
- `validation` with Gateway deterministic checks plus any reviewer findings
- `provenance` with profile ID, trace ID and context source identifiers

The contract contains no database credentials and no query results.

Catalyst requires response `question` to equal the submitted current
instruction exactly and binds model provenance to the selected Gateway profile.
It also requires returned `dataSource`, `catalogVersion`, and `dialect` to equal
the request target, and every `approvedViews` entry to belong to the requested
catalog. Normalized intent belongs in trace metadata, not this execution
contract.

Catalyst rejects:

- an unknown contract version;
- a response that fails the normative JSON Schema;
- an unavailable, substituted or provenance-mismatched profile;
- SQL that is not a single read-only statement;
- references outside the approved view set;
- a dialect mismatch;
- a query exceeding configured complexity, timeout, or row limits.

Model review improves generation quality but does not replace Catalyst's
deterministic execution boundary.

The contract supports dialect-neutral `:name` placeholders. When placeholders
are present, Catalyst checks their typed values and the execution adapter binds
them without string interpolation. Named parameters are recommended for longer
queries but are not a condition for manually validating or running otherwise
valid read-only SQL in the research workbench.

## Preview and execution contract

The normative preview, execution, and Catalyst policy-outcome schemas are:

- [`contracts/catalyst-preview-v1.schema.json`](contracts/catalyst-preview-v1.schema.json)
- [`contracts/catalyst-execute-request-v1.schema.json`](contracts/catalyst-execute-request-v1.schema.json)
- [`contracts/catalyst-execution-outcome-v1.schema.json`](contracts/catalyst-execution-outcome-v1.schema.json)
- [`contracts/catalyst-policy-outcome-v1.schema.json`](contracts/catalyst-policy-outcome-v1.schema.json)

The preview carries the exact question, target, SQL, typed parameters, and
expected columns that its digest covers so the UI displays the authoritative
payload being accepted.

Preview state transitions are transactional:

```text
awaiting_acceptance
  → consuming
      → succeeded
      → failed
```

`queryDigest` is SHA-256 over RFC 8785 JSON Canonicalization Scheme bytes for
the stored `{question, target, sql, parameters, expectedColumns}` object.
`target` includes the catalog version. Execution atomically compares that
digest before moving from `awaiting_acceptance` to `consuming`.

Previews do not expire. Only one idempotency key can consume a preview.
Repeating the same key while
execution is active returns `in_progress` and permits polling with that key.
Repeating it after completion returns the stored outcome without running the
query again; a different concurrent key conflicts. Failed execution does not
reopen the preview—a new preview is required.
Successful execution returns `catalyst.table.v1`. In-progress, conflicting,
consumed, and failed requests return the versioned non-success
execution outcome. A same-key replay returns the originally stored table or
non-success outcome with no second query execution.

The `{previewId}` path value and execute-request `previewId` must match exactly.
Unknown previews and unknown polling pairs return the versioned `not_found`
outcome.

## Table contract

The normative successful result schema is
[`contracts/catalyst-table-v1.schema.json`](contracts/catalyst-table-v1.schema.json).
It uses tagged values so decimals retain precision and dates, timestamps,
nulls, booleans and integers remain distinguishable. Runtime validation also
checks that each row has exactly one value per column and that tagged cell
types match column logical types.

A successful table response contains:

- the original question;
- the accepted preview ID and query digest;
- the accepted query and bound parameters;
- typed column metadata, including units when available;
- rows;
- returned row count, optional exact total, and explicit total-exactness;
- truncation and limit information;
- analytics source and schema version;
- structured source watermark, pipeline run, completion state, and observed
  lag;
- execution duration;
- trace identifiers;
- warnings suitable for display.

Runtime checks require `rowCount.returned == rows.length`, one cell per column,
and matching expected/table column names and logical types. An empty successful
table is distinct from a versioned failed execution outcome.
Table `source.dataSource`, `source.catalogVersion`, and `source.views` must
match the accepted preview target.

## Analytics data contract

The runtime catalog contains every non-system PostgreSQL relation and column
for which the configured database role has `SELECT`. This makes the schema
guide, SQL completion, model grounding and deterministic validator agree about
what is queryable. Database grants—not a hard-coded product whitelist—define
the accessible boundary.

The installed demo includes FHIR Data Pipes projections and a semantic result
fact. Important reporting surfaces include:

- laboratory result facts;
- sample and test volumes;
- positivity and categorical result summaries;
- turnaround time;
- pending or validation work queues.

Each governed semantic view's catalog entry carries only what the gateway
actually reads (`Catalog.load`): approval, a stable name and version,
documented grain, typed columns with units, and semantic dimensions
(canonical analyte names and aliases). The catalog is GENERATED — from
`COMMENT ON VIEW`/`COMMENT ON COLUMN` on the curated SQL plus a small
per-source `catalog-overlay.json` (identity, approved views, semantic
canonical values validated against live data) — never hand-maintained. Older
hand-written catalogs also carried allowed-filters, terminology-note,
freshness, and example-query sections; those are inert (unread by the
gateway) and are not part of the generated shape.

FHIR Data Pipes produces per-resource analytics representations. Cross-resource
facts such as turnaround time or order-to-result relationships require a
separate semantic transformation layer; a single FHIR `ViewDefinition` is not
treated as a general cross-resource join mechanism.

Raw FHIR nesting and transactional OpenELIS are not copied into prompts unless
they are actually exposed as readable PostgreSQL relations. Flattening, joins
and semantic naming remain data-platform responsibilities.

Freshness metadata includes source watermark, pipeline run ID, completion state
and observed lag rather than one ambiguous timestamp.

## Demo safeguards and future security

The MVP is a local demo, not a production clinical system.

Demo safeguards:

1. Use synthetic or approved demo data only.
2. Use med-agent-hub with local LLMs only.
3. Keep database credentials out of model context.
4. Execute through a read-only analytics identity.
5. Permit one parsed read-only statement over relations granted to the database
   role; deterministic diagnostics remain visible even when advisory.
6. Require an explicit Run action. The notebook never executes model output
   automatically and never expires a user's saved query.
7. Enforce statement timeout, row limit and resource limit.
8. Keep enough trace metadata to reproduce a demo result.

Future production hardening includes authentication, OpenELIS RBAC, tenant and
facility scope, PHI classification, service authentication, network policy,
encryption, secrets, durable audit, trace access/retention, warehouse
governance, threat modeling and security testing. None of those controls are
claimed by the demo MVP.

## Functional requirements

- **CAT-FR-001:** Accept natural-language laboratory reporting questions.
- **CAT-FR-002:** Expose Gateway-owned query profiles and record the selected
  profile/model/prompt/configuration evidence for every turn.
- **CAT-FR-003:** Supply the versioned runtime catalog visible to the read-only
  database role to the query profile, editor completion and validator.
- **CAT-FR-004:** Accept only the versioned structured query contract.
- **CAT-FR-005:** Deterministically validate and safely execute accepted
  read-only queries.
- **CAT-FR-006:** Return typed table results with freshness and provenance.
- **CAT-FR-007:** Keep one canonical SQL editor, save valid manual buffers as
  human versions, preserve unresolved snapshots, and require explicit Run.
- **CAT-FR-008:** Reserve a trusted execution boundary for future authorization
  and facility scope.
- **CAT-FR-009:** Optionally generate an evidence-linked report with explicit
  grounding states from a governed table through an approved hub profile.
- **CAT-FR-010:** Relay staged report events without interpreting or
  re-orchestrating hub stages.
- **CAT-FR-011:** Record profile, query, execution, and evidence trace metadata
  sufficient to reproduce demo behavior.
- **CAT-FR-012:** Expose health that distinguishes Catalyst, Hub transport,
  configured query-profile, model-router, analytics-source, and
  execution-service readiness.
- **CAT-FR-013:** Preserve a compact chronological turn timeline and restore it
  without invoking a model.
- **CAT-FR-014:** Generate a complete successor from the exact active editor
  buffer and current follow-up instruction, with bounded context and no result
  rows.
- **CAT-FR-015:** Keep prior results visible and label the exact query version
  that produced them; mark them stale after edits or successor generation.
- **CAT-FR-016:** Create a dashboard draft only from a successful query
  execution and bind it to the exact query, execution, source and result
  evidence that produced it.
- **CAT-FR-017:** Let the user choose exactly one table or compatible bar/line
  visualization and manually configure its title, bindings, labels and sort.
- **CAT-FR-018:** Append immutable dashboard versions on explicit save and
  retain author plus source provenance for every version.
- **CAT-FR-019:** Restore the latest saved dashboard and its version history
  after refresh without invoking a model or re-executing its query.
- **CAT-FR-020:** Keep a dashboard visible but mark its source stale when the
  active query or execution no longer matches; never silently rebind it.
- **CAT-FR-021:** Keep Dashboard MVP independent of multi-source completion,
  automated SQL repair, experiment export, narrative reporting and production
  security pathways.

## Accepted query-workbench MVP

MVP requires an end-to-end deployment that demonstrates:

1. Gateway profile discovery, including writer/reviewer model identities, plus
   Hub generic-executor readiness.
2. At least one natural-language question producing a valid structured query
   against an approved semantic-layer view over Data Pipes projections.
3. Query review and governed read-only execution.
4. Correct typed table output against seeded expected data.
5. Deterministic rejection of disallowed and out-of-scope queries.
6. Explicit, idempotent execution of the exact selected query version.
7. Local med-agent-hub and local model-router execution only.
8. Demo data only.
9. Complete freshness, query, profile, source, and trace provenance.
10. Automated happy-path and failure-path integration tests.
11. Initial question, manual edit, Validate/Run, contextual follow-up, complete
    successor, rerun and refresh restoration in one linear session.
12. Independent PostgreSQL comparison for live acceptance, with model
    nondeterminism recorded rather than treated as test determinism.

The deterministic component, mocked-browser, real-model, independent
PostgreSQL, keyboard-only, narrow-layout, and actual 200%-browser-zoom gates
passed on the accepted MVP candidate. Failed model candidates and
temperature-zero output differences remain retained evidence; syntactic
validity alone is not correctness. The deterministic Playwright notebook path
now preserves the accepted keyboard focus order and 200%-equivalent reflow
boundary. An optional report demonstration does not substitute for
table/notebook acceptance.

## Dashboard MVP acceptance

Dashboard MVP is complete only when a user can:

1. Run a query and create a draft from that exact successful execution.
2. Configure and save one table, bar chart, or line chart without a model call.
3. Reload the application and recover the saved artifact and immutable version
   history without query re-execution.
4. Trace the artifact to its session, query version/digest, execution, data
   source/catalog version, result schema/digest, and author.
5. Edit or replace the underlying query and see the saved dashboard remain
   visible with an explicit stale-source state.
6. Complete the flow using the accepted keyboard and 200%-zoom accessibility
   boundaries.

Acceptance uses a deterministic seeded result fixture plus one real Catalyst
execution whose rendered values are independently checked against PostgreSQL.
The milestone makes no claim about multi-dashboard composition, sharing,
scheduling, narrative correctness or production access control.

Because the local demo has no authentication, dashboard-version authorship
records only the actor kind `human`; it does not claim a verified user identity.
Production identity attribution remains part of R5.

## Relationship to OGC-70

OGC-70 has specifications because Catalyst was originally planned as a
cross-cutting OpenELIS feature using Spec Kit. Its scope included Python
services, an OpenELIS Java execution layer, and an OpenELIS Carbon UI. The
Python source was later extracted into this repository while the feature
specification, plan, and task history remained in `OpenELIS-Global-2`.

This specification preserves the product goal but changes component ownership
and delivery order.

| OGC-70 capability | Local direction |
| --- | --- |
| Natural language to SQL | Retained; generated by the Gateway-owned query engine through Hub's generic role executor |
| SQL review and table results | Retained as the first product MVP |
| Schema RAG/MCP | Reframed as one runtime-discovered role-readable PostgreSQL catalog shared by model grounding, completion and validation |
| Gemini and LM Studio clients in Catalyst | Reassigned to med-agent-hub and its model router |
| RouterAgent → SchemaAgent → SQLGenAgent | Superseded by Gateway profile orchestration over the runtime catalog |
| MCP SQL validation | Retained as a deterministic boundary, independent of model review |
| OpenELIS Java RBAC/execution | Deferred to production hardening; demo execution remains local and read-only |
| Carbon UI integration | Retained as a future host integration; not required for the first sidecar MVP |
| Base `clinlims` SQL | Not granted by the demo analytics role; any future exposure is controlled through database grants and the runtime catalog |
| CloudSafe and LocalPHI security modes | Deferred; the current target is explicitly local demo data with local LLMs |
| Golden-query evaluation | Moved into the umbrella Clinical AI Validation Harness with real-path execution and versioned evidence |
| Dashboards | Selected next: one manually configured, versioned artifact from one executed result |
| Advanced report storage, sharing and scheduling | Deferred |

## Clinical AI Validation Harness integration

The
[Clinical AI Validation Harness](https://github.com/pmanko/clinical-ai-validation-harness)
is the umbrella repository. It pins Catalyst and med-agent-hub as sibling
submodules and builds the Hub sibling directly. Catalyst contains no nested Git
submodule; standalone use may clone the same immutable Hub commit as a
disposable fallback.

The real path is:

```text
Harness → Catalyst ↔ med-agent-hub
                  └→ analytics source → table/report
```

Initial harness support emits versioned run manifests, events, query/table
evidence and deterministic assertions while preserving component SHAs, dataset
and catalog versions, profile/model/prompt/configuration provenance and trace
IDs. The workbench's append-only turn/version events are designed to map into
that envelope. Comparative experiments, broader notebook scenario coverage and
report grounding/abstention scoring remain subsequent harness work.
