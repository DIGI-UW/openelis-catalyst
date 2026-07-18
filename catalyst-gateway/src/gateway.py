from contextlib import asynccontextmanager

from fastapi import FastAPI

from .a2a_client import A2AClient
from .catalyst.analytics import PostgresAnalyticsAdapter
from .catalyst.catalog import Catalog
from .catalyst.contracts import ContractRegistry
from .catalyst.hub import HubClient
from .catalyst.policy import SqlPolicy
from .catalyst.routes import install_catalyst_routes
from .catalyst.service import CatalystService
from .catalyst.storage import PreviewStore, WorkbenchStore
from .config import load_config


def _default_catalyst_service() -> CatalystService:
    config = load_config()
    contracts = ContractRegistry.default()
    catalog = Catalog.load(config.catalog_path)
    return CatalystService(
        contracts=contracts,
        catalog=catalog,
        hub=HubClient(
            config.hub_base_url,
            contracts,
            timeout_seconds=config.hub_timeout_seconds,
        ),
        analytics=PostgresAnalyticsAdapter(
            config.analytics_dsn,
            data_source_id=catalog.data_source,
        ),
        store=PreviewStore(
            config.preview_store_path,
            execution_lease_seconds=config.execution_lease_seconds,
        ),
        sql_policy=SqlPolicy(max_rows=config.max_rows),
        max_rows=config.max_rows,
        statement_timeout_ms=config.statement_timeout_ms,
        workbench_store=WorkbenchStore(config.preview_store_path),
    )


def create_app(*, catalyst_service: CatalystService | None = None) -> FastAPI:
    config = load_config()
    client = A2AClient(config.router_url)
    catalyst = catalyst_service or _default_catalyst_service()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await catalyst.aclose()
            await client.aclose()

    app = FastAPI(
        title="Catalyst Gateway",
        version="0.0.1",
        lifespan=lifespan,
    )
    app.state.catalyst = catalyst
    app.state.a2a_client = client
    install_catalyst_routes(app, catalyst)

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict) -> dict:
        return await client.send_chat_completion(payload)

    return app


app = create_app()
