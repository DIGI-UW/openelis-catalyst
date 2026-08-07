DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'catalyst_readonly'
    ) THEN
        CREATE ROLE catalyst_readonly
            LOGIN PASSWORD 'demo-readonly-change-me';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE catalyst_analytics TO catalyst_readonly;
ALTER ROLE catalyst_readonly SET default_transaction_read_only = on;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM catalyst_readonly;
GRANT USAGE ON SCHEMA public TO catalyst_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO catalyst_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE catalyst_analytics_writer
    IN SCHEMA public GRANT SELECT ON TABLES TO catalyst_readonly;
