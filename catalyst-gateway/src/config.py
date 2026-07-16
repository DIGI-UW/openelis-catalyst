import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    router_url: str
    hub_base_url: str
    analytics_dsn: str
    preview_store_path: str
    max_rows: int
    statement_timeout_ms: int
    preview_ttl_seconds: int
    hub_timeout_seconds: float


def load_config() -> GatewayConfig:
    return GatewayConfig(
        router_url=os.getenv("CATALYST_ROUTER_URL", "http://localhost:9100"),
        hub_base_url=os.getenv("MED_AGENT_HUB_BASE_URL", "http://localhost:8080"),
        analytics_dsn=os.getenv(
            "CATALYST_ANALYTICS_DSN",
            "postgresql://catalyst_readonly@localhost:5432/openelis_analytics",
        ),
        preview_store_path=os.getenv(
            "CATALYST_PREVIEW_STORE_PATH",
            "/tmp/catalyst-gateway-previews.sqlite3",
        ),
        max_rows=int(os.getenv("CATALYST_QUERY_MAX_ROWS", "500")),
        statement_timeout_ms=int(os.getenv("CATALYST_STATEMENT_TIMEOUT_MS", "10000")),
        preview_ttl_seconds=int(os.getenv("CATALYST_PREVIEW_TTL_SECONDS", "300")),
        hub_timeout_seconds=float(os.getenv("CATALYST_HUB_TIMEOUT_SECONDS", "30")),
    )
