# Catalyst product specification

**Status:** Current product contract. The query notebook and binding Dashboard
Builder design are accepted; the generic connection, Spark reference deployment,
and final Dashboard acceptance remain open.

## Purpose

Catalyst helps a person:

- ask a data question;
- inspect and edit the generated SQL;
- run the exact selected query against a configured source;
- inspect bounded rows or the database error;
- refine the query in conversation;
- save a successful result as a Dataset;
- create Widgets and a Dashboard; and
- publish a deterministic native bundle for Superset.

Catalyst is a generic SQL-connected application. It does not own ingestion, a
clinical warehouse, or a preferred database engine.

## Product boundary

A source supplies:

- a stable identity and label;
- connection configuration or a connection reference;
- an explicit SQL dialect;
- independent availability; and
- every table, view, column, and type readable through that connection.

Optional source annotations may add descriptions, relationships, units, or
examples. They cannot hide, approve, or rank readable relations.

A session binds one source at creation. Selecting another source starts another
session. An unavailable source does not prevent application startup or another
source from being used.

med-agent-hub owns configured model profiles, prompts, role-to-model mapping, and
model settings. Catalyst owns request context, conversation and query versions,
advisory validation, connection execution, results, and Dashboard Builder state.

Superset owns rendering. Catalyst owns deterministic bundle generation and
publication status based on explicit importer receipts.

## Architecture

```text
person
  -> Catalyst UI
  -> Catalyst Gateway
       -> med-agent-hub for configured model roles
       -> configured SQL connection for schema and exact execution
       -> SQLite for Catalyst operating metadata
       -> outbox for Superset bundles
  -> Superset using the same configured data source
```

Generated and manually edited queries use shared connection-execution code.
Catalyst does not translate SQL between engines.

## Query workbench

### Session creation

The person selects one available source and one available model profile, asks a
question, and starts a session. The model request receives:

- the current instruction;
- source identity and declared SQL dialect;
- the complete readable schema;
- applicable optional descriptions;
- the configured writer/checker profile; and
- relevant same-session context.

The application records what was actually sent and any omission with its reason.
It does not silently summarize, rank, or substitute context. Result rows never
enter model context.

### Writer responses

The writer returns one of:

- `ready`: a query candidate;
- `needs_clarification`: one question and no SQL;
- `unsupported`: a concise explanation and no SQL.

Contract or orchestration failure remains a failure. Clarification and
unsupported turns execute no SQL and preserve the previous selected query.

### Editor and Run

The latest turn contains exactly one editable SQL control. It supports:

- highlighting, formatting, and keyword/function completion for the declared
  dialect;
- relation and column completion from the same readable schema supplied to the
  model;
- typed parameters;
- visible model output and failure evidence;
- advisory findings;
- immutable query versions;
- Clear and Restore; and
- explicit Run.

Formatting and validation never execute SQL. Findings never disable Run or
rewrite SQL.

Run saves the exact visible draft as an immutable version and submits its exact
SQL and typed parameters through the configured connection. Catalyst applies a
time limit and returned-row limit. The connection or reference deployment
prevents mutation of source data.

Success retains typed columns, bounded rows, counts, source, dialect, readable
schema reference, query identity, and timing. Failure retains the error returned
by the database. A bad query or database error remains a valid observable result.

### Conversation and state

A follow-up uses the current visible editor state, prior user instructions,
relevant failure information, and eligible verified examples from the same
source and session. Earlier material cannot replace the current instruction.

Earlier turns become readable summaries. Only the latest turn owns the editor.
A result remains inspectable but is marked stale when the visible query changes.
Refresh restores the session, selected query, findings, executions, result
state, and saved Dashboard Builder objects. New session is the only action that
clears the active thread.

## Available data

The compact Available data disclosure and full browser show every readable
relation and column. The full browser remains searchable, filterable, and
paginated and has clear empty, loading, unavailable, and error states.

Refreshing schema discovery reflects current connection access. A changed
schema is visible and recorded but does not by itself prevent application
startup or discard saved evidence.

## Dashboard Builder

The binding interaction and visual contract is
[dashboard-builder-mvp-design.md](dashboard-builder-mvp-design.md) and its
populated binding 4c page.

A Dashboard Builder Dataset is an immutable saved query and execution artifact.
It is not a source, warehouse, or restricted schema copy.

### Dataset

- Only a successful execution for the exact current query may create or refresh
  a Dataset draft.
- The Dataset review panel owns the full bounded typed table.
- Save is immutable and idempotent for identical content.
- Each Dataset retains source, dialect, readable-schema reference, SQL,
  parameters, execution identity, typed shape, warnings, and recorded
  configuration.

### Widget

- Compatibility and the initial visualization suggestion are deterministic from
  the Dataset's typed shape.
- The person reviews the suggestion and may choose another compatible type.
- A saved Widget is immutable and retains its Dataset identity and bindings.
- The accepted visualization families are table, key value, time series,
  grouped or stacked bar, and proportion bar.

### Dashboard

- A Dashboard arranges multiple saved Widgets from one source.
- Each Dataset keeps its own readable-schema reference; a harmless later schema
  refresh does not block same-source composition.
- A saved Dashboard is immutable and keeps stable logical identity across
  versions.

### Publication and import

- Publish writes a deterministic native Superset bundle to the outbox and offers
  the same bytes for download.
- The bundle contains configuration and recorded identities, not result rows.
- Status follows explicit importer receipts. File existence alone is not
  imported.
- Failures remain actionable and do not expose false success or Open controls.
- The stable Dashboard URL opens only after successful import.
- Superset renders the saved queries against the configured source.
- Acceptance inspects one displayed value against the originating Catalyst
  result without a second database query.

Superset application programming interface publication, embedded viewing,
bidirectional synchronization, sharing, scheduling, automatic refresh, and
model-generated chart specifications are later work.

## Selected reference deployment

For each source included in the selected demonstration or comparison:

```text
FHIR source
  -> pinned FHIR Data Pipes
  -> Parquet and applicable ViewDefinitions
  -> Spark SQL
  -> Catalyst and Superset
```

The ingestion configuration, ViewDefinitions, Parquet, Spark service, and
optional source descriptions belong to the reference deployment, not Catalyst
core. OpenELIS assets are packaged in `analytics/` for convenience.

Each included source receives one live end-to-end proof when integrated:

- nonempty Parquet and applicable ViewDefinitions;
- one manual Spark query proving the endpoint and a known fact;
- the same readable tables discovered by Catalyst;
- one successful exact query and one database error in the browser;
- one Dataset-to-Superset render; and
- proof that the chosen Spark query path cannot mutate source data.

The manual Spark query is a one-time materialization check. It does not become a
second harness or per-run comparison path.

## Program acceptance

### Phase 1 regression smoke

The Phase 1 connection checkpoint includes one saved-query-to-Superset path. This
smoke protects integration but does not complete Dashboard Builder.

### Phase 3 Dashboard Builder

Final acceptance compares the live Workbench, Dataset review/library, Widget
review/library, Dashboard library/arrangement, and all publish/import states
side by side with the binding design. It confirms profile selection, generation
and failure evidence, Clear/Restore, complete Available data browsing, fixed
composer/thread, one editor, review panels, multiple Widgets, and actionable
publication states. The owner performs the final browser review.

## Accessibility

All interactive controls remain keyboard operable with logical order, visible
focus, usable announcements, Escape and focus return for overlays, reduced
motion, and the accepted desktop and narrow-layout behavior.

## Out of scope

- an application relation allowlist, fixed relation count, or relation ranking;
- a connector framework or SQL translation;
- a shadow analytics warehouse or automatic database fallback;
- result rows in model context;
- automatic query execution;
- a second database path for acceptance;
- production authentication, authorization, row-level access, or sensitive-data
  controls for the demo stage;
- reseed, restart-persistence, environment-parity, repeated-model-run, or
  exhaustive infrastructure-failure gates; and
- automatic scoring, ranking, or model-team selection.
