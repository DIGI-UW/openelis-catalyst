# AGENTS.md

## Product authority

Catalyst is a generic SQL-connected workbench and Dashboard Builder. Read these
files before changing product behavior:

1. `docs/specification.md`
2. `docs/roadmap.md`
3. `docs/dashboard-builder-mvp-design.md`
4. `docs/med-agent-hub.md`

The binding Dashboard visual reference is
`docs/prototypes/dashboard-builder-mvp/Catalyst Dashboard Builder 4c.dc.html`.

Current documents contain current requirements and implementation status. Run
reports are evidence, not product requirements.

## Architecture rules

A source declares its identity, label, connection configuration or reference,
and SQL dialect. Model and human tools receive every table, view, column, and
type readable through that connection. Optional descriptions may enrich but
cannot filter that information.

Catalyst owns conversation, model-request assembly, query versions, advisory
validation, shared connection execution, results, Dataset/Widget/Dashboard
state, and native Superset bundle publication. It does not own FHIR ingestion, a
clinical warehouse, or a preferred database engine.

FHIR Data Pipes -> Parquet -> Spark SQL is the selected reference deployment.
Its implementation and acceptance are open; it is not Catalyst core. OpenELIS deployment files live under `analytics/` for
packaging convenience.

Validation never blocks or rewrites exact selected SQL. Catalyst retains its time
and returned-row limits and records typed rows or the database error. Do not add
an application relation allowlist, fixed relation count, SQL translation,
automatic database fallback, or a second database comparison path.

A session binds one source. Changing source starts a new session.

## Program order

The validation harness owns program order:

1. implement the generic connection and run the Phase 1 context comparison;
2. define Phase 2 conversation mode after reviewing that report;
3. complete Phase 3 Dashboard Builder.

Phase 1 connection implementation includes one Dataset-to-Superset regression smoke. That
smoke does not close or reduce Dashboard Builder.

For final Dashboard acceptance, compare the live Workbench, Dataset
review/library, Widget review/library, Dashboard library/arrangement, and
publish/import states side by side with the binding design. Backend or evidence
work cannot replace browser-visible acceptance. Only explicit owner approval may
change product scope.

## Toolchain

- Python 3.11, pinned in `.python-version`
- uv
- Node.js 22
- Docker and Docker Compose v2.20 or newer

Each Python component owns its own `pyproject.toml`, `uv.lock`, and
environment.

## Setup

```bash
cp env.recommended .env
mkdir -p logs
cd catalyst-gateway && uv sync --frozen --extra dev && cd ..
cd catalyst-ui && npm ci && cd ..
```

The Clinical AI Validation Harness supplies the pinned med-agent-hub sibling and
owns the combined local reference stack. Use its `scripts/catalyst-mvp.sh`
wrapper for cross-repository work. Do not invoke Catalyst Compose alone for
acceptance because the wrapper supplies the isolated ports, Hub context, and
reference-source configuration. Seeding and reset remain explicit operations.

The Spark reference stack is not implemented. Current Compose files do not
establish Spark acceptance.

## Model boundary

med-agent-hub owns query profiles, prompts, role-to-model mapping, and model
settings. Catalyst Gateway discovers available profiles and invokes named roles.
Catalyst cannot silently substitute a model, prompt, or role configuration.

Catalyst owns the request's source, dialect, complete readable schema, session
context, writer/checker ordering, advisory findings, execution, and recorded
versions and configuration. med-agent-hub does not discover schemas or execute
SQL.

## Development approach

- Prefer small vertical changes that establish one current path and remove code
  with no remaining owner.
- Use the existing `AnalyticsProtocol` and `DataSourceBundle` seams before
  creating another abstraction.
- Run generated and manually edited SQL through shared connection-execution
  code.
- Remove behavior with no current requirement and dedicated tests rather than preserving a
  compatibility flag or porting it to Spark.
- Carry forward a source description or relationship only when the accepted
  readable schema demonstrably uses it.
- If the complete readable schema, thin connection behavior, or pinned FHIR Data
  Pipes path fails, record the concrete failure and ask the owner before adding
  selection, translation, fallback, or another subsystem.

Do not add restart, reseed, environment-parity, exhaustive-failure,
direct-database reconciliation, repeated-model-run, or repeated-reader gates.

## Tests

Run focused checks for the component changed:

```bash
uv run ruff format --check .
uv run ruff check .
PYTHONPATH=. uv run pytest tests/ -v
```

For UI work:

```bash
cd catalyst-ui
npm test
npx playwright test --project=deterministic
```

A reference source receives one live FHIR Data Pipes -> Parquet -> Spark ->
Catalyst proof when it is integrated. Ordinary unrelated pull requests do not
require a live Spark service.

For Dashboard Builder, focused API/component tests support but do not replace
the final side-by-side browser review. The final proof uses the real configured
model profile, one successful query, one database error, one saved Dataset,
multiple Widgets, a published/imported Dashboard, and one rendered value checked
against the originating Catalyst result without a second database query.
