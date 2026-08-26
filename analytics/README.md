# Catalyst analytics demo contract

This directory defines the first demo-only PostgreSQL analytics slice:

```text
OpenELIS 3.2.1.x
  -> OpenELIS HAPI store
  -> OHS FHIR Data Pipes (FHIR_SEARCH)
  -> upstream default ViewDefinitions (lossless: one row per resource per
     coding, via forEachOrNull) + documented additive extensions
  -> curation in SQL (sql/001_analytics_v1.sql): collapses the per-coding
     cross product, pivots the LOINC coding into typed columns
  -> analytics.lab_result_fact_v1
```

Layering rule: the ingestion layer (`config/views/`) is the upstream
fhir-data-pipes default ViewDefinitions essentially verbatim, plus additive
extensions and gap-fill views for resources upstream ships none for
(Specimen, ServiceRequest). It is lossless — every coding on every resource
survives as its own row. ALL curation (collapsing to one row per resource,
picking a canonical display, pivoting known coding systems into columns)
happens afterward in SQL, where a mistake costs a `CREATE OR REPLACE VIEW`
instead of a full FHIR re-fetch. The catalog the gateway reads is GENERATED
from this SQL's view/column comments plus `catalog-overlay.json` by the
harness's `scripts/generate-catalyst-source-catalog.py` — never
hand-maintained.

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

The ViewDefinitions in `config/views/` are the upstream default projections
used essentially verbatim: `patient_flat`, `observation_flat`, and
`diagnostic_report_flat` are exactly the upstream defaults; `observation_flat`
carries one additive extension column (`issued`) plus
`basedOn`/`specimen` reference-key columns; `specimen_flat` and
`service_request_flat` are gap-fill views (upstream ships none) following the
same `forEachOrNull` cross-product pattern. Every one of these is lossless —
`patient_flat` has one row per (Patient x name x identifier x
generalPractitioner) combination, `observation_flat` one row per
(Observation x code.coding x value.coding), and so on. Nothing is collapsed,
deduplicated, or "first()"-picked at this layer.

The curated fact view (`analytics.lab_result_fact_v1`, defined in
`sql/001_analytics_v1.sql`) is what collapses `observation_flat`'s per-coding
rows to one row per Observation (`GROUP BY o.id`) and pivots the LOINC coding
into the `test_*` columns (`MAX(...) FILTER (WHERE code_sys = 'http://loinc.org')`),
falling back to whatever coding is present via `COALESCE` when no LOINC coding
was recorded. `public.service_request_flat_v1` is the equivalent
one-row-per-request compatibility view over `service_request_flat`.

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
five projection tables present before applying:

```bash
psql "$ANALYTICS_DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f analytics/sql/001_analytics_v1.sql
```

Use explicit `FULL` runs for this sink-only demo. The pinned controller requires
an `incrementalSchedule` value, but its incremental path merges Parquet
snapshots and is not the refresh contract for this no-Parquet configuration.
The annual cron in the sample config is a placeholder, not a freshness SLA.

## Semantic catalog and freshness

### Synthetic Specimen receipt-time compatibility transform

OpenELIS 3.2.1.x's legacy `OEToFhir` transform preserves collection time for
directly seeded samples but stamps `Specimen.receivedTime` at export time. The
versioned Catalyst fixture defines a receipt-to-release interval in OpenELIS.
`normalize-catalyst-specimen-times.py` reads that interval from each synthetic
OpenELIS sample/analysis pair and applies it relative to the actual FHIR
Observation issued time, only for `CAT*` accessions, through HAPI's transaction
API before Data Pipes runs.
The transform is deterministic and idempotent; it is not used for non-fixture
records.

`catalog/analytics-catalog-v1.json` is GENERATED by the harness's
`scripts/generate-catalyst-source-catalog.py` from this SQL's
`COMMENT ON VIEW`/`COMMENT ON COLUMN` plus `catalog-overlay.json` (the one
hand-maintained input: identity, views receiving curated metadata, and
semantic canonical values).
It carries only the sections the gateway actually consumes — version, grain,
typed columns, units, semantic dimensions — not the inert
filters/groupings/examples/freshness metadata older hand-written catalogs
carried. At runtime, PostgreSQL grants define the query boundary: Catalyst
discovers every relation and column the configured read-only role can select
and uses that same catalog for model grounding, editor completion, and
validation.

### `datasetBrowser`: which relation the dataset view reads

The catalog also carries a `datasetBrowser` block, copied through from
`catalog-overlay.json` verbatim because it is a curation decision the database
cannot answer. The dataset view renders one generic shape — subject, category,
value, unit, timestamps — and every source spells those differently: here the
category is `test_name` on `analytics.lab_result_fact_v1`, while the OpenMRS
HIV source calls it `concept_name` on `analytics.hiv_observation_fact_v1`.

```json
"datasetBrowser": {
  "factView": "analytics.lab_result_fact_v1",
  "identityColumn": "observation_id",
  "subjectColumn": "patient_id",
  "categoryColumn": "test_name",
  "observedAtColumn": "observed_at",
  "valueColumn": "result_value",
  "unitColumn": "result_unit",
  "issuedAtColumn": "issued_at",
  "durationColumn": "receipt_to_release_minutes"
}
```

Only `factView` and the identity/subject/category/observedAt columns are
required; a source without a unit, an issued time, or a turnaround interval
reports those as null rather than inventing them. A source whose value is
coded or textual rather than numeric can list `valueFallbackColumns`, which are
coalesced in order for display — without them a source like the HIV one renders
an empty value on most rows, because its answers live in `value_coded_name`
rather than `value_numeric`.

Every identifier is checked at catalog load: it must be a plain lowercase SQL
identifier and must exist in the named view, so a typo fails on startup naming
the offending column instead of reaching the database. A catalog with no
`datasetBrowser` is still fully queryable — the block only governs the dataset
view, which reports that this source is unconfigured rather than guessing
another source's column names.

## More than one data source

This directory is one source's slice. Catalyst registers additional sources
through `CATALYST_DATA_SOURCES_PATH`, a JSON registry naming each source's id,
label, `analyticsDsn`, and `catalogPath`. Each source is independent: its own
database, its own generated catalog, its own `datasetBrowser` mapping. Nothing
here is shared with another source, and a turn targets exactly one of them, so
switching sources mid-session cannot mix schemas.

The second source shipped with the demo (OpenMRS HIV/ART) lives outside this
repository, in the harness's `catalyst-sources/openmrs-hiv/`, because it brings
its own ingestion pipeline and database. See `DEMO-DEPLOY.md` for how a
directory of extra sources is mounted into the demo stack.

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
  '{"Patient":1,"Observation":3,"ServiceRequest":3,"Specimen":3,"DiagnosticReport":3}'
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
