# Catalyst analytics demo contract

This directory defines the first demo-only PostgreSQL analytics slice:

```text
OpenELIS 3.2.1.x
  -> OpenELIS HAPI store
  -> OHS FHIR Data Pipes (FHIR_SEARCH)
  -> PostgreSQL per-resource projections
  -> analytics.lab_result_fact_v1
```

It is not a production migration, clinical fixture, or supported upgrade path.
The root Compose files intentionally do not start the analytics database or
Data Pipes controller yet.

## Fixed dependency

Run:

```bash
./scripts/bootstrap-fhir-data-pipes.sh
```

The script checks out
`3ea890884d674e2f31257a2da421601f2d75b5e9` in
`.fhir-data-pipes/`, refuses tracked local changes, and verifies `HEAD`.

## Synthetic OpenELIS seed

`openelis/seed-openelis-3.2.1.sql` inserts one synthetic patient and three
finalized viral-load results:

| Accession | Observed date | Result |
| --- | --- | --- |
| `CATVL0001` | 2026-01-15 | 1200 copies/mL |
| `CATVL0002` | 2026-02-15 | 450 copies/mL |
| `CATVL0003` | 2026-03-15 | 80 copies/mL |

The seed uses fixed FHIR UUIDs and source markers. Re-running it verifies and
reuses matching rows; it fails rather than overwrite conflicting rows.

Live assumptions are deliberately strict:

- the operator supplies the actual deployed release and it matches
  `3.2.1.x`;
- the `3.2.x.x` Liquibase family and FHIR UUID columns are present;
- the stock active OpenELIS `Viral Load` test has GUID
  `b50d156e-0f6f-40cd-921c-4e831602a623` and a copies/mL unit;
- the stock whole-blood and finalized/sample status reference rows exist;
- direct SQL is acceptable only for this disposable demo database.

A customized test catalog or schema should fail the guards. It should not be
made to pass by weakening them.

With the OpenELIS database and web application already ready:

```bash
OPENELIS_VERSION=3.2.1.11 ./scripts/mvp-seed.sh
```

The SQL insert bypasses OpenELIS application events. Therefore `mvp-seed.sh`
calls `openelis/backfill-hapi.sh`, which invokes the synchronous
`/OEToFhir?checkAll=true&batchSize=10&threads=1&waitForResults=true` contract
and waits for all fixed Patient, Observation, ServiceRequest, Specimen, and
DiagnosticReport IDs in HAPI.

Important limitation: OpenELIS 3.2.1.x exposes no seed-scoped backfill route.
`checkAll=true` re-transforms every existing sample-backed record, not only the
Catalyst fixture. Use it only on a small disposable demo database. The script
defaults to the local development endpoints and supports:

- `OE_BACKFILL_URL`, `OE_USERNAME`, `OE_PASSWORD`, `OE_TLS_INSECURE`;
- `HAPI_FHIR_URL`, `HAPI_CLIENT_CERT`, `HAPI_CLIENT_KEY`,
  `HAPI_TLS_INSECURE`;
- `FHIR_WAIT_ATTEMPTS`, `FHIR_WAIT_SECONDS`.

## Data Pipes controller

`config/controller/application.yaml` uses:

- `FHIR_SEARCH`, not HAPI JDBC;
- only Patient, Observation, ServiceRequest, Specimen, and DiagnosticReport;
- a PostgreSQL sink with SQL-on-FHIR ViewDefinitions;
- no Parquet view generation or Hive resource tables.

The four ViewDefinitions are intentionally single-select, non-exploding
projections. `observation_flat_v1` therefore keeps one row per Observation;
the semantic fact does not join repeated FHIR arrays.

The committed `config/postgres-sink.json` contains disposable local-demo
credentials and host names. Create that database/user or render a runtime copy
with environment-specific values. Never reuse this file for production.
Mount `analytics/config/controller/application.yaml` as the controller
configuration, `analytics/config/views/` as `config/views`, and the sink JSON
as `config/postgres-sink.json`.

Start an initial controller run with:

```bash
curl -fsS -X POST 'http://localhost:8090/run?runMode=FULL'
curl -fsS 'http://localhost:8090/status'
```

The initial run must finish with `pipelineStatus` returning to `IDLE` and the
four projection tables present before applying:

```bash
psql "$ANALYTICS_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f analytics/sql/001_analytics_v1.sql
```

Use explicit `FULL` runs for this sink-only demo. The pinned controller requires
an `incrementalSchedule` value, but its incremental path merges Parquet
snapshots and is not the refresh contract for this no-Parquet configuration.
The annual cron in the sample config is a placeholder, not a freshness SLA.

## Semantic catalog and freshness

`catalog/analytics-catalog-v1.json` is the profile-facing allowlist. It records
the stable view/version, one-Observation grain, typed columns and units,
allowed filters/groupings, terminology caveats, mandatory date/result limits,
freshness behavior, examples, and demo-only classification.

`contracts/pipeline-run-v1.schema.json` and
`analytics.pipeline_run_v1` define the run metadata contract. The controller
does not populate Catalyst-specific metadata itself. The deployment wrapper
that starts or observes a run must insert one row, for example:

```sql
INSERT INTO analytics.pipeline_run_v1 (
  pipeline_run_id, completion_state, source_watermark, started_at,
  completed_at, observed_at, data_pipes_commit, resource_counts
) VALUES (
  'full-20260716T000000Z', 'succeeded', '2026-03-15T09:00:00Z',
  '2026-07-16T00:00:00Z', '2026-07-16T00:01:00Z',
  '2026-07-16T00:01:00Z',
  '3ea890884d674e2f31257a2da421601f2d75b5e9',
  '{"Observation":3,"ServiceRequest":3,"Specimen":3,"DiagnosticReport":3}'
);
```

`source_watermark` is the greatest source timestamp fully represented by that
completed run. `observed_at` is when the metadata was measured. Consumers use
the run ID, completion state, watermark, and derived observed lag together;
no single timestamp is called “freshness.”

After loading run metadata:

```bash
ANALYTICS_DATABASE_URL='postgresql://...' \
  ./scripts/mvp-analytics-health.sh
```
