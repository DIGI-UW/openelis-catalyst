\set ON_ERROR_STOP on

\if :{?openelis_version}
\else
  \echo 'ERROR: psql variable openelis_version is required'
  \quit 2
\endif

SELECT :'openelis_version' ~ '^3\.2\.1\.[0-9]+$' AS openelis_version_supported \gset
\if :openelis_version_supported
\else
  \echo 'ERROR: this demo seed supports OpenELIS 3.2.1.x only'
  \quit 3
\endif

BEGIN;
SET LOCAL search_path TO clinlims, public;

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
    v_row record;
BEGIN
    IF to_regclass('databasechangelog') IS NULL
       OR to_regclass('clinlims.patient') IS NULL
       OR to_regclass('clinlims.sample') IS NULL
       OR to_regclass('clinlims.sample_human') IS NULL
       OR to_regclass('clinlims.sample_item') IS NULL
       OR to_regclass('clinlims.analysis') IS NULL
       OR to_regclass('clinlims.result') IS NULL THEN
        RAISE EXCEPTION 'OpenELIS schema guard failed: required 3.2.1.x tables are missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM databasechangelog
        WHERE replace(filename, '\', '/') LIKE '%liquibase/3.2.x.x/%'
    ) THEN
        RAISE EXCEPTION 'OpenELIS schema guard failed: no 3.2.x.x changelog was applied';
    END IF;

    IF EXISTS (
        SELECT required.column_name
        FROM (
            VALUES
                ('patient', 'fhir_uuid'),
                ('sample', 'fhir_uuid'),
                ('sample_item', 'fhir_uuid'),
                ('analysis', 'fhir_uuid'),
                ('result', 'fhir_uuid'),
                ('analysis', 'status_id'),
                ('sample_item', 'status_id'),
                ('sample_item', 'received_date')
        ) AS required(table_name, column_name)
        WHERE NOT EXISTS (
            SELECT 1
            FROM information_schema.columns AS present
            WHERE present.table_schema = 'clinlims'
              AND present.table_name = required.table_name
              AND present.column_name = required.column_name
        )
    ) THEN
        RAISE EXCEPTION 'OpenELIS schema guard failed: required FHIR/status columns are missing';
    END IF;

    SELECT id, test_section_id
    INTO STRICT v_test_id, v_test_section_id
    FROM clinlims.test
    WHERE guid = 'b50d156e-0f6f-40cd-921c-4e831602a623'
      AND is_active = 'Y'
      AND lower(name) = 'viral load'
      AND uom_id IN (
          SELECT id
          FROM clinlims.unit_of_measure
          WHERE lower(name) = 'copies/ml'
      );

    SELECT id
    INTO STRICT v_sample_type_id
    FROM clinlims.type_of_sample
    WHERE display_key = 'sample.type.Sang'
      AND is_active = true;

    SELECT id INTO STRICT v_sample_status_id
    FROM clinlims.status_of_sample
    WHERE display_key = 'status.sample.finished'
      AND is_active = 'Y';

    SELECT id INTO STRICT v_sample_item_status_id
    FROM clinlims.status_of_sample
    WHERE id = 20
      AND status_type = 'SAMPLE'
      AND name = 'SampleEntered'
      AND is_active = 'Y';

    SELECT id INTO STRICT v_finalized_status_id
    FROM clinlims.status_of_sample
    WHERE display_key = 'status.test.valid'
      AND status_type = 'ANALYSIS'
      AND is_active = 'Y';

    IF (SELECT count(*) FROM clinlims.patient WHERE external_id = 'CATALYST-DEMO-PATIENT-001') > 1 THEN
        RAISE EXCEPTION 'Seed marker CATALYST-DEMO-PATIENT-001 is not unique';
    END IF;

    SELECT id, person_id
    INTO v_patient_id, v_person_id
    FROM clinlims.patient
    WHERE external_id = 'CATALYST-DEMO-PATIENT-001';

    IF v_patient_id IS NULL THEN
        v_person_id := nextval('clinlims.person_seq');
        INSERT INTO clinlims.person (
            id, first_name, last_name, lastupdated
        ) VALUES (
            v_person_id, 'Catalyst', 'Demo', timestamp '2026-01-15 12:00:00'
        );

        v_patient_id := nextval('clinlims.patient_seq');
        INSERT INTO clinlims.patient (
            id, fhir_uuid, person_id, gender, birth_date, external_id, lastupdated
        ) VALUES (
            v_patient_id,
            '11111111-1111-4111-8111-111111111111',
            v_person_id,
            'F',
            timestamp '1990-01-01 00:00:00',
            'CATALYST-DEMO-PATIENT-001',
            timestamp '2026-01-15 12:00:00'
        );
    ELSE
        IF (SELECT fhir_uuid FROM clinlims.patient WHERE id = v_patient_id) IS NULL THEN
            UPDATE clinlims.patient
            SET fhir_uuid = '11111111-1111-4111-8111-111111111111'
            WHERE id = v_patient_id;
        ELSIF (SELECT fhir_uuid::text FROM clinlims.patient WHERE id = v_patient_id)
              <> '11111111-1111-4111-8111-111111111111' THEN
            RAISE EXCEPTION 'Existing Catalyst demo patient has an unexpected FHIR UUID';
        END IF;
    END IF;

    FOR v_row IN
        SELECT *
        FROM (
            VALUES
                ('CATVL0001', timestamp '2026-01-15 09:00:00', 1200::numeric, '21111111-1111-4111-8111-111111111111'::uuid, '31111111-1111-4111-8111-111111111111'::uuid, '41111111-1111-4111-8111-111111111111'::uuid, '51111111-1111-4111-8111-111111111111'::uuid),
                ('CATVL0002', timestamp '2026-02-15 09:00:00', 450::numeric, '22222222-2222-4222-8222-222222222222'::uuid, '32222222-2222-4222-8222-222222222222'::uuid, '42222222-2222-4222-8222-222222222222'::uuid, '52222222-2222-4222-8222-222222222222'::uuid),
                ('CATVL0003', timestamp '2026-03-15 09:00:00', 80::numeric, '23333333-3333-4333-8333-333333333333'::uuid, '33333333-3333-4333-8333-333333333333'::uuid, '43333333-3333-4333-8333-333333333333'::uuid, '53333333-3333-4333-8333-333333333333'::uuid)
        ) AS fixture(
            accession_number,
            observed_at,
            result_value,
            sample_fhir_uuid,
            specimen_fhir_uuid,
            analysis_fhir_uuid,
            observation_fhir_uuid
        )
    LOOP
        SELECT id
        INTO v_sample_id
        FROM clinlims.sample
        WHERE accession_number = v_row.accession_number;

        IF v_sample_id IS NULL THEN
            v_sample_id := nextval('clinlims.sample_seq');
            INSERT INTO clinlims.sample (
                id, fhir_uuid, accession_number, domain, next_item_sequence,
                revision, entered_date, received_date, collection_date,
                status, released_date, sys_user_id, lastupdated, status_id
            ) VALUES (
                v_sample_id, v_row.sample_fhir_uuid, v_row.accession_number,
                'H', 2, 0, v_row.observed_at - interval '2 hours',
                v_row.observed_at - interval '1 hour',
                v_row.observed_at - interval '3 hours',
                'F', v_row.observed_at, 1, v_row.observed_at,
                v_sample_status_id
            );
        ELSIF (SELECT fhir_uuid FROM clinlims.sample WHERE id = v_sample_id) IS DISTINCT FROM v_row.sample_fhir_uuid THEN
            RAISE EXCEPTION 'Existing seed sample % has an unexpected FHIR UUID', v_row.accession_number;
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM clinlims.sample_human
            WHERE samp_id = v_sample_id
              AND patient_id = v_patient_id
        ) THEN
            IF EXISTS (
                SELECT 1
                FROM clinlims.sample_human
                WHERE samp_id = v_sample_id
                  AND patient_id IS DISTINCT FROM v_patient_id
            ) THEN
                RAISE EXCEPTION 'Existing seed sample % belongs to another patient', v_row.accession_number;
            END IF;

            INSERT INTO clinlims.sample_human (
                id, samp_id, patient_id, lastupdated
            ) VALUES (
                nextval('clinlims.sample_human_seq'),
                v_sample_id,
                v_patient_id,
                v_row.observed_at
            );
        END IF;

        SELECT id
        INTO v_sample_item_id
        FROM clinlims.sample_item
        WHERE fhir_uuid = v_row.specimen_fhir_uuid;

        IF v_sample_item_id IS NULL THEN
            v_sample_item_id := nextval('clinlims.sample_item_seq');
            INSERT INTO clinlims.sample_item (
                id, fhir_uuid, sort_order, samp_id, typeosamp_id, uom_id,
                quantity, lastupdated, collection_date, received_date,
                status_id, collector
            ) VALUES (
                v_sample_item_id, v_row.specimen_fhir_uuid, 1, v_sample_id,
                v_sample_type_id, NULL, 1, v_row.observed_at,
                v_row.observed_at - interval '3 hours',
                (v_row.observed_at - interval '1 hour')
                    AT TIME ZONE 'America/New_York',
                v_sample_item_status_id, 'Catalyst Demo'
            );
        ELSIF (SELECT samp_id FROM clinlims.sample_item WHERE id = v_sample_item_id) IS DISTINCT FROM v_sample_id THEN
            RAISE EXCEPTION 'Existing seed specimen % belongs to another sample', v_row.specimen_fhir_uuid;
        END IF;

        UPDATE clinlims.sample_item
        SET received_date = (
            v_row.observed_at - interval '1 hour'
        ) AT TIME ZONE 'America/New_York'
        WHERE id = v_sample_item_id
          AND received_date IS DISTINCT FROM (
              v_row.observed_at - interval '1 hour'
          ) AT TIME ZONE 'America/New_York';

        SELECT id
        INTO v_analysis_id
        FROM clinlims.analysis
        WHERE fhir_uuid = v_row.analysis_fhir_uuid;

        IF v_analysis_id IS NULL THEN
            v_analysis_id := nextval('clinlims.analysis_seq');
            INSERT INTO clinlims.analysis (
                id, fhir_uuid, sampitem_id, test_sect_id, test_id, revision,
                status, started_date, completed_date, released_date,
                is_reportable, analysis_type, lastupdated, status_id,
                entry_date, referred_out, type_of_sample_name, corrected
            ) VALUES (
                v_analysis_id, v_row.analysis_fhir_uuid, v_sample_item_id,
                v_test_section_id, v_test_id, 0, v_finalized_status_id::text,
                v_row.observed_at - interval '30 minutes',
                v_row.observed_at, v_row.observed_at, 'Y', 'NORMAL',
                v_row.observed_at, v_finalized_status_id,
                v_row.observed_at - interval '30 minutes', false,
                'Whole Blood', false
            );
        ELSIF (SELECT sampitem_id FROM clinlims.analysis WHERE id = v_analysis_id) IS DISTINCT FROM v_sample_item_id THEN
            RAISE EXCEPTION 'Existing seed analysis % belongs to another specimen', v_row.analysis_fhir_uuid;
        END IF;

        UPDATE clinlims.analysis
        SET status = v_finalized_status_id::text,
            status_id = v_finalized_status_id
        WHERE id = v_analysis_id
          AND (
              status IS DISTINCT FROM v_finalized_status_id::text
              OR status_id IS DISTINCT FROM v_finalized_status_id
          );

        SELECT id
        INTO v_result_id
        FROM clinlims.result
        WHERE fhir_uuid = v_row.observation_fhir_uuid;

        IF v_result_id IS NULL THEN
            v_result_id := nextval('clinlims.result_seq');
            INSERT INTO clinlims.result (
                id, fhir_uuid, analysis_id, sort_order, is_reportable,
                result_type, value, lastupdated, significant_digits, "grouping"
            ) VALUES (
                v_result_id, v_row.observation_fhir_uuid, v_analysis_id, 1,
                'Y', 'N', v_row.result_value::text, v_row.observed_at, 0, 0
            );
        ELSIF (
            SELECT analysis_id
            FROM clinlims.result
            WHERE id = v_result_id
        ) IS DISTINCT FROM v_analysis_id
        OR (
            SELECT value
            FROM clinlims.result
            WHERE id = v_result_id
        ) IS DISTINCT FROM v_row.result_value::varchar THEN
            RAISE EXCEPTION 'Existing seed Observation % has different analysis/value data',
                v_row.observation_fhir_uuid;
        END IF;

        v_sample_id := NULL;
        v_sample_item_id := NULL;
        v_analysis_id := NULL;
        v_result_id := NULL;
    END LOOP;

    IF (
        SELECT count(*)
        FROM clinlims.result
        WHERE fhir_uuid IN (
            '51111111-1111-4111-8111-111111111111',
            '52222222-2222-4222-8222-222222222222',
            '53333333-3333-4333-8333-333333333333'
        )
          AND value::numeric IN (1200, 450, 80)
    ) <> 3 THEN
        RAISE EXCEPTION 'Seed verification failed: expected three fixed viral-load results';
    END IF;
END
$seed$;

COMMIT;

SELECT
    patient.external_id,
    sample.accession_number,
    analysis.completed_date AS observed_at,
    result.value::numeric AS viral_load_copies_per_ml
FROM clinlims.patient AS patient
JOIN clinlims.sample_human AS sample_human
  ON sample_human.patient_id = patient.id
JOIN clinlims.sample AS sample
  ON sample.id = sample_human.samp_id
JOIN clinlims.sample_item AS sample_item
  ON sample_item.samp_id = sample.id
JOIN clinlims.analysis AS analysis
  ON analysis.sampitem_id = sample_item.id
JOIN clinlims.result AS result
  ON result.analysis_id = analysis.id
WHERE patient.external_id = 'CATALYST-DEMO-PATIENT-001'
  AND sample.accession_number IN ('CATVL0001', 'CATVL0002', 'CATVL0003')
ORDER BY analysis.completed_date;
