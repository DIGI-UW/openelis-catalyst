# med-agent-hub Integration Contract

**Status:** Hub-configured Catalyst query roles implemented locally; report profiles future
**Product specification:** [`specification.md`](specification.md)  
**Roadmap:** [`roadmap.md`](roadmap.md)

## Purpose

For governed query generation, Catalyst Gateway owns context assembly,
structured-output formats, writer/reviewer orchestration, deterministic
lint/correction, execution, and query lineage. [med-agent-hub](https://github.com/pmanko/med-agent-hub)
owns the shared profile entry containing role-to-model mapping, role knobs, and
prompt references. Gateway invokes each role by profile ID and cannot override
those Hub-owned settings.

Planned narrative reports may still use Hub-owned product profiles. This
document separates the implemented configured query-role contract from that
future report contract and defines what remains outside Hub's trust boundary.

## Runtime topology

```text
Catalyst
  → Catalyst governed-query engine
      → med-agent-hub POST /v1/hub/query-profiles/{profile}/roles/{role}/generate
      → local OpenAI-compatible model router
          → Hub-profile-selected local role model
      → Catalyst deterministic lint / optional review / re-lint
```

For query generation, Catalyst sends a user question and approved analytics
catalog context inside role-specific messages. Hub executes one model call and
returns assistant content verbatim. Gateway parses and validates that content,
coordinates any correction/reviewer call, finalizes the structured query
contract, and executes the query outside Hub.

For report generation, Catalyst sends governed table evidence. The hub returns
staged report output with explicit validation and grounding states.

## API surface

Catalyst's implemented query path depends on:

- `GET /health`
- `GET /v1/hub/query-profiles`
- `POST /v1/hub/query-profiles/{profile}/roles/{role}/generate`

The configured role endpoint accepts a non-system message list and optional
`response_format`. Hub resolves the model, system prompt, temperature,
`dry_multiplier`, and token cap from the selected profile. It returns:

```json
{"profile_id": "catalyst-query-e4b-qwen14b", "role": "query_generate", "model": "google/gemma-4-e4b", "content": "{...assistant content...}"}
```

Hub owns profile compilation and discovery, provider/auth/timeout abstraction,
serialized model-router connection, configured system-prompt injection, and
structured-output pass-through. It does not interpret the Catalyst catalog,
select a profile on the user's behalf, run query lint, or compose query roles.
Catalyst does not call model-router endpoints directly.

The `data` entries from `GET /v1/models` and
`POST /v1/chat/completions` remain Hub product-profile surfaces for planned
report integration and legacy consumers. Catalyst query generation does not
depend on those clinical profiles. Query-profile discovery carries its own
versioned backend inventory and exact credential-free profile evidence.
Low-level
dynamic report legs such as `answer:...`, `answer-review:...`, and
`indepth-only:...` are experimentation interfaces, not stable Catalyst
dependencies.

## Hub query-profile policy

| Hub profile | Role mapping | Use |
| --- | --- | --- |
| `catalyst-query-e4b-qwen14b` | `google/gemma-4-e4b` writer and `qwen2.5-14b-instruct-mlx` reviewer | External cross-family manual-testing lane |

Gateway relays available choices through `GET /v1/catalyst/query-options`. The
profile ID is selected per turn and bound into versioned
profile/model/prompt/configuration evidence. Query profiles use temperature zero
and disable the router's DRY repetition penalty. Profile evidence records
`dry: 0`; invocation evidence records `dryMultiplier: 0` alongside the response
format and token cap.

An unknown Hub profile fails closed; Catalyst never silently substitutes a
different profile or model. All listed profiles accept contextual revisions;
revision capability is independent of reviewer presence. A writer-only profile
has no reviewer invocation or reviewer evidence. A reviewed profile runs writer
→ deterministic lint → reviewer → deterministic re-lint before finalization.

The query-options registry derives live availability from Hub query-profile
discovery. A profile is available only when the backend inventory
advertises every exact writer/reviewer alias it requires. Inventory failure
fails closed; missing aliases are reported per model, unavailable profiles are
omitted by the UI, and selection is rejected before model invocation or session
mutation. Availability is a discovery snapshot, so Catalyst still records a
truthful generation/backend failure if the backend changes before invocation.

The umbrella harness builds its pinned sibling Hub checkout; standalone
Catalyst clones the same unmodified Hub commit as a fallback.

## Governed-query engine

Hub's `workflow: catalyst_query` profiles configure model roles for the
query-to-table engine. Existing `workflow: clinical_answer` profiles run a
different hosted clinical workflow and do not satisfy the structured query
contract.

Gateway owns:

- context selection;
- query planning;
- SQL generation;
- generation-time schema grounding;
- review and repair loops;
- query and invocation evidence;
- deterministic query policy and profile evaluation.

Hub owns the shared profile schema, role-to-model mapping, prompts, sampling
knobs, backend availability, and execution of each named role.

The engine must:

1. Expose its configured profiles through
   `GET /v1/catalyst/query-options`.
2. Accept an explicit analytics target, SQL dialect and versioned approved-view
   catalog.
3. Advertise and return only the versioned `catalyst.query.v1` structured
   contract.
4. Distinguish unsupported and ambiguous questions from generated queries.
5. Preserve the catalog and context source identifiers used.
6. Return deterministic validation findings and reviewer warnings.
7. Record exact profile, prompt, model, configuration, invocation, and
   correlation evidence.
8. Never send database credentials or result rows to Hub.

Model review is a generation-quality control. Gateway still parses and
validates every candidate before execution.

## Query request

Initial requests use
[`contracts/catalyst-query-request-v1.schema.json`](contracts/catalyst-query-request-v1.schema.json).
Revisions use
[`contracts/catalyst-query-request-v2.schema.json`](contracts/catalyst-query-request-v2.schema.json)
and the linked revision-context/editor-snapshot/turn contracts. They define the
complete Gateway engine request:

- `model`: the selected Hub query profile ID;
- `stream`: `false` for the first query-contract version;
- `messages[0].content`: the current initial or follow-up instruction;
- `catalystQuery`: analytics target, compact runtime catalog, non-secret query
  policy, correlation IDs, and `requiredOutputContract: catalyst.query.v1`;
- for v2, the exact active editor SQL/parameters/digest, current stored version
  and digest, initial instruction plus at most five prior follow-ups, and only
  exact-base validation/execution summaries.

The demo request contains no production actor, facility, tenant, or
authorization context.

Gateway does not send this whole request to Hub. It converts the governed
context into role-specific user messages and sends one configured-role request
per invocation:

```json
{
  "messages": [
    {"role": "user", "content": "..."}
  ],
  "response_format": {"type": "json_schema", "json_schema": {"name": "...", "schema": {}}}
}
```

The governed engine context and role requests do not contain:

- database credentials;
- unrestricted database schema;
- arbitrary production table DDL;
- query results;
- patient/result rows;
- every historical SQL copy, unrelated sessions or raw hidden traces;
- instructions that weaken profile policy.

The MVP uses demo questions and the local model router. Production PHI
classification and provider-routing policy are deferred.

## `catalyst.query.v1`

The normative finalized-query schema is
[`contracts/catalyst-query-v1.schema.json`](contracts/catalyst-query-v1.schema.json).
The earlier remote-profile completion envelope remains documented by
[`contracts/catalyst-query-completion-v1.schema.json`](contracts/catalyst-query-completion-v1.schema.json).
The implemented Gateway engine instead parses each configured Hub role response against
its role-specific structured-output schema and builds `catalyst.query.v1`
in-process.

The finalized query object has:

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
| `validation` | Gateway deterministic checks plus reviewer findings |
| `provenance` | Profile ID, trace ID and context source IDs |

Catalyst rejects prose masquerading as the contract, a missing version, unknown
fields that change execution meaning, or a `ready` response without complete
target and provenance metadata.

Catalyst also rejects a question mismatch, target data source/catalog/dialect
mismatch, or an approved view that was not present in the request catalog.
Normalized intent may appear in Gateway trace metadata but cannot replace the
execution contract question.

Named SQL placeholders use `:name` when the candidate uses parameters. Literal
read-only SQL remains valid; named parameters are a recommendation for longer
queries, not a mandatory generation gate.

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

Catalyst maps Hub transport/profile failures and governed-query terminal
outcomes into stable categories:

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

Gateway performs bounded model correction only when it has structured
deterministic findings. Transport retries are limited to requests known to be
safe to replay; Catalyst must not retry an ambiguous completion after receiving
response bytes unless the Hub contract supplies an idempotency mechanism.

Report failure never changes the status of a successfully executed table.

## Configuration

Implemented query-path integration settings are:

- `MED_AGENT_HUB_BASE_URL`
- `CATALYST_HUB_TIMEOUT_SECONDS`;
- `CATALYST_HUB_QUERY_PROFILE_URL` only when discovery is not at the standard
  Hub path.

Query profiles and prompt IDs are versioned Hub source configuration in the
same shared catalog as clinical profiles. Planned report integration adds:

- `CATALYST_REPORT_PROFILE`
- `CATALYST_DEEP_REPORT_PROFILE`
- connection, first-byte, idle-stream and total timeouts;
- report and deep-report enable flags.

The demo configuration points to the local hub and local model router.
Production service authentication, endpoint identity, egress policy and trace
governance are deferred to the roadmap's production-security milestone.

The following belong to med-agent-hub or its model router, not Catalyst:

- `LLM_BASE_URL`
- provider credentials and transport authentication;
- model files and the router's served-model inventory;
- router concurrency and backend timeouts.

Catalyst owns governed-query stage ordering, review, deterministic query policy,
execution, and query trace evidence. Hub owns role-to-model mapping, prompts,
temperature/DRY/token knobs, and physical model execution. Hub-hosted clinical
profiles retain their temporal, grounding, and drug-safety workflow adapter.

Legacy `CATALYST_LLM_PROVIDER`, `LMSTUDIO_BASE_URL`, `GOOGLE_API_KEY`, and
`GEMINI_MODEL` settings apply only to the legacy Catalyst-local compatibility
path, not the governed-query MVP.

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
3. Availability of at least one configured Hub query profile whose exact
   role-model IDs are advertised by the router.
4. Hub-owned model-router catalog reachability.
5. Enabled report-profile availability when R4 is implemented.
6. Analytics catalog and source freshness.
7. Query execution service readiness.

Logs and traces correlate:

- Catalyst request ID;
- Catalyst trace ID;
- profile ID;
- writer/reviewer invocation IDs, roles, models, configurations, durations,
  request/response digests, and outcomes;
- catalog/view versions;
- query validation decision;
- execution trace ID.

Ordinary logs should avoid unnecessary result rows even in demo mode.

## Contract testing

Before enabling a Hub Catalyst-query profile in a supported deployment, tests cover:

- Hub profile discovery and exact profile evidence;
- writer-only and reviewed role sequences;
- unknown profiles and unserved-model failures;
- named-role request serialization and structured-output pass-through;
- exact `catalyst.query.v1` finalization;
- malformed and incompatible responses;
- timeout, cancellation and transport failure;
- oversized context;
- trace correlation;
- deterministic writer lint/correction and reviewer-correction re-lint;
- Hub and real external model-router configuration;
- proof that database credentials and result rows are absent from role
  requests.

Planned report-profile tests separately cover product-profile discovery and SSE
heartbeats, ordering, terminal events, and grounding states. Live query
integration tests must use the real configured-role Hub API; mock-only engine tests are
not sufficient for milestone sign-off.
