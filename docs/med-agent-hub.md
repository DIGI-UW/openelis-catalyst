# med-agent-hub Client Contract

**Status:** Query-profile integration implemented locally; report profiles future
**Product specification:** [`specification.md`](specification.md)  
**Roadmap:** [`roadmap.md`](roadmap.md)

## Purpose

Catalyst delegates all LLM and model-team behavior to
[med-agent-hub](https://github.com/pmanko/med-agent-hub). Catalyst is a client;
it does not reproduce the hub's profile compiler, stage engine, model router,
prompts, context selector, review logic, grounding, or trace package.

This document defines what Catalyst needs from the hub and what remains outside
the hub's trust boundary.

## Runtime topology

```text
Catalyst
  → med-agent-hub :8080
      → local OpenAI-compatible model router
          → profile-selected local models
```

For query generation, Catalyst sends a user question and approved analytics
catalog context. The hub returns a structured query contract. Catalyst then
validates and executes the query outside the hub.

For report generation, Catalyst sends governed table evidence. The hub returns
staged report output with explicit validation and grounding states.

## API surface

Catalyst depends on the hub's product API:

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`

Catalyst does not call low-level model-router endpoints directly.

Low-level dynamic legs such as `answer:...`, `answer-review:...`, and
`indepth-only:...` are experimentation interfaces. They are not stable Catalyst
product dependencies.

## Profile policy

| Configuration | Default | Availability |
| --- | --- | --- |
| Query profile | `catalyst-query-checked` | Shipped in the pinned Hub commit |
| Fast report profile | `single-e4b-checked` | Shipped in hub; Catalyst integration planned in R4 |
| Deep report profile | `team-med-checked` | Shipped in hub; Catalyst integration planned in R4 |

The v1 query profile ID is fixed as `catalyst-query-checked` because the
normative contracts bind request and provenance to it. Report profile IDs are
deployment configuration. Model IDs are not Catalyst configuration.

At startup and during readiness checks, Catalyst must use `GET /v1/models` to
verify that every enabled product profile:

- is advertised;
- is available;
- exposes the required capability;
- satisfies exact-context requirements where applicable;
- advertises the required output contract version.

An unknown, hidden, experimental, or unavailable profile fails closed.
Catalyst must not silently substitute a different profile.

The pinned Hub commit advertises `catalyst.query.v1` and contains the strict
request/response handling and checked query stages directly. The umbrella
harness builds its sibling Hub checkout; standalone Catalyst clones that same
unmodified commit as a fallback.

## Checked query profile

`catalyst-query-checked` is a new med-agent-hub product profile required by the
query-to-table MVP. Existing answer profiles generate clinical prose and do not
meet the structured query requirement.

The hub owns this profile's:

- model roles and routing;
- context selection;
- prompts;
- query planning;
- SQL generation;
- generation-time schema grounding;
- review and repair loops;
- sampling settings;
- trace events;
- profile evaluation and change control.

The profile must:

1. Be advertised by `GET /v1/models`.
2. Accept an explicit analytics target, SQL dialect and versioned approved-view
   catalog.
3. Advertise and return only the versioned `catalyst.query.v1` structured
   contract.
4. Distinguish unsupported and ambiguous questions from generated queries.
5. Preserve the catalog and context source identifiers used.
6. Return profile validation findings and warnings.
7. Emit a hub trace ID.
8. Never execute SQL or require database credentials.

Hub review is a generation-quality control. Catalyst still parses and validates
the final SQL before execution.

## Query request

The normative request schema is
[`contracts/catalyst-query-request-v1.schema.json`](contracts/catalyst-query-request-v1.schema.json).
It defines the complete OpenAI-compatible request envelope:

- `model`: fixed v1 query profile ID `catalyst-query-checked`;
- `stream`: `false` for the first query-contract version;
- `messages[0].content`: the demo user question;
- `catalystQuery`: analytics target, compact approved catalog, non-secret query
  policy, correlation IDs, and `requiredOutputContract: catalyst.query.v1`.

The demo request contains no production actor, facility, tenant, or
authorization context.

The patched hub implements the `catalystQuery` extension and owns conversion of
`requiredOutputContract` into the model-backend structured-output mechanism;
Catalyst does not configure profile internals.

The MVP request contains demo questions and uses the local model router.
Production PHI classification and provider-routing policy are deferred.

The request does not contain:

- database credentials;
- unrestricted database schema;
- arbitrary production table DDL;
- query results;
- patient/result rows;
- model or sampling overrides;
- instructions that weaken profile policy.

## `catalyst.query.v1`

The normative wire schema is
[`contracts/catalyst-query-v1.schema.json`](contracts/catalyst-query-v1.schema.json).
The outer blocking HTTP response is validated against
[`contracts/catalyst-query-completion-v1.schema.json`](contracts/catalyst-query-completion-v1.schema.json).
`choices[0].message.content` contains exactly one JSON serialization of
`catalyst.query.v1`, with no Markdown fence or surrounding prose.

The profile response object has:

| Field | Purpose |
| --- | --- |
| `contractVersion` | Must equal `catalyst.query.v1` |
| `deploymentMode` | Must equal `demo` |
| `status` | `ready`, `needs_clarification`, `unsupported`, or `rejected` |
| `question` | Exact original request text |
| `target` | Source, catalog version, approved views and SQL dialect |
| `sql` | One candidate read-only statement when status is `ready` |
| `parameters` | Typed values bound outside the SQL text |
| `expectedColumns` | Expected output names and types |
| `clarification` | User-facing clarification when required |
| `message` | User-facing reason when status is `unsupported` or `rejected` |
| `validation` | Hub-owned generation checks and warnings |
| `provenance` | Profile ID, trace ID and context source IDs |

Catalyst rejects prose masquerading as the contract, a missing version, unknown
fields that change execution meaning, or a `ready` response without complete
target and provenance metadata.

Catalyst also rejects a question mismatch, target data source/catalog/dialect
mismatch, or an approved view that was not present in the request catalog.
Normalized intent may appear in hub trace metadata but cannot replace the
execution contract question.

Named SQL placeholders use `:name`. Parameters extracted from the question
carry their typed JSON value.

The colon syntax is the Catalyst contract grammar, not a claim that every
warehouse driver accepts it directly. The execution adapter for the selected
MVP dialect translates placeholders to native bindings without string
interpolation.

After JSON Schema validation, Catalyst performs runtime invariants that JSON
Schema cannot fully express: parameter names are unique, every SQL placeholder
has exactly one parameter, no extra parameter exists, and tagged parameter
types match the selected SQL dialect.

## Report profiles

Report generation uses demo result rows and the local demo hub/model router.

### `single-e4b-checked`

This is the planned default Catalyst report profile because it is an
advertised, checked hub product path optimized for a fast answer followed by
validation and in-depth output. It owns deterministic temporal checks,
reference resolution, review, final grounding and trace production.

### `team-med-checked`

This is an optional deeper profile. It adds hub-owned team gathering and
specialist model roles. Catalyst treats it exactly like any other product
profile and does not orchestrate those roles.

A deployment may disable deep reports if local hardware cannot serve this
profile. It must not silently map the deep-report choice to an unvalidated
profile.

Catalyst must not claim that every report is grounded unless the selected
profile advertises a versioned fail-closed grounding capability. Current
`validation: true` metadata alone does not guarantee that unchecked grounding
cannot ship.

## Report evidence

Catalyst sends only evidence created from an already accepted query result.
Every evidence record requires:

- stable source ID;
- source view and view version;
- row or aggregate identity;
- effective date when applicable;
- compact display text;
- typed values and units;
- filters and reporting scope;
- source-data freshness;
- query and execution correlation IDs.

The initial implementation is restricted to bounded tables serialized as
numbered inline records compatible with the hub evidence ledger. A dedicated
Catalyst analytics `ContextSource` is a future hub dependency for larger or
richer tables because patient-chart serialization does not fully represent
aggregate reporting provenance.

Oversized or mandatory evidence must fail with a clear context error rather
than silently drop required rows.

## Streaming report events

Report requests use `stream: true`. Catalyst relays the hub's staged SSE
semantics:

| Event | Catalyst behavior |
| --- | --- |
| `answer_done` | Display a provisional fast answer with resolved references and current validation state; do not label it finally grounded |
| `answer_validation` | Apply the provisional reviewed replacement while preserving the original in trace metadata |
| `indepth_pending` | Mark the in-depth section as running |
| `indepth_done` | Display in-depth output with its current gate metadata |
| `indepth_error` | Preserve the table and answer; show the section failure |
| `done` | Finalize the response; mark output validated only when a versioned fail-closed grounding verdict is present |
| `error` | Surface structured source/code/message without inventing partial success |
| heartbeat comment | Keep the connection alive; do not expose as report content |

Catalyst must preserve event order and must not reinterpret hub validation
verdicts.

## Error behavior

Catalyst maps hub failures into stable categories:

- `hub_unavailable`
- `profile_unavailable`
- `profile_incompatible`
- `hub_timeout`
- `hub_cancelled`
- `hub_invalid_response`
- `insufficient_context`
- `query_needs_clarification`
- `query_unsupported`
- `query_rejected`

Transport retries are limited to requests known to be safe to replay. Catalyst
must not retry an ambiguous completion after receiving response bytes unless
the hub contract supplies an idempotency mechanism.

Report failure never changes the status of a successfully executed table.

## Configuration

MVP Catalyst configuration contains only integration policy:

- `MED_AGENT_HUB_BASE_URL`
- `CATALYST_REPORT_PROFILE`
- `CATALYST_DEEP_REPORT_PROFILE`
- connection, first-byte, idle-stream and total timeouts;
- report and deep-report enable flags.

The demo configuration points to the local hub and local model router.
Production service authentication, endpoint identity, egress policy and trace
governance are deferred to the roadmap's production-security milestone.

The following belong to med-agent-hub or its model router, not Catalyst:

- `LLM_BASE_URL`
- model file or model ID selection;
- role-to-model mapping;
- prompt selection;
- temperature and sampling;
- context window and reserved output tokens;
- stage ordering;
- review, temporal, grounding and drug-safety policies.

Legacy `CATALYST_LLM_PROVIDER`, `LMSTUDIO_BASE_URL`, `GOOGLE_API_KEY`, and
`GEMINI_MODEL` settings remain relevant only until the current implementation
is migrated off Catalyst-local inference.

## Future production boundary

The demo contract does not claim production security. Before real clinical data
is enabled, the integration requires service authentication, trusted endpoint
identity, PHI/provider-routing policy, restricted egress, protected traces,
retention/deletion policy, authorization scope, audit and security testing.
Those controls belong to the future production-security milestone.

## Readiness and observability

Catalyst reports separate states for:

1. Catalyst process readiness.
2. Hub health.
3. Required query-profile availability and compatibility.
4. Enabled report-profile availability.
5. Model-router availability exposed by profile discovery.
6. Analytics catalog and source freshness.
7. Query execution service readiness.

Logs and traces correlate:

- Catalyst request ID;
- Catalyst trace ID;
- hub trace ID;
- profile ID;
- catalog/view versions;
- query validation decision;
- execution trace ID.

Ordinary logs should avoid unnecessary result rows even in demo mode.

## Contract testing

Before enabling a profile in a supported deployment, tests cover:

- discovery and capability parsing;
- advertised `catalyst.query.v1` compatibility;
- unavailable and unknown profiles;
- request serialization;
- exact structured query response;
- malformed and incompatible responses;
- timeout, cancellation and transport failure;
- SSE heartbeats, ordering and terminal events;
- oversized context;
- trace correlation;
- prevention of provider/model/stage overrides;
- local hub and local model-router configuration;
- proof that database credentials and result rows are absent from query-profile
  requests.

Live integration tests must use the real hub API. Mock-only client tests are
not sufficient for milestone sign-off.
