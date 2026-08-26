# Catalyst Dashboard Builder contract

**Status:** Current human-readable contract for the implemented HTTP surface and
the accepted Dashboard Builder experience.

The product requirements live in [the Catalyst specification](../specification.md).
The interaction, visual, responsive, and accessibility requirements live in
[the accepted Dashboard Builder design](../dashboard-builder-mvp-design.md).
This file explains the smaller HTTP boundary that supports that experience. It
does not replace either authority or invent routes that the application does not
serve.

Base path: `/v1/catalyst/dashboard-builder`

## Product boundary

Catalyst turns one successful Workbench execution into a saved Dataset, a
reviewed Widget, and a Dashboard that Superset can render:

```text
exact successful Workbench execution
  -> Dataset
  -> Widget
  -> Dashboard
  -> native Superset bundle
  -> explicit import result
```

The following rules apply across the whole path:

- A session uses one configured data source. Selecting another source starts a
  new session.
- A Dashboard may contain many Widgets, but all of their Datasets must use the
  same data source. Schema-version references remain lineage; they are not a
  second source boundary.
- Dataset, Widget, and Dashboard saves create immutable records. A saved object
  is never silently rebound to a later query, Dataset, Widget, schema, or source.
- Only explicit Workbench **Run** executes SQL. Saving, browsing libraries,
  choosing a visualization, publishing, downloading, and reading publication
  status do not run SQL or call a model.
- Catalyst stores desired Dashboard configuration. Superset renders it.
- A ready bundle is not an imported Dashboard. Catalyst shows imported success
  and enables the stable Superset link only after a matching importer result.
- Bundles contain query and chart configuration, not result rows or source
  credentials.

## Accepted user experience

The accepted design remains the complete product target. The API must support it
without reducing it to the routes that happen to exist today.

### Workbench to Dataset

- The Workbench retains profile selection, generation and failure details, one
  editable SQL control, formatting, typed parameters, advisory findings,
  explicit Run, database results or errors, query versions, Clear/Restore,
  contextual follow-up, refresh restoration, and complete Available data
  browsing.
- A Dataset draft appears only after the exact visible query runs successfully.
  Editing the query makes the previous result visibly stale rather than silently
  changing the draft.
- The Dataset review panel is the one place that shows the full bounded typed
  result table and performs the save.
- Saved Datasets appear in the Dataset library and retain their exact query,
  typed parameters, source, dialect, readable-schema reference, execution, typed
  columns, bounds, and warnings.

### Dataset to Widget

- Widget review starts with a deterministic suggestion derived from the saved
  Dataset's typed shape. It does not call a model.
- The person may choose any compatible accepted type: table, big number,
  time-series line or area, grouped or stacked bar, or proportion bar.
- Bindings are derived from the Dataset shape and remain reviewable. Choosing a
  chart never changes the Dataset SQL or asks the person to repeat its
  calculation.
- Saved Widgets appear in the Widget library and may be used on more than one
  Dashboard.

### Widget to Dashboard

- Dashboard review selects and arranges saved Widgets from one data source.
- Saved Dashboards appear in the Dashboard library with their Widget count,
  layout summary, and publication state.
- The current API persists Widget order but has no separate geometry request.
  If the accepted arrangement experience requires more layout data, extend the
  Dashboard request directly; do not add a parallel layout service.

### Publish and import

- Publish creates a native Superset bundle for one exact Dashboard version and
  exposes the same bytes for download.
- Publication state is `bundle_ready`, `imported`, or `import_failed`.
- `bundle_ready` means the bundle exists and awaits the importer.
- `imported` requires a matching import result and a usable Dashboard URL.
- `import_failed` keeps a bounded, actionable error. It never exposes false
  success or an Open Superset control.

## Current HTTP surface

The implemented routes are defined in
[`dashboard_routes.py`](../../catalyst-gateway/src/catalyst/dashboard_routes.py).

| Method | Route | Behavior |
| --- | --- | --- |
| `GET` | `/datasets` | List saved Dataset entities, newest first. |
| `POST` | `/datasets` | Save the current successful Workbench execution as a Dataset. |
| `GET` | `/widgets` | List saved Widget entities, newest first. |
| `POST` | `/widgets` | Save a Widget for one saved Dataset version. |
| `GET` | `/dashboards` | List saved Dashboard entities, newest first. |
| `POST` | `/dashboards` | Save a Dashboard from an ordered list of Widget versions. |
| `POST` | `/dashboards/{dashboardVersionId}/publish` | Publish one exact Dashboard version. |
| `GET` | `/dashboards/{dashboardVersionId}/publication` | Read that version's publication and import state. |
| `GET` | `/dashboards/{dashboardId}/bundle` | Download the published ZIP for the logical Dashboard. |

The current surface contains no item-detail, update, delete, separate
suggestion, or separate layout routes. Callers use only the routes listed above.

## Requests

All write bodies are JSON objects. Malformed JSON or a non-object body is
rejected. The current handlers ignore extra fields. An omitted or blank title
uses the server's default.

### Save Dataset

```json
{
  "sessionId": "workbench-session-id",
  "executionId": "successful-execution-id",
  "title": "Monthly viral load"
}
```

The Gateway verifies that the session and execution exist, the execution
succeeded, and it belongs to the session's current query version. The UI also
keeps Save disabled when its visible editor differs from that execution.

### Save Widget

```json
{
  "datasetVersionId": "dataset-version-id",
  "title": "Viral load by month",
  "presentationKind": "time_series_line"
}
```

`presentationKind` is optional at the HTTP boundary and currently defaults to
`table`. The reviewed UI sends the person's compatible selection. The Gateway
derives bindings from the Dataset's typed columns and rejects an incompatible
type.

### Save Dashboard

```json
{
  "title": "HIV program",
  "widgetVersionIds": ["widget-version-a", "widget-version-b"]
}
```

The list must be nonempty and contain saved Widget version IDs. Order is
significant. The referenced Widgets must resolve to one `dataSourceId`.

Widgets from the same source remain composable across schema refreshes. A
different `catalogVersion` value alone must not reject their composition.

### Publish

Publish has no request body. The path names the exact Dashboard version to
serialize.

## Responses

Each collection response has this shape:

```json
{
  "contractVersion": "catalyst.dashboard-builder.v1",
  "kind": "dataset",
  "items": []
}
```

`kind` is `dataset`, `widget`, or `dashboard` and matches the route.

Each saved entity has this common shape:

```json
{
  "id": "logical-id",
  "versionId": "immutable-version-id",
  "ordinal": 1,
  "configuration": {},
  "configurationDigest": "sha256",
  "createdAt": "2026-08-26T12:00:00.000Z"
}
```

The current Dataset configuration retains the Workbench source identities,
exact parameterized query, typed parameters, compiled publication query, typed
columns, result bounds, and digests. It does not copy result rows into Dashboard
Builder state. The Dataset's source execution remains the source for the review
table.

The current Widget configuration retains its exact Dataset version, chosen and
suggested presentation types, typed columns, and derived bindings. The current
Dashboard configuration retains its ordered Widget version references and data
source identity.

A publication response contains:

- `status`;
- the exact Dashboard entity;
- `pointer` and `manifest` for the generated bundle;
- `downloadPath`; and
- when available, `importState` with the matching result, identifiers, error, or
  Dashboard URL.

Exact current TypeScript response types are in
[`types.ts`](../../catalyst-ui/src/features/query/types.ts). Exact machine
formats for publication files are:

- [bundle manifest](catalyst-superset-bundle-v1.schema.json);
- [current outbox pointer](catalyst-superset-outbox-current-v1.schema.json);
- [import receipt](catalyst-superset-import-receipt-v1.schema.json);
- [latest import projection](catalyst-superset-import-latest-v1.schema.json); and
- [last verified Dashboard projection](catalyst-superset-last-verified-v1.schema.json).

Those files mirror the running implementation and change atomically with its
consumers. Fields absent from this product contract are listed in the
[machine-contract status](README.md).

## Errors

HTTP errors use the route's actual envelope:

```json
{
  "error": {
    "code": "widget_not_saveable",
    "message": "Bar chart requires a categorical and numeric result."
  }
}
```

- Invalid JSON, a non-object body, or a missing required field returns `400
  invalid_request`.
- A save or publish that cannot satisfy its resource rules returns `422` with
  `dataset_not_saveable`, `widget_not_saveable`, `dashboard_not_saveable`, or
  `publication_not_saveable`.
- A missing Dashboard, publication, or bundle returns `404` with the matching
  `*_not_found` code.

Errors must remain useful to the person and must not include credentials or
result rows.

## Connection and engine independence

Dashboard Builder consumes the source identity, declared SQL dialect, readable
schema reference, typed query, and typed execution result already owned by the
Workbench. It does not discover another schema, translate SQL to another
dialect, or introduce another query path.

The selected reference deployment will use FHIR Data Pipes, Parquet, and Spark
SQL. Its implementation and acceptance are open. Both Catalyst and Superset
will use that same configured source. Engine-specific
parameter handling and Superset export details belong at this publication edge,
not in the Dataset, Widget, Dashboard, route, or user-experience model.

## Acceptance

### Phase 1 saved-query-to-Superset smoke

The Phase 1 connection checkpoint includes one real browser path:

1. run one reviewed query against the configured reference source;
2. save that successful result as a Dataset;
3. create a compatible Widget and a one-source Dashboard;
4. publish and import its bundle; and
5. open the Superset Dashboard and compare one displayed value with the already
   captured Catalyst result, without adding another query path.

This is a regression smoke for the connection and publication seam. It does not
complete Dashboard Builder or waive any accepted screen, interaction, state,
layout, or accessibility requirement.

### Phase 3 final browser acceptance

Final acceptance compares the live application side by side with the binding
design and exercises the whole visible journey:

- Workbench generation, one editor, explicit Run, successful results, a normal
  database error, evidence, findings, Clear/Restore, follow-up, staleness,
  refresh, and complete Available data;
- Dataset draft, review table, save, and library;
- deterministic Widget suggestion, compatible override, save, and library;
- a one-source Dashboard containing multiple Widgets, arrangement, save, and
  library;
- bundle-ready, imported, and failed-import presentation with the correct
  actions; and
- keyboard, focus, overlay, reduced-motion, desktop, and narrow-layout behavior
  required by the accepted design.

The owner performs the final browser review. Passing the Phase 1 smoke alone is
not a Phase 3 acceptance result.

## Stop rules

Keep this path small:

- Do not add a second query engine, schema service, relation allowlist, fixed
  relation count, SQL translator, or shadow data store for Dashboard Builder.
- Do not add model calls or database execution to save, suggestion, library,
  publish, download, or status operations.
- Do not create a second acceptance database path, automatic scoring system, or
  large cross-artifact proof framework.
- Do not add routes, digests, locks, retries, or background services unless a
  concrete accepted user interaction or publication integrity requirement needs
  them.
- Remove engine-specific behavior and tests that have no owner in the generic
  connection design instead of moving them behind new abstractions.
