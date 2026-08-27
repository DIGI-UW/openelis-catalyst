import json
import os
from dataclasses import dataclass
from pathlib import Path

from .catalyst.request import QUERY_PROFILE_ID


@dataclass(frozen=True)
class DataSourceConfig:
    """One queryable source.

    A source is its identity, its label, how to connect, the SQL dialect it
    speaks, and which dialect adapter implements that grammar. There is no
    preferred engine and no fallback: an engine reaches Catalyst entirely
    through these values.
    """

    source_id: str
    label: str
    connection_uri: str
    dialect: str
    dialect_adapter: str


@dataclass(frozen=True)
class GatewayConfig:
    hub_base_url: str
    preview_store_path: str
    max_rows: int
    statement_timeout_ms: int
    execution_lease_seconds: int
    hub_timeout_seconds: float
    data_sources: tuple[DataSourceConfig, ...]
    default_data_source_id: str
    default_query_profile_id: str
    superset_outbox_path: str
    superset_receipts_path: str


def _data_source(entry: dict) -> DataSourceConfig:
    dialect = str(entry["dialect"])
    return DataSourceConfig(
        source_id=str(entry["id"]),
        label=str(entry.get("label", entry["id"])),
        connection_uri=str(entry["connectionUri"]),
        dialect=dialect,
        # A source may name its adapter explicitly; the common case is that the
        # adapter is the one implementing the dialect it declares.
        dialect_adapter=str(entry.get("dialectAdapter", dialect)),
    )


def _load_extra_data_sources() -> tuple[DataSourceConfig, ...]:
    """Additional sources registered via CATALYST_DATA_SOURCES_PATH (JSON).

    Shape: {"dataSources": [{"id", "label", "connectionUri", "dialect"}, ...]}.
    Unset env => no extra sources. A path that is set but points at nothing is
    an operator error and fails boot loudly.
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
    return tuple(_data_source(entry) for entry in raw.get("dataSources", []))


def load_config() -> GatewayConfig:
    default_source_id = os.getenv("CATALYST_DATA_SOURCE_ID", "openelis")
    default_source = DataSourceConfig(
        source_id=default_source_id,
        label=os.getenv("CATALYST_DATA_SOURCE_LABEL", "OpenELIS Laboratory"),
        connection_uri=os.getenv(
            "CATALYST_CONNECTION_URI",
            "hive2://catalyst@spark-thriftserver:10000/default",
        ),
        dialect=os.getenv("CATALYST_DIALECT", "spark"),
        dialect_adapter=os.getenv(
            "CATALYST_DIALECT_ADAPTER", os.getenv("CATALYST_DIALECT", "spark")
        ),
    )
    data_sources = (default_source, *_load_extra_data_sources())
    return GatewayConfig(
        hub_base_url=os.getenv("MED_AGENT_HUB_BASE_URL", "http://localhost:8082"),
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
        superset_outbox_path=os.getenv(
            "CATALYST_SUPERSET_OUTBOX", "/tmp/catalyst-superset-outbox"
        ),
        superset_receipts_path=os.getenv(
            "CATALYST_SUPERSET_RECEIPTS", "/tmp/catalyst-superset-receipts"
        ),
    )
