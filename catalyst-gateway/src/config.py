import json
import os
from dataclasses import dataclass
from pathlib import Path

from .catalyst.request import QUERY_PROFILE_ID


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "analytics"
    / "catalog"
    / "analytics-catalog-v1.json"
)


@dataclass(frozen=True)
class DataSourceConfig:
    """One queryable data source: its own analytics DB (DSN) and catalog."""

    source_id: str
    label: str
    analytics_dsn: str
    catalog_path: str


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
    data_sources: tuple[DataSourceConfig, ...]
    default_data_source_id: str
    default_query_profile_id: str


def _load_extra_data_sources() -> tuple[DataSourceConfig, ...]:
    """Additional data sources registered via CATALYST_DATA_SOURCES_PATH (JSON).

    Shape: {"dataSources": [{"id", "label", "analyticsDsn", "catalogPath"}, ...]}.
    Unset env => no extra sources (single-source back-compat). A path that is
    set but points at nothing is an operator error and fails boot loudly.
    """
    path = os.getenv("CATALYST_DATA_SOURCES_PATH")
    if not path:
        return ()
    if not Path(path).is_file():
        raise FileNotFoundError(
            f"CATALYST_DATA_SOURCES_PATH is set to {path!r} but no such file "
            "exists; fix the path or unset the variable."
        )
    raw = json.loads(Path(path).read_text())
    extras: list[DataSourceConfig] = []
    for entry in raw.get("dataSources", []):
        extras.append(
            DataSourceConfig(
                source_id=str(entry["id"]),
                label=str(entry.get("label", entry["id"])),
                analytics_dsn=str(entry["analyticsDsn"]),
                catalog_path=str(entry["catalogPath"]),
            )
        )
    return tuple(extras)


def load_config() -> GatewayConfig:
    analytics_dsn = os.getenv(
        "CATALYST_ANALYTICS_DSN",
        "postgresql://catalyst_readonly:demo-readonly-change-me"
        "@localhost:15433/catalyst_analytics",
    )
    catalog_path = os.getenv("CATALYST_CATALOG_PATH", str(DEFAULT_CATALOG_PATH))
    default_source_id = "openelis"
    default_source = DataSourceConfig(
        source_id=default_source_id,
        label="OpenELIS Laboratory",
        analytics_dsn=analytics_dsn,
        catalog_path=catalog_path,
    )
    data_sources = (default_source, *_load_extra_data_sources())
    return GatewayConfig(
        router_url=os.getenv("CATALYST_ROUTER_URL", "http://localhost:9100"),
        hub_base_url=os.getenv("MED_AGENT_HUB_BASE_URL", "http://localhost:8082"),
        analytics_dsn=analytics_dsn,
        catalog_path=catalog_path,
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
        data_sources=data_sources,
        default_data_source_id=default_source_id,
        default_query_profile_id=os.getenv(
            "CATALYST_QUERY_PROFILE_ID", QUERY_PROFILE_ID
        ),
    )
