-- OpenELIS analytics layer over the fhir-data-pipes DEFAULT flat tables.
--
-- Layering rule (deliberate): the ingestion layer uses the upstream default
-- ViewDefinitions essentially verbatim (lossless, one row per resource per
-- coding via forEachOrNull) plus documented additive extensions and gap-fill
-- views for resources upstream ships none for (Specimen, ServiceRequest).
-- The replaceable SQL views below define the query-facing shape. The harness
-- generates the catalog from this file's view/column comments plus
-- analytics/catalog-overlay.json.

BEGIN;

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.pipeline_run_v1 (
    pipeline_run_id text PRIMARY KEY,
    contract_version text NOT NULL DEFAULT 'catalyst.analytics.pipeline-run.v1'
        CHECK (contract_version = 'catalyst.analytics.pipeline-run.v1'),
    completion_state text NOT NULL
        CHECK (completion_state IN ('running', 'succeeded', 'failed')),
    source_watermark timestamptz,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    data_pipes_commit text NOT NULL,
    resource_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    CHECK (
        (completion_state = 'running' AND completed_at IS NULL)
        OR
        (completion_state IN ('succeeded', 'failed') AND completed_at IS NOT NULL)
    ),
    CHECK (
        completion_state <> 'succeeded'
        OR source_watermark IS NOT NULL
    )
);

COMMENT ON TABLE analytics.pipeline_run_v1 IS
    'One row per Data Pipes run; the authoritative Catalyst freshness/run contract.';
COMMENT ON COLUMN analytics.pipeline_run_v1.source_watermark IS
    'Greatest source resource timestamp fully represented by a completed run.';
COMMENT ON COLUMN analytics.pipeline_run_v1.observed_at IS
    'Time at which run metadata was observed and recorded in the analytics store.';

CREATE OR REPLACE VIEW analytics.pipeline_freshness_v1 AS
SELECT
    pipeline_run_id,
    contract_version,
    completion_state,
    source_watermark,
    started_at,
    completed_at,
    observed_at,
    data_pipes_commit,
    resource_counts,
    error_message,
    CASE
        WHEN source_watermark IS NULL THEN NULL
        ELSE EXTRACT(EPOCH FROM (observed_at - source_watermark))::bigint
    END AS observed_lag_seconds
FROM analytics.pipeline_run_v1;

COMMENT ON VIEW analytics.pipeline_freshness_v1 IS
    'Structured source watermark, run state, and observed lag; never a single ambiguous timestamp.';

-- Clear object names used by the views defined below.
DROP VIEW IF EXISTS analytics.lab_result_fact_v1;
-- A source may already contain service_request_flat_v1 as a table or a view.
-- PostgreSQL requires the matching DROP form, so handle both object types.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'public' AND tablename = 'service_request_flat_v1'
    ) THEN
        EXECUTE 'DROP TABLE public.service_request_flat_v1';
    END IF;
END $$;
DROP VIEW IF EXISTS public.service_request_flat_v1;
DROP TABLE IF EXISTS public.observation_flat_v1;
DROP TABLE IF EXISTS public.patient_flat_v1;
DROP TABLE IF EXISTS public.specimen_flat_v1;
DROP TABLE IF EXISTS public.diagnostic_report_flat_v1;

-- One-row-per-request projection used by the seed and health checks. It
-- collapses the lossless service_request_flat coding cross product and pivots
-- the LOINC coding into the test_* columns.
CREATE VIEW public.service_request_flat_v1 AS
SELECT
    sr.id,
    MAX(sr.patient_id) AS patient_id,
    MAX(sr.specimen_id) AS specimen_id,
    MAX(sr.request_status) AS request_status,
    MAX(sr.request_intent) AS request_intent,
    MAX(sr.authored_at) AS authored_at,
    MAX(sr.code_sys) FILTER (WHERE sr.code_sys = 'http://loinc.org')
        AS test_code_system,
    MAX(sr.code_code) FILTER (WHERE sr.code_sys = 'http://loinc.org')
        AS test_code,
    COALESCE(
        MAX(sr.code_display) FILTER (WHERE sr.code_sys = 'http://loinc.org'),
        MAX(sr.code_display)
    ) AS test_name
FROM public.service_request_flat AS sr
GROUP BY sr.id;

COMMENT ON VIEW public.service_request_flat_v1 IS
    'One row per FHIR ServiceRequest over the lossless service_request_flat base; LOINC coding pivoted into the test_* columns.';

CREATE VIEW analytics.lab_result_fact_v1 AS
WITH per_observation AS (
    SELECT
        o.id AS observation_id,
        MAX(o.patient_id) AS patient_id,
        MAX(o.service_request_id) AS service_request_id,
        MAX(o.specimen_id) AS specimen_id,
        MAX(o.status) AS result_status,
        MAX(o.obs_date) AS observed_at,
        MAX(o.issued) AS issued_at,
        MAX(o.code_sys) FILTER (WHERE o.code_sys = 'http://loinc.org')
            AS test_code_system,
        MAX(o.code_code) FILTER (WHERE o.code_sys = 'http://loinc.org')
            AS test_code,
        COALESCE(
            MAX(o.code_display) FILTER (WHERE o.code_sys = 'http://loinc.org'),
            MAX(o.code_display)
        ) AS test_name,
        MAX(o.val_quantity) AS result_value,
        MAX(o.val_quantity_unit) AS result_unit,
        MAX(o.val_quantity_system) AS result_unit_system,
        MAX(o.val_quantity_code) AS result_unit_code
    FROM public.observation_flat AS o
    GROUP BY o.id
)
SELECT
    f.observation_id,
    f.patient_id,
    f.service_request_id,
    f.specimen_id,
    f.result_status,
    f.observed_at,
    f.issued_at,
    f.test_code_system,
    f.test_code,
    f.test_name,
    f.result_value,
    f.result_unit,
    f.result_unit_system,
    f.result_unit_code,
    s.received_at AS specimen_received_at,
    (
        EXTRACT(EPOCH FROM (f.issued_at - s.received_at))
        / 60.0
    )::numeric AS receipt_to_release_minutes
FROM per_observation AS f
LEFT JOIN (
    SELECT DISTINCT id, received_at FROM public.specimen_flat
) AS s
    ON s.id = f.specimen_id;

COMMENT ON VIEW analytics.lab_result_fact_v1 IS
    'Exactly one row per FHIR Observation, with at most one Specimen matched by resource key. Built over the lossless default projections: the per-coding cross product is collapsed per observation and the LOINC coding pivoted into the test_* columns.';

COMMENT ON COLUMN analytics.lab_result_fact_v1.observation_id IS
    'FHIR Observation resource identifier and stable row identity for the laboratory result.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.patient_id IS
    'FHIR Patient resource identifier referenced by the observation.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.service_request_id IS
    'FHIR ServiceRequest resource identifier referenced by the observation.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.specimen_id IS
    'FHIR Specimen resource identifier referenced by the observation.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.result_status IS
    'FHIR Observation status for the laboratory result.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.observed_at IS
    'FHIR Observation effective date and time used to place the result clinically.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.issued_at IS
    'FHIR Observation issued instant.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.test_code_system IS
    'Coding-system URI associated with the observation test code.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.test_code IS
    'OpenELIS/FHIR test code for the observation.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.test_name IS
    'OpenELIS test display name. A question naming an analyte must constrain this field rather than assume the view contains only that analyte.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.result_value IS
    'Numeric FHIR Quantity value; do not aggregate across unlike units.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.result_unit IS
    'FHIR Quantity display unit.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.result_unit_system IS
    'Coding-system URI associated with the FHIR Quantity unit.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.result_unit_code IS
    'Machine-readable FHIR Quantity unit code.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.specimen_received_at IS
    'FHIR Specimen received date and time when a matching specimen is available.';
COMMENT ON COLUMN analytics.lab_result_fact_v1.receipt_to_release_minutes IS
    'Elapsed minutes from Specimen.receivedTime to Observation.issued.';

GRANT USAGE ON SCHEMA analytics TO catalyst_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO catalyst_readonly;

COMMIT;
