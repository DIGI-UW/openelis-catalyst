import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DatabaseConfig:
    """Database configuration for MCP server schema extraction."""

    host: str
    port: int
    database: str
    username: str
    password: str
    schema: str = "clinlims"  # OpenELIS default schema

    @property
    def connection_string(self) -> str:
        """Build PostgreSQL connection string."""
        return (
            f"postgresql://{self.username}:{self.password}@"
            f"{self.host}:{self.port}/{self.database}?options=-csearch_path={self.schema}"
        )


def load_database_config() -> Optional[DatabaseConfig]:
    """
    Load database configuration from environment variables.

    M0.0: Returns None (not used, mocks used instead)
    M1+: Returns config for real PostgreSQL connection
    """
    if not os.getenv("MCP_DB_ENABLED", "false").lower() == "true":
        return None

    return DatabaseConfig(
        host=os.getenv("MCP_DB_HOST", "db.openelis.org"),
        port=int(os.getenv("MCP_DB_PORT", "5432")),
        database=os.getenv("MCP_DB_NAME", "clinlims"),
        username=os.getenv("MCP_DB_USER", "catalyst_schema_reader"),
        password=os.getenv("MCP_DB_PASSWORD", ""),
        schema=os.getenv("MCP_DB_SCHEMA", "clinlims"),
    )


@dataclass(frozen=True)
class FhirConfig:
    """OE2 embedded FHIR provider configuration (feature 011).

    Primary data-access surface for the FHIR sidecar POC. Named "embedded" to
    distinguish it from OE2's separate HAPI FHIR sidecar container, which
    requires a client TLS certificate this POC does not provision (see
    specs/011-catalyst-fhir-sidecar-poc/research.md item 5) and is only used
    by the Story 4 parity probe, not this config.
    """

    base_url: str
    username: str
    password: str
    timeout_s: float
    verify_tls: bool = False  # local dev uses OE2's self-signed cert


def load_fhir_config() -> FhirConfig:
    return FhirConfig(
        base_url=os.getenv(
            "OE2_FHIR_BASE_URL", "https://localhost:18443/OpenELIS-Global/fhir"
        ).rstrip("/"),
        username=os.getenv("OE2_FHIR_USERNAME", "admin"),
        password=os.getenv("OE2_FHIR_PASSWORD", ""),
        timeout_s=float(os.getenv("OE2_FHIR_TIMEOUT_S", "15")),
        verify_tls=os.getenv("OE2_FHIR_VERIFY_TLS", "false").lower() == "true",
    )


@dataclass(frozen=True)
class HapiConfig:
    """OE2's HAPI FHIR sidecar (feature 011, Story 4 parity probe only).

    Not used by the answer path (see FhirConfig) — its TLS listener demands
    a client certificate this POC does not provision, so every read against
    it is expected to fail with a transport-layer error. The parity probe
    exists to record that failure as a documented, non-blocking gap-log
    entry rather than to actually retrieve data from it.
    """

    base_url: str
    timeout_s: float
    verify_tls: bool = False


def load_hapi_config() -> HapiConfig:
    return HapiConfig(
        base_url=os.getenv("OE2_HAPI_BASE_URL", "https://localhost:8444/fhir").rstrip("/"),
        timeout_s=float(os.getenv("OE2_HAPI_TIMEOUT_S", "10")),
        verify_tls=os.getenv("OE2_HAPI_VERIFY_TLS", "false").lower() == "true",
    )
