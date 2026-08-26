# med-agent-hub integration boundary

**Status:** Current Hub route and profile contract. The complete-schema and
generic-connection behavior below is the selected Catalyst boundary and is not
yet implemented.

The broader product requirements live in [the Catalyst specification](specification.md)
and [product roadmap](roadmap.md). This document covers only the boundary
between Catalyst and med-agent-hub.

## Purpose

med-agent-hub configures and runs model roles. Catalyst supplies the data-source
context, interprets the model output, presents the query for human review, and
runs the exact SQL the person selects.

Selected boundary:

```text
configured source
  -> Catalyst discovers its complete readable schema
  -> Catalyst assembles the question, dialect, schema, and session context
  -> Hub runs the selected profile's writer and optional reviewer models
  -> Catalyst parses the responses and shows the query and advisory findings
  -> person reviews or edits the SQL
  -> explicit Run executes that exact SQL through the configured connection
```

Hub never connects to the data source and never executes SQL.

## Ownership

### med-agent-hub owns

- query-profile definitions and availability;
- the model assigned to each named role;
- system prompts and prompt references;
- model settings, token limits, and model-router access;
- execution of each named writer or reviewer role; and
- credential-free details describing the profile and role invocation that
  actually ran.

### Catalyst owns

- data-source and model-profile selection;
- source identity, declared SQL dialect, and the complete schema readable
  through the selected connection;
- optional descriptions and relationships that enrich, but never hide, readable
  relations;
- the current instruction, current editor state, and relevant same-session
  context;
- the structured writer and reviewer response formats;
- writer/reviewer sequencing and parsing;
- visible advisory findings and model or parsing failures;
- query versions and their connection to the source, profile, and conversation;
- the one editable SQL control; and
- execution of the exact user-selected SQL, including typed parameters, row and
  time limits, typed results, and database errors.

The unchanged machine contracts may retain older field names for the readable
schema. Those names do not authorize Catalyst or Hub to filter, rank, or hide a
relation that the configured connection can read.

## Live HTTP surface

The current Catalyst integration is implemented by
[`local_hub.py`](../catalyst-gateway/src/catalyst/local_hub.py) and
[`query_engine.py`](../catalyst-gateway/src/catalyst/query_engine.py).

Catalyst calls these med-agent-hub routes:

| Method | Route | Use |
| --- | --- | --- |
| `GET` | `/health` | Check whether Hub responds. |
| `GET` | `/v1/hub/query-profiles` | Discover configured profiles, roles, models, settings, and live availability. |
| `POST` | `/v1/hub/query-profiles/{profileId}/roles/{role}/generate` | Run one named role from one selected profile. |

Catalyst exposes the available choices to its UI through
`GET /v1/catalyst/query-options`, defined in
[`routes.py`](../catalyst-gateway/src/catalyst/routes.py). There is no public
Catalyst route that lets the caller override a profile's role model, prompt, or
model settings.

## Profile discovery and selection

`GET /v1/hub/query-profiles` is the source for profile names, role mappings,
settings, and live model availability. Catalyst lists only profiles that Hub
marks available.

The person selects a profile explicitly. Catalyst binds that selection to the
query turn and uses the exact same profile for its role calls. If the profile is
missing, unavailable, or changes incompatibly before generation completes, the
turn fails visibly. Catalyst does not substitute another profile, role model,
provider, or direct model-router call.

A profile must include a `query_generate` role. It may also include a
`query_review` role. Catalyst runs only the roles declared by that selected
profile.

## Context sent for generation

Catalyst assembles the content for each role call. It includes:

- the current question or follow-up instruction;
- selected source identity and declared SQL dialect;
- every readable relation, column, and type discovered through that source's
  connection;
- applicable optional descriptions and relationships;
- the required structured response format;
- the current query and typed parameters for a follow-up;
- relevant prior instructions and a relevant database failure when they help
  interpret the follow-up; and
- request and trace identifiers needed to connect the response to its turn.

Catalyst does not silently reduce the readable schema to a hand-picked subset.
If a selected model cannot accept the required request, generation fails with a
clear context error rather than sending a different schema.

Catalyst never sends Hub:

- source credentials or connection strings;
- query result rows;
- credentials for any model provider; or
- content from another source or session.

## Named role call

The role endpoint receives caller messages and the requested structured response
format:

```json
{
  "messages": [
    {"role": "user", "content": "...Catalyst-assembled context..."}
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {"name": "...", "schema": {}}
  }
}
```

The caller does not submit the model or its settings. Hub adds the selected
role's system prompt, applies the profile's configuration, calls its model
router, and returns the assistant content with the profile, role, and model that
ran. Catalyst checks that those details match the selected profile.

## Response handling

Catalyst parses writer and reviewer content. A writer may produce:

- a query candidate ready for human review;
- one clarification question with no SQL; or
- a concise explanation that the request is unsupported, with no SQL.

A malformed response or a response tied to another profile is an explicit
generation failure. It is not rewritten into a successful query.

When the selected profile includes a reviewer, Catalyst supplies the candidate
to that role and parses its response before presenting the final candidate.
Profile review helps improve the candidate; it does not replace the person's
review.

Parser, reviewer, and SQL checks may produce visible findings. Those findings
are advice. They do not disable Run, rewrite the editor, or prevent a
user-selected query from reaching the configured connection. A syntactically or
semantically wrong query may therefore return a normal database error, which is
useful feedback rather than an invalid turn.

Clarification and unsupported responses execute no SQL and leave the previous
selected query intact.

## Query record

For each turn, Catalyst records enough information to explain what produced the
visible query:

- source identity, dialect, and readable-schema version;
- selected profile and declared role models;
- prompt references and effective model settings supplied by Hub;
- request, turn, and trace identifiers;
- the writer and optional reviewer outcomes;
- the resulting query version and its parent editor state; and
- any later execution and database outcome.

This record belongs to Catalyst operating state. It is not clinical data and is
not a reason to send result rows back to Hub.

## Failure behavior

- Unreachable or malformed profile discovery makes profile selection
  unavailable and returns an explicit error.
- A selected profile that is no longer available fails that turn without a
  fallback.
- A failed writer or reviewer call remains a model-generation failure; Catalyst
  does not label it clarification, unsupported data, or a database error.
- An invalid response remains visible as a response or parsing failure and does
  not create a runnable generated version.
- The current editor and earlier successful result remain available after a
  generation failure.
- Database errors occur only after explicit Run and are recorded as execution
  outcomes, not Hub failures.

## Configuration

The current integration reads:

- `MED_AGENT_HUB_BASE_URL` for Hub health and profile discovery;
- `CATALYST_HUB_QUERY_PROFILE_URL` as the base used for named role calls; and
- `CATALYST_HUB_TIMEOUT_SECONDS` for the named role-call timeout.

Hub and its model router own provider credentials and physical model setup.
Catalyst configuration must not duplicate those settings.

## Acceptance

The integration is acceptable when one real browser flow shows that:

- the UI options match live Hub profile discovery;
- the selected profile's writer and optional reviewer roles use their
  Hub-configured models, prompts, and settings;
- each role receives the selected source, declared dialect, and complete
  readable schema, with no credentials or result rows;
- a query candidate appears in the single editor with any findings shown as
  advice;
- clarification and unsupported responses run no SQL;
- a Hub or profile failure is explicit and does not trigger fallback;
- the person can edit the candidate and Run sends the exact visible SQL through
  the configured source connection; and
- Catalyst retains the resulting query version and either typed rows or the
  database error after refresh.

This check proves the integration boundary without adding another database path
or automatic scoring.

## Keep the boundary small

- Do not move source discovery, SQL execution, query versions, or UI findings
  into Hub.
- Do not let Hub configuration narrow the readable source schema.
- Do not add a second profile-selection or direct-router path in Catalyst.
- Do not add engine-specific SQL assumptions to this integration contract.
- Do not turn advisory findings into a gate on the person's explicit Run.
