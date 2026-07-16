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

CREATE OR REPLACE VIEW analytics.lab_result_fact_v1 AS
SELECT
    observation.id AS observation_id,
    observation.patient_id,
    observation.service_request_id,
    observation.specimen_id,
    observation.result_status,
    observation.observed_at,
    observation.issued_at,
    observation.test_code_system,
    observation.test_code,
    observation.test_name,
    observation.result_value,
    observation.result_unit,
    observation.result_unit_system,
    observation.result_unit_code
FROM public.observation_flat_v1 AS observation;

COMMENT ON VIEW analytics.lab_result_fact_v1 IS
    'Demo-only laboratory result fact at exactly one row per FHIR Observation.';

COMMIT;
