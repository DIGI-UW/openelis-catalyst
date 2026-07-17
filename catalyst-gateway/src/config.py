import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "analytics"
    / "catalog"
    / "analytics-catalog-v1.json"
)


@dataclass(frozen=True)
class GatewayConfig:
    router_url: str
    hub_base_url: str
    analytics_dsn: str
    catalog_path: str
    preview_store_path: str
    max_rows: int
    statement_timeout_ms: int
    execution_lease_seconds: int
    hub_timeout_seconds: float


def load_config() -> GatewayConfig:
    return GatewayConfig(
        router_url=os.getenv("CATALYST_ROUTER_URL", "http://localhost:9100"),
        hub_base_url=os.getenv("MED_AGENT_HUB_BASE_URL", "http://localhost:8082"),
        analytics_dsn=os.getenv(
            "CATALYST_ANALYTICS_DSN",
            "postgresql://catalyst_readonly:demo-readonly-change-me"
            "@localhost:15433/catalyst_analytics",
        ),
        catalog_path=os.getenv("CATALYST_CATALOG_PATH", str(DEFAULT_CATALOG_PATH)),
        preview_store_path=os.getenv(
            "CATALYST_PREVIEW_STORE_PATH",
            "/tmp/catalyst-gateway-previews.sqlite3",
        ),
        max_rows=int(os.getenv("CATALYST_QUERY_MAX_ROWS", "500")),
        statement_timeout_ms=int(os.getenv("CATALYST_STATEMENT_TIMEOUT_MS", "10000")),
        execution_lease_seconds=int(
            os.getenv("CATALYST_EXECUTION_LEASE_SECONDS", "60")
        ),
        hub_timeout_seconds=float(os.getenv("CATALYST_HUB_TIMEOUT_SECONDS", "360")),
    )
