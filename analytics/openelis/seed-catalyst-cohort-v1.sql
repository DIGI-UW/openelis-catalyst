\set ON_ERROR_STOP on

BEGIN;
SET LOCAL search_path TO clinlims, public;

CREATE OR REPLACE FUNCTION pg_temp.fixture_uuid(value text)
RETURNS uuid
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT (
        substr(md5(value), 1, 8) || '-' ||
        substr(md5(value), 9, 4) || '-4' ||
        substr(md5(value), 14, 3) || '-8' ||
        substr(md5(value), 18, 3) || '-' ||
        substr(md5(value), 21, 12)
    )::uuid
$$;

CREATE TEMP TABLE catalyst_fixture_result (
    patient_index integer NOT NULL,
    test_guid uuid NOT NULL,
    test_code text NOT NULL,
    sequence_no integer NOT NULL,
    observed_at timestamp NOT NULL,
    result_value numeric NOT NULL,
    turnaround_minutes integer NOT NULL,
    accession_number text NOT NULL UNIQUE
) ON COMMIT DROP;

-- OpenELIS omits FHIR CodeableConcepts when a test has neither an active
-- terminology mapping nor a legacy test.loinc value. Keep the synthetic
-- fixture exportable while preserving any nonblank site-configured mapping.
CREATE TEMP TABLE catalyst_fixture_test_terminology (
    test_guid uuid PRIMARY KEY,
    loinc text NOT NULL
) ON COMMIT DROP;

INSERT INTO catalyst_fixture_test_terminology VALUES
    ('b50d156e-0f6f-40cd-921c-4e831602a623', '25836-8'),
    ('a6718123-8d56-4103-9bbe-26b19306b83d', '24467-3'),
    ('614652de-5e04-4fe7-a897-77d976317d2b', '8123-2'),
    ('466b3775-e117-4268-92a7-3d3de95d43b3', '718-7'),
    ('17ff4ca7-b8b6-44a1-bae0-97f38affc35c', '777-3'),
    ('e08bdd35-b7e4-4910-ae73-da5b6447e901', '6690-2'),
    ('d7f672c4-52ea-4c26-bdf0-e9527d2ba95f', '2160-0'),
    ('3a3661a1-a166-4590-90bc-937912789739', '1742-6'),
    ('8410a83b-d09a-475d-a71c-1fcbcca94e58', '2345-7');

-- Complete the original patient trajectory with an undetectable-boundary result.
INSERT INTO catalyst_fixture_result VALUES (
    1,
    'b50d156e-0f6f-40cd-921c-4e831602a623',
    'VL',
    4,
    timestamp '2026-04-15 09:00:00',
    45,
    120,
    'CAT-VL-001-04'
);

-- Four reproducible viral-load trajectories for patients 2-96.
INSERT INTO catalyst_fixture_result
SELECT
    patient_index,
    'b50d156e-0f6f-40cd-921c-4e831602a623'::uuid,
    'VL',
    sequence_no,
    timestamp '2025-07-15 09:00:00'
        + ((sequence_no - 1) * interval '90 days')
        + ((patient_index % 17) * interval '1 day'),
    CASE patient_index % 4
        WHEN 0 THEN (ARRAY[45, 40, 35, 30]::numeric[])[sequence_no]
        WHEN 1 THEN (ARRAY[35000, 4200, 800, 45]::numeric[])[sequence_no]
        WHEN 2 THEN (ARRAY[25000, 12000, 8000, 6000]::numeric[])[sequence_no]
        ELSE (ARRAY[40, 45, 5000, 9000]::numeric[])[sequence_no]
    END,
    CASE
        WHEN patient_index % 12 = 0 THEN 2880
        ELSE 90 + ((patient_index * 13 + sequence_no * 17) % 390)
    END,
    'CAT-VL-' || lpad(patient_index::text, 3, '0') || '-'
        || lpad(sequence_no::text, 2, '0')
FROM generate_series(2, 96) AS patient_index
CROSS JOIN generate_series(1, 4) AS sequence_no;

-- Explicit threshold cases used by deterministic validation scenarios.
UPDATE catalyst_fixture_result SET result_value = 50
WHERE patient_index = 8 AND test_code = 'VL' AND sequence_no = 4;
UPDATE catalyst_fixture_result SET result_value = 1000
WHERE patient_index = 9 AND test_code = 'VL' AND sequence_no = 3;

-- Eight complementary analytes supported by the installed OpenELIS catalog.
INSERT INTO catalyst_fixture_result
SELECT
    patient_index,
    analyte.test_guid,
    analyte.test_code,
    1,
    timestamp '2026-03-20 10:00:00'
        + ((patient_index % 19) * interval '1 day')
        + (analyte.day_offset * interval '1 day'),
    CASE analyte.test_code
        WHEN 'CD4A' THEN 180 + ((patient_index * 17) % 850)
        WHEN 'CD4P' THEN 8 + ((patient_index * 7) % 38)
        WHEN 'HGB' THEN 9.5 + ((patient_index * 11) % 70) / 10.0
        WHEN 'PLT' THEN 120 + ((patient_index * 19) % 330)
        WHEN 'WBC' THEN 3.5 + ((patient_index * 13) % 90) / 10.0
        WHEN 'CREA' THEN 6 + ((patient_index * 5) % 10)
        WHEN 'ALT' THEN 15 + ((patient_index * 23) % 80)
        WHEN 'GLU' THEN 0.65 + ((patient_index * 7) % 85) / 100.0
    END,
    45 + ((patient_index * 11 + analyte.day_offset * 29) % 360),
    'CAT-' || analyte.test_code || '-'
        || lpad(patient_index::text, 3, '0') || '-01'
FROM generate_series(1, 96) AS patient_index
CROSS JOIN (
    VALUES
        ('a6718123-8d56-4103-9bbe-26b19306b83d'::uuid, 'CD4A', 0),
        ('614652de-5e04-4fe7-a897-77d976317d2b'::uuid, 'CD4P', 1),
        ('466b3775-e117-4268-92a7-3d3de95d43b3'::uuid, 'HGB', 2),
        ('17ff4ca7-b8b6-44a1-bae0-97f38affc35c'::uuid, 'PLT', 3),
        ('e08bdd35-b7e4-4910-ae73-da5b6447e901'::uuid, 'WBC', 4),
        ('d7f672c4-52ea-4c26-bdf0-e9527d2ba95f'::uuid, 'CREA', 5),
        ('3a3661a1-a166-4590-90bc-937912789739'::uuid, 'ALT', 6),
        ('8410a83b-d09a-475d-a71c-1fcbcca94e58'::uuid, 'GLU', 7)
) AS analyte(test_guid, test_code, day_offset);

DO $seed$
DECLARE
    v_patient_id numeric(10, 0);
    v_person_id numeric(10, 0);
    v_test_id numeric(10, 0);
    v_test_section_id numeric(10, 0);
    v_sample_type_id numeric(10, 0);
    v_sample_status_id numeric(10, 0);
    v_sample_item_status_id numeric(10, 0);
    v_finalized_status_id numeric(10, 0);
    v_sample_id numeric(10, 0);
    v_sample_item_id numeric(10, 0);
    v_analysis_id numeric(10, 0);
    v_result_id numeric(10, 0);
    v_patient record;
    v_row record;
    v_patient_uuid uuid;
    v_sample_uuid uuid;
    v_specimen_uuid uuid;
    v_analysis_uuid uuid;
    v_observation_uuid uuid;
BEGIN
    IF to_regclass('clinlims.patient') IS NULL
       OR to_regclass('clinlims.sample') IS NULL
       OR to_regclass('clinlims.analysis') IS NULL
       OR to_regclass('clinlims.result') IS NULL THEN
        RAISE EXCEPTION 'Catalyst cohort schema guard failed';
    END IF;

    SELECT id INTO STRICT v_sample_type_id
    FROM clinlims.type_of_sample
    WHERE display_key = 'sample.type.Sang' AND is_active = true;

    SELECT id INTO STRICT v_sample_status_id
    FROM clinlims.status_of_sample
    WHERE display_key = 'status.sample.finished' AND is_active = 'Y';

    SELECT id INTO STRICT v_sample_item_status_id
    FROM clinlims.status_of_sample
    WHERE id = 20 AND status_type = 'SAMPLE'
      AND name = 'SampleEntered' AND is_active = 'Y';

    SELECT id INTO STRICT v_finalized_status_id
    FROM clinlims.status_of_sample
    WHERE display_key = 'status.test.valid'
      AND status_type = 'ANALYSIS' AND is_active = 'Y';

    IF EXISTS (
        SELECT fixture.test_guid
        FROM (SELECT DISTINCT test_guid FROM catalyst_fixture_result) AS fixture
        LEFT JOIN clinlims.test ON test.guid = fixture.test_guid::text
                               AND test.is_active = 'Y'
        WHERE test.id IS NULL
    ) THEN
        RAISE EXCEPTION 'Catalyst cohort references a missing or inactive test GUID';
    END IF;

    IF EXISTS (
        SELECT fixture.test_guid
        FROM (SELECT DISTINCT test_guid FROM catalyst_fixture_result) AS fixture
        LEFT JOIN catalyst_fixture_test_terminology AS terminology
          ON terminology.test_guid = fixture.test_guid
        WHERE terminology.test_guid IS NULL
    ) THEN
        RAISE EXCEPTION 'Catalyst cohort test is missing fixture terminology';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM catalyst_fixture_test_terminology AS terminology
        JOIN clinlims.test AS test ON test.guid = terminology.test_guid::text
        WHERE NULLIF(btrim(test.loinc), '') IS NOT NULL
          AND test.loinc <> terminology.loinc
    ) THEN
        RAISE EXCEPTION 'Catalyst fixture test has a conflicting LOINC code';
    END IF;

    UPDATE clinlims.test AS test
    SET loinc = terminology.loinc,
        lastupdated = GREATEST(
            COALESCE(test.lastupdated, timestamp '2026-07-16 00:00:00'),
            timestamp '2026-07-16 00:00:00'
        )
    FROM catalyst_fixture_test_terminology AS terminology
    WHERE test.guid = terminology.test_guid::text
      AND NULLIF(btrim(test.loinc), '') IS NULL;

    IF EXISTS (
        SELECT 1
        FROM catalyst_fixture_test_terminology AS terminology
        JOIN clinlims.test AS test ON test.guid = terminology.test_guid::text
        WHERE NULLIF(btrim(test.loinc), '') IS NULL
    ) THEN
        RAISE EXCEPTION 'Catalyst fixture test lacks an exportable LOINC code';
    END IF;

    FOR v_patient IN SELECT generate_series(1, 96) AS patient_index LOOP
        IF v_patient.patient_index = 1 THEN
            SELECT id, person_id, fhir_uuid
            INTO STRICT v_patient_id, v_person_id, v_patient_uuid
            FROM clinlims.patient
            WHERE external_id = 'CATALYST-DEMO-PATIENT-001';
        ELSE
            v_patient_uuid := pg_temp.fixture_uuid(
                'catalyst-cohort-v1-patient-' || v_patient.patient_index
            );
            SELECT id, person_id INTO v_patient_id, v_person_id
            FROM clinlims.patient
            WHERE external_id = 'CATALYST-DEMO-PATIENT-'
                || lpad(v_patient.patient_index::text, 3, '0');

            IF v_patient_id IS NULL THEN
                v_person_id := nextval('clinlims.person_seq');
                INSERT INTO clinlims.person (
                    id, first_name, last_name, lastupdated
                ) VALUES (
                    v_person_id,
                    'Synthetic',
                    'Patient ' || lpad(v_patient.patient_index::text, 3, '0'),
                    timestamp '2026-01-01 00:00:00'
                );
                v_patient_id := nextval('clinlims.patient_seq');
                INSERT INTO clinlims.patient (
                    id, fhir_uuid, person_id, gender, birth_date,
                    external_id, lastupdated
                ) VALUES (
                    v_patient_id,
                    v_patient_uuid,
                    v_person_id,
                    CASE WHEN v_patient.patient_index % 2 = 0 THEN 'F' ELSE 'M' END,
                    date '1965-01-01'
                        + ((v_patient.patient_index * 173) % 14600),
                    'CATALYST-DEMO-PATIENT-'
                        || lpad(v_patient.patient_index::text, 3, '0'),
                    timestamp '2026-01-01 00:00:00'
                );
            ELSIF (SELECT fhir_uuid FROM clinlims.patient WHERE id = v_patient_id)
                    IS DISTINCT FROM v_patient_uuid THEN
                RAISE EXCEPTION 'Cohort patient % has unexpected FHIR UUID',
                    v_patient.patient_index;
            END IF;
        END IF;

        FOR v_row IN
            SELECT * FROM catalyst_fixture_result
            WHERE patient_index = v_patient.patient_index
            ORDER BY observed_at, test_code
        LOOP
            SELECT id, test_section_id
            INTO STRICT v_test_id, v_test_section_id
            FROM clinlims.test
            WHERE guid = v_row.test_guid::text AND is_active = 'Y';

            v_sample_uuid := pg_temp.fixture_uuid('sample-' || v_row.accession_number);
            v_specimen_uuid := pg_temp.fixture_uuid('specimen-' || v_row.accession_number);
            v_analysis_uuid := pg_temp.fixture_uuid('analysis-' || v_row.accession_number);
            v_observation_uuid := pg_temp.fixture_uuid('observation-' || v_row.accession_number);

            SELECT id INTO v_sample_id FROM clinlims.sample
            WHERE accession_number = v_row.accession_number;
            IF v_sample_id IS NULL THEN
                v_sample_id := nextval('clinlims.sample_seq');
                INSERT INTO clinlims.sample (
                    id, fhir_uuid, accession_number, domain, next_item_sequence,
                    revision, entered_date, received_date, collection_date,
                    status, released_date, sys_user_id, lastupdated, status_id
                ) VALUES (
                    v_sample_id, v_sample_uuid, v_row.accession_number,
                    'H', 2, 0,
                    v_row.observed_at - make_interval(mins => v_row.turnaround_minutes),
                    v_row.observed_at - make_interval(mins => v_row.turnaround_minutes),
                    v_row.observed_at - make_interval(mins => v_row.turnaround_minutes + 120),
                    v_sample_status_id::text, v_row.observed_at, 1, v_row.observed_at,
                    v_sample_status_id
                );
            END IF;

            UPDATE clinlims.sample
            SET status = v_sample_status_id::text,
                status_id = v_sample_status_id
            WHERE id = v_sample_id
              AND (
                  status IS DISTINCT FROM v_sample_status_id::text
                  OR status_id IS DISTINCT FROM v_sample_status_id
              );

            IF NOT EXISTS (
                SELECT 1 FROM clinlims.sample_human
                WHERE samp_id = v_sample_id AND patient_id = v_patient_id
            ) THEN
                INSERT INTO clinlims.sample_human (
                    id, samp_id, patient_id, lastupdated
                ) VALUES (
                    nextval('clinlims.sample_human_seq'),
                    v_sample_id, v_patient_id, v_row.observed_at
                );
            END IF;

            SELECT id INTO v_sample_item_id FROM clinlims.sample_item
            WHERE fhir_uuid = v_specimen_uuid;
            IF v_sample_item_id IS NULL THEN
                v_sample_item_id := nextval('clinlims.sample_item_seq');
                INSERT INTO clinlims.sample_item (
                    id, fhir_uuid, sort_order, samp_id, typeosamp_id, uom_id,
                    quantity, lastupdated, collection_date, received_date,
                    status_id, collector
                ) VALUES (
                    v_sample_item_id, v_specimen_uuid, 1, v_sample_id,
                    v_sample_type_id, NULL, 1, v_row.observed_at,
                    v_row.observed_at - make_interval(mins => v_row.turnaround_minutes + 120),
                    (v_row.observed_at - make_interval(mins => v_row.turnaround_minutes))
                        AT TIME ZONE 'America/New_York',
                    v_sample_item_status_id, 'Catalyst Synthetic Fixture'
                );
            END IF;

            SELECT id INTO v_analysis_id FROM clinlims.analysis
            WHERE fhir_uuid = v_analysis_uuid;
            IF v_analysis_id IS NULL THEN
                v_analysis_id := nextval('clinlims.analysis_seq');
                INSERT INTO clinlims.analysis (
                    id, fhir_uuid, sampitem_id, test_sect_id, test_id, revision,
                    status, started_date, completed_date, released_date,
                    is_reportable, analysis_type, lastupdated, status_id,
                    entry_date, referred_out, type_of_sample_name, corrected
                ) VALUES (
                    v_analysis_id, v_analysis_uuid, v_sample_item_id,
                    v_test_section_id, v_test_id, 0,
                    v_finalized_status_id::text,
                    v_row.observed_at - interval '30 minutes',
                    v_row.observed_at, v_row.observed_at,
                    'Y', 'NORMAL', v_row.observed_at, v_finalized_status_id,
                    v_row.observed_at - interval '30 minutes', false,
                    'Whole Blood', false
                );
            END IF;

            SELECT id INTO v_result_id FROM clinlims.result
            WHERE fhir_uuid = v_observation_uuid;
            IF v_result_id IS NULL THEN
                v_result_id := nextval('clinlims.result_seq');
                INSERT INTO clinlims.result (
                    id, fhir_uuid, analysis_id, sort_order, is_reportable,
                    result_type, value, lastupdated, significant_digits,
                    "grouping"
                ) VALUES (
                    v_result_id, v_observation_uuid, v_analysis_id, 1,
                    'Y', 'N', v_row.result_value::text,
                    v_row.observed_at, 2, 0
                );
            ELSIF (SELECT value FROM clinlims.result WHERE id = v_result_id)
                    IS DISTINCT FROM v_row.result_value::varchar THEN
                RAISE EXCEPTION 'Fixture result % changed value', v_row.accession_number;
            END IF;

            v_sample_id := NULL;
            v_sample_item_id := NULL;
            v_analysis_id := NULL;
            v_result_id := NULL;
        END LOOP;
    END LOOP;

    IF (SELECT count(*) FROM catalyst_fixture_result) <> 1149 THEN
        RAISE EXCEPTION 'Expected 1,149 cohort-extension results';
    END IF;
END
$seed$;

COMMIT;

SELECT
    count(DISTINCT patient.external_id) AS patients,
    count(*) AS results,
    count(DISTINCT test.name) AS test_types,
    min(analysis.completed_date) AS first_result,
    max(analysis.completed_date) AS last_result
FROM clinlims.patient AS patient
JOIN clinlims.sample_human AS sample_human ON sample_human.patient_id = patient.id
JOIN clinlims.sample AS sample ON sample.id = sample_human.samp_id
JOIN clinlims.sample_item AS sample_item ON sample_item.samp_id = sample.id
JOIN clinlims.analysis AS analysis ON analysis.sampitem_id = sample_item.id
JOIN clinlims.result AS result ON result.analysis_id = analysis.id
JOIN clinlims.test AS test ON test.id = analysis.test_id
WHERE patient.external_id LIKE 'CATALYST-DEMO-PATIENT-%'
  AND sample.accession_number LIKE 'CAT%';
