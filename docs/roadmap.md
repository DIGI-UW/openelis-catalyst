# Catalyst product roadmap

**Status:** Generic connection and the Spark reference deployment are next.
Final Dashboard Builder acceptance follows the validation harness's Phase 1
comparison and Phase 2 scope decision.

[specification.md](specification.md) owns product requirements.
[dashboard-builder-mvp-design.md](dashboard-builder-mvp-design.md) owns the
binding Dashboard Builder interaction and visual contract. This file owns the
Catalyst-repository implementation sequence.

## Outcome

```text
configured source + declared dialect
  -> complete readable schema
  -> model and editor
  -> exact selected SQL
  -> rows or database error
  -> saved Dataset -> Widget -> Dashboard -> Superset
```

The selected reference deployment will use FHIR Data Pipes -> Parquet -> Spark
SQL. Its implementation and acceptance are open; it does not define Catalyst core.

## Starting point

The current code provides the product seams and has these connection gaps:

- `catalyst-gateway/src/config.py::DataSourceConfig` requires an analytics
  address and generated catalog path;
- `catalyst-gateway/src/gateway.py::_default_catalyst_service` constructs one
  engine-specific adapter for every source;
- `catalyst-gateway/src/catalyst/catalog.py::Catalog` mixes live discovery,
  optional descriptions, and relation filtering;
- current machine schemas and serializers require `approvedViews`, one
  PostgreSQL-shaped relation name, and mandatory grain/description metadata;
- execution, validation, editor vocabulary, errors, and types contain
  engine-specific behavior;
- `docker-compose.mvp.yml` disables Parquet and Spark views and redirects FHIR
  Data Pipes into a separate analytics store; and
- Superset is wired to that separate store.

Keep `AnalyticsProtocol`, `DataSourceBundle`, conversation, query versions,
explicit Run, result display, Dashboard Builder, deterministic bundle
publication, and importer receipts. Simplify them around the current product
contract.

## Documentation status

The README, development instructions, specification, this roadmap, Dashboard
design, and human-readable contracts state the selected behavior. Machine
contract indexes identify fields that change with implementation. Owner review
of the complete planning diff remains open.

## 1. Generic connection behavior

Use `AnalyticsProtocol` and `DataSourceBundle`. The shared connection code
does only:

- independent source availability;
- complete readable schema discovery;
- exact SQL execution with typed parameters, time limit, and row limit; and
- typed rows or the database error.

Source configuration contains identity, label, connection configuration or
reference, explicit dialect, and optional descriptions.

Exit:

- arbitrary readable fixture relations reach model and editor;
- database-native identifiers and engine-appropriate qualification survive
  discovery and execution;
- descriptions enrich without filtering;
- editor completion, formatting, and validation use the declared dialect;
- advisory findings do not block exact selected SQL;
- generated and manually edited queries use shared execution code;
- valid rows and database errors are stored and displayed;
- one unavailable source does not prevent startup; and
- focused tests prove those behaviors without a connector framework, SQL
  translation, or second catalog service.

Pause for review.

## 2. Spark reference deployment

For every source packaged in this repository and actually included in the demo:

- enable the pinned FHIR Data Pipes Parquet path;
- materialize applicable ViewDefinitions;
- expose the readable tables through Spark SQL;
- register Spark as an ordinary Catalyst source;
- configure Superset for the same Spark source; and
- visibly refuse one intentional write attempt through the Spark connection and
  leave source data unchanged.

Run one manual Spark query to prove materialization and a known fact. This is a
one-time endpoint check, not a new acceptance framework.

Exit:

- nonempty Parquet exists;
- Spark and Catalyst expose the expected readable tables;
- one successful query and one database error are visible in the browser;
- one intentional write attempt is visibly refused and leaves source data
  unchanged;
- one generated query survives refresh;
- one saved Dataset publishes, imports, and renders in Superset;
- one displayed value is inspected against the originating Catalyst result
  without another database query; and
- no separate clinical analytics store, shadow copy, or fallback participates.

Pause for owner review of the live browser and Superset smoke.

## 3. Keep one runtime path

After the generic path works, remove code and files with no current product or
deployment owner:

- preferred-engine configuration and automatic adapter construction;
- relation approval/filtering and generated-catalog requirements;
- engine-specific type, error, parsing, validation, and editor assumptions that
  do not belong in the selected client's small adapter;
- copied marts, sink scripts, health checks, and catalog generators; and
- tests without a current product or deployment requirement.
- standalone `catalyst-agents` and `catalyst-mcp` packages and their development
  wiring.

Do not change source-application storage or Superset's internal metadata store.
Carry forward only descriptions or relationships shown to help the accepted
readable schema.

Exit:

- active product code has one connection path;
- no hidden fallback exists;
- current user instructions describe the active behavior; and
- focused tests and ordinary continuous-integration checks pass.

## 4. Phase 1 regression support

The validation harness pins the merged Catalyst revision and runs the context
comparison. Catalyst must provide:

- one source per session;
- the complete readable schema and declared dialect in model evidence;
- exact selected SQL execution once per ready turn;
- no execution for clarification or unsupported turns;
- rows or database errors with query lineage; and
- one Dataset-to-Superset regression smoke.

This support does not close Dashboard Builder.

## 5. Phase 3 Dashboard Builder completion

After Phase 1 review and the Phase 2 scope decision, finish the remaining
Dashboard Builder work:

- public route coverage;
- lossless typed execution-to-Dataset conversion for the active dialect;
- deterministic visualization compatibility and reviewable override;
- deterministic bundle generation and receipt-based status;
- real model-assisted ask -> Dataset -> multiple Widgets -> arranged Dashboard
  -> publish -> import -> stable URL; and
- focused accessibility and layout checks.

Compare every live Workbench, Dataset review/library, Widget review/library,
Dashboard library/arrangement, and publish/import state side by side with the
binding design. Final acceptance is the owner's browser review.

## Guardrails

Do not add a connector framework, SQL translator, second catalog service,
relation allowlist, schema ranking, fixed schema/context count, FHIR Data Pipes
fork, namespace layer, shadow warehouse, automatic fallback, direct database
reconciliation, row hashing, repeated model runs, restart/reset matrices, or
live Spark on every pull request.

If the complete readable schema, thin connection behavior, or pinned FHIR Data
Pipes path fails, record the concrete failure and return to the owner before
adding another subsystem.
