# Catalyst Product Specification

**Status:** Target architecture  
**Scope:** `DIGI-UW/openelis-catalyst`  
**Deployment mode:** Local demo with demo data and local LLMs  
**Supersedes locally:** The standalone-agent architecture inherited from OGC-70  
**Last updated:** 2026-07-15

## Product statement

Catalyst is the OpenELIS reporting integration service. It turns a laboratory
user's natural-language question into a governed query, executes that query
against an analytics data source, and returns a typed table. It can then ask
med-agent-hub to turn the table into an evidence-linked narrative report with
explicit grounding states.

Catalyst is a **client of med-agent-hub**. It does not select language models,
compose model teams, own prompts, or implement LLM review loops. Those concerns
belong to med-agent-hub profiles.

The first product milestone is:

> Natural-language question → reviewed query → table output

Narrative and in-depth reports build on the same table and provenance after the
table milestone works reliably.

## Document authority

This file is the canonical local product and architecture specification.

- [`roadmap.md`](roadmap.md) defines delivery order and exit criteria.
- [`med-agent-hub.md`](med-agent-hub.md) defines the hub client contract.
- [`../AGENTS.md`](../AGENTS.md) contains development and test instructions,
  not product architecture.
- The original
  [OGC-70 specification](https://github.com/DIGI-UW/OpenELIS-Global-2/tree/develop/specs/OGC-070-catalyst-assistant)
  remains useful history. The compatibility mapping below records what this
  specification retains, reassigns, or defers.

The current Python implementation still contains the original RouterAgent,
CatalystAgent, SchemaAgent, and SQLGenAgent prototype. That code is migration
scaffolding; its presence does not override this specification.

## Goals

1. Produce correct, reviewable tabular answers to common laboratory reporting
   questions.
2. Keep model, prompt, validation-stage, and local-model-team complexity inside
   med-agent-hub.
3. Query curated analytics views rather than expose an LLM to the transactional
   OpenELIS schema.
4. Keep database credentials and query execution outside the LLM boundary.
5. Preserve source, query, schema, and data-freshness provenance for every
   result.
6. Support an evidence-linked report with explicit grounding states, generated
   from an already governed result table.
7. Make transition to the Clinical AI Validation Harness an explicit post-MVP
   goal without coupling MVP delivery to that external repository.

## Non-goals

- Reimplementing med-agent-hub orchestration inside Catalyst.
- Letting med-agent-hub connect directly to the analytics database.
- Using external model providers in the demo path.
- Processing production patient data or claiming production security.
- Generating arbitrary SQL against the production `clinlims` database.
- Building report scheduling, sharing, dashboards, or cross-report memory in
  the first MVP.
- Updating the Clinical AI Validation Harness as part of the local spec reset.

## System boundaries

| System | Owns | Does not own |
| --- | --- | --- |
| OpenELIS | Source laboratory data; future production identity and permissions | LLM orchestration |
| OHS FHIR Data Pipes | FHIR extraction, incremental synchronization and per-resource Parquet or database projections | Cross-resource business metrics or user-facing query workflow |
| Analytics semantic layer | Cross-resource marts, governed metrics, approved views and catalog metadata | LLM orchestration |
| Catalyst | Hub client, approved-view catalog, deterministic query policy, demo execution adapter, trace metadata and table response | Models, prompts or model-team topology |
| med-agent-hub | Product profiles, model routing, context selection, query-generation stages, repair/review loops, evidence ledger and report stages | Database credentials or SQL execution |
| Catalyst/OpenELIS UI | Query review, acceptance, table rendering and report-stage rendering | Hidden model orchestration |
| Clinical AI Validation Harness | Post-MVP scenarios, comparison runs, scoring and reviewable evidence | Production request serving |

## Target architecture

```text
OpenELIS FHIR
  → OHS FHIR Data Pipes
  → per-resource analytics tables
  → governed cross-resource semantic marts and approved views
  → Catalyst catalog/context adapter
  → med-agent-hub query profile
  → Catalyst deterministic validation
  → local read-only demo execution
  → typed table
  → optional med-agent-hub report profile
```

The analytics engine and SQL dialect must be explicit in every query request.
FHIR Data Pipes can support more than one warehouse engine; generated SQL is
not assumed to be portable across Spark SQL, PostgreSQL, DuckDB, or other
targets.

## Catalyst-facing demo API

The query-to-table UI uses a small Catalyst API. These routes are target
contracts and are not implemented by the current Gateway prototype.

### Submit a question

`POST /v1/catalyst/queries`

Request:
[`contracts/catalyst-question-request-v1.schema.json`](contracts/catalyst-question-request-v1.schema.json)

Responses:

| HTTP | Body | Meaning |
| --- | --- | --- |
| `201` | `catalyst.preview.v1` | Query is ready for review and acceptance |
| `200` | Non-`ready` `catalyst.query.v1` | Clarification, unsupported, or rejected outcome; never executable |
| `400` | Request validation error | Malformed demo question request |
| `422` | `catalyst.policy.outcome.v1` | Hub returned `ready`, but Catalyst deterministic policy rejected it |
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
| `410` | Execution outcome with `expired` | Preview expired before consumption |
| `502` | Execution outcome with `failed` | Demo analytics execution failed; create a new preview |

### Poll or replay execution

`GET /v1/catalyst/executions/{previewId}?idempotencyKey={key}`

The endpoint returns the same stored `200` table, `202` in-progress outcome, or
non-success outcome associated with that key. An unknown pair returns the
versioned `404` `not_found` outcome. Polling never starts a query.

The current `/v1/chat/completions` endpoint remains a legacy compatibility
surface during migration. It is not the normative query-to-table API.

## Primary workflow: query to table

1. The demo UI sends the user's question to Catalyst.
2. Catalyst resolves the approved analytics catalog, schema version, SQL
   dialect, and data-freshness metadata.
3. Catalyst calls the required med-agent-hub query profile over the local demo
   network with the question and compact approved-view context.
4. The hub performs query planning, SQL generation, and profile-owned review or
   repair, then returns `catalyst.query.v1`.
5. If status is `needs_clarification`, Catalyst returns the clarification
   without SQL. If status is `unsupported` or `rejected`, Catalyst returns a
   stable non-executable outcome with the contract `message`. Only `ready`
   continues.
6. Catalyst independently validates the returned query against deterministic
   policy. A failure returns `catalyst.policy.outcome.v1`; Catalyst does not
   rewrite the hub-owned response.
7. Catalyst stores a preview bound to the query digest, parameters, catalog
   version, expiry, and one-time execution state.
8. The UI presents the preview for explicit acceptance.
9. Catalyst runs the accepted preview against the local demo analytics store
   with read-only credentials, timeout, row, and resource limits.
10. Catalyst returns typed columns, rows, truncation status, freshness, and
    provenance for table rendering.

The hub never receives database credentials and never executes the query.

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

## med-agent-hub profiles

| Profile | Status | Purpose |
| --- | --- | --- |
| `catalyst-query-checked` | Planned; required for query MVP | Return a reviewed `catalyst.query.v1` result for approved semantic-layer views |
| `single-e4b-checked` | Shipped in hub; Catalyst integration planned in R4 | Default fast, checked narrative report |
| `team-med-checked` | Shipped in hub; Catalyst integration planned in R4 | Optional deeper team-based report and later evaluation candidate |

`catalyst-query-checked` is not currently present in med-agent-hub. MVP cannot
be declared complete until the hub advertises this profile through
`GET /v1/models` and its structured contract passes integration tests.

The v1 query contract fixes the profile ID as `catalyst-query-checked`.
Catalyst may configure approved report profile identifiers, but never model
identifiers. A profile may change its internal models or stages under
med-agent-hub's change-control process without requiring Catalyst orchestration
changes.

## Query contract

The normative query-profile request schema is
[`contracts/catalyst-query-request-v1.schema.json`](contracts/catalyst-query-request-v1.schema.json).
It defines the exact request placement for the demo question, approved catalog,
query policy, correlation IDs, and required output-contract identifier. The
hub profile owns construction of any model-backend `response_format`.

The normative wire schema is
[`contracts/catalyst-query-v1.schema.json`](contracts/catalyst-query-v1.schema.json).
The outer blocking response is validated against
[`contracts/catalyst-query-completion-v1.schema.json`](contracts/catalyst-query-completion-v1.schema.json).
It places one JSON serialization of `catalyst.query.v1` in
`choices[0].message.content`. Catalyst does not parse query instructions from
surrounding prose.

The planned `catalyst.query.v1` response contains:

- `contractVersion`
- `deploymentMode`: `demo`
- `status`: `ready`, `needs_clarification`, `unsupported`, or `rejected`
- `question`: exact original request text
- `target` with data source, catalog version, approved view set and SQL dialect
- `sql`, `parameters`, and `expectedColumns` only when status is `ready`
- `clarification` when status is `needs_clarification`
- `message` when status is `unsupported` or `rejected`
- `validation` with profile-owned checks and warnings
- `provenance` with profile ID, trace ID and context source identifiers

The contract contains no database credentials and no query results.

Catalyst requires response `question` to equal the submitted question exactly.
It also requires returned `dataSource`, `catalogVersion`, and `dialect` to equal
the request target, and every `approvedViews` entry to belong to the requested
catalog. Normalized intent belongs in trace metadata, not this execution
contract.

Catalyst rejects:

- an unknown contract version;
- a response that fails the normative JSON Schema;
- an unavailable or unapproved profile;
- SQL that is not a single read-only statement;
- references outside the approved view set;
- a dialect mismatch;
- unbound literals where parameters are required;
- a query exceeding configured complexity, timeout, or row limits.

Hub validation improves quality but does not replace Catalyst's deterministic
execution boundary.

The contract uses dialect-neutral `:name` placeholders. Catalyst checks that
placeholders and typed parameter values match, then the selected execution
adapter translates them to driver-native bindings without string
interpolation. R2 selects one MVP engine/dialect before implementation.

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
  → expired
```

`queryDigest` is SHA-256 over RFC 8785 JSON Canonicalization Scheme bytes for
the stored `{question, target, sql, parameters, expectedColumns}` object.
`target` includes the catalog version. Execution atomically compares that
digest and expiry
before moving from `awaiting_acceptance` to `consuming`.

Only one idempotency key can consume a preview. Repeating the same key while
execution is active returns `in_progress` and permits polling with that key.
Repeating it after completion returns the stored outcome without running the
query again; a different concurrent key conflicts. Failed execution does not
reopen the preview—a new preview is required.
Successful execution returns `catalyst.table.v1`. In-progress, expired,
conflicting, consumed, and failed requests return the versioned non-success
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

The query profile sees only documented, approved semantic-layer views built
over FHIR Data Pipes projections. Initial views should cover a narrow reporting
vocabulary such as:

- laboratory result facts;
- sample and test volumes;
- positivity and categorical result summaries;
- turnaround time;
- pending or validation work queues.

Each view requires:

- a stable name and version;
- documented grain;
- typed columns and units;
- allowed filters and groupings;
- terminology and code-system notes;
- freshness and update behavior;
- example queries;
- confirmation that the view contains demo data only.

FHIR Data Pipes produces per-resource analytics representations. Cross-resource
facts such as turnaround time or order-to-result relationships require a
separate semantic transformation layer; a single FHIR `ViewDefinition` is not
treated as a general cross-resource join mechanism.

Raw FHIR nesting and the transactional OpenELIS schema are not prompt
contracts. Flattening, joins and semantic naming are governed data-platform
responsibilities.

Freshness metadata includes source watermark, pipeline run ID, completion state
and observed lag rather than one ambiguous timestamp.

## Demo safeguards and future security

The MVP is a local demo, not a production clinical system.

Demo safeguards:

1. Use synthetic or approved demo data only.
2. Use med-agent-hub with local LLMs only.
3. Keep database credentials out of model context.
4. Execute through a read-only analytics identity.
5. Permit one parsed SELECT statement over approved views.
6. Require query preview and explicit acceptance.
7. Enforce statement timeout, row limit and resource limit.
8. Keep enough trace metadata to reproduce a demo result.

Future production hardening includes authentication, OpenELIS RBAC, tenant and
facility scope, PHI classification, service authentication, network policy,
encryption, secrets, durable audit, trace access/retention, warehouse
governance, threat modeling and security testing. None of those controls are
claimed by the demo MVP.

## Functional requirements

- **CAT-FR-001:** Accept natural-language laboratory reporting questions.
- **CAT-FR-002:** Discover and call the fixed v1 query profile and configured
  med-agent-hub report profiles.
- **CAT-FR-003:** Supply only approved, versioned analytics context to the query
  profile.
- **CAT-FR-004:** Accept only the versioned structured query contract.
- **CAT-FR-005:** Deterministically validate and safely execute accepted
  read-only queries.
- **CAT-FR-006:** Return typed table results with freshness and provenance.
- **CAT-FR-007:** Require explicit user acceptance through a server-bound,
  expiring query preview before execution.
- **CAT-FR-008:** Reserve a trusted execution boundary for future authorization
  and facility scope.
- **CAT-FR-009:** Optionally generate an evidence-linked report with explicit
  grounding states from a governed table through an approved hub profile.
- **CAT-FR-010:** Relay staged report events without interpreting or
  re-orchestrating hub stages.
- **CAT-FR-011:** Record profile, query, execution, and evidence trace metadata
  sufficient to reproduce demo behavior.
- **CAT-FR-012:** Expose health that distinguishes Catalyst, hub-profile,
  analytics-source, and execution-service readiness.

## MVP acceptance

MVP requires an end-to-end deployment that demonstrates:

1. Required profile discovery from med-agent-hub.
2. At least one natural-language question producing a valid structured query
   against an approved semantic-layer view over Data Pipes projections.
3. Query review and governed read-only execution.
4. Correct typed table output against seeded expected data.
5. Deterministic rejection of disallowed and out-of-scope queries.
6. Expiring preview acceptance and one-time execution.
7. Local med-agent-hub and local model-router execution only.
8. Demo data only.
9. Complete freshness, query, profile, source, and trace provenance.
10. Automated happy-path and failure-path integration tests.

An optional report demonstration does not substitute for the table acceptance
criteria.

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
| Natural language to SQL | Retained; generated through a hub profile |
| SQL review and table results | Retained as the first product MVP |
| Schema RAG/MCP | Reframed as approved analytics catalog/context; retrieval implementation remains pluggable |
| Gemini and LM Studio clients in Catalyst | Reassigned to med-agent-hub and its model router |
| RouterAgent → SchemaAgent → SQLGenAgent | Superseded by hub profile orchestration |
| MCP SQL validation | Retained as a deterministic boundary, independent of hub review |
| OpenELIS Java RBAC/execution | Deferred to production hardening; demo execution remains local and read-only |
| Carbon UI integration | Retained as a future host integration; not required for the first sidecar MVP |
| Base `clinlims` SQL | Replaced as the preferred target by governed semantic-layer views over OHS FHIR Data Pipes projections |
| CloudSafe and LocalPHI security modes | Deferred; the current target is explicitly local demo data with local LLMs |
| Golden-query evaluation | Retained locally for MVP correctness; external harness integration deferred |
| Advanced report storage, scheduling and dashboards | Deferred |

## Deferred Clinical AI Validation Harness goal

After MVP acceptance, Catalyst should integrate with the
[Clinical AI Validation Harness](https://github.com/pmanko/clinical-ai-validation-harness).
The intended real path is:

```text
Harness → Catalyst ↔ med-agent-hub
                  └→ analytics source → table/report
```

The future integration should migrate or deduplicate local golden scenarios,
emit the harness run manifest and event schema, preserve component SHAs and hub
profile IDs, and score query correctness, table correctness, grounding,
abstention, safety, latency, and transport failures.

No harness repository or harness specification change is required for the
local MVP.
