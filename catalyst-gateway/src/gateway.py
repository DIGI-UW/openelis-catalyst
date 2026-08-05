from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from .a2a_client import A2AClient
from .catalyst.analytics import PostgresAnalyticsAdapter
from .catalyst.catalog import Catalog
from .catalyst.contracts import ContractRegistry
from .catalyst.dashboard_builder import DashboardBuilder
from .catalyst.dashboard_routes import install_dashboard_routes
from .catalyst.local_hub import LocalHub
from .catalyst.policy import SqlPolicy
from .catalyst.routes import install_catalyst_routes
from .catalyst.service import CatalystService, DataSourceBundle
from .catalyst.storage import PreviewStore, WorkbenchStore
from .config import load_config


def _default_catalyst_service() -> CatalystService:
    config = load_config()
    contracts = ContractRegistry.default()
    catalog = Catalog.load(config.catalog_path)
    analytics = PostgresAnalyticsAdapter(
        config.analytics_dsn,
        data_source_id=catalog.data_source,
        dataset_browser=catalog.dataset_browser,
    )
    bundles: list[DataSourceBundle] = []
    for source in config.data_sources:
        if source.source_id == config.default_data_source_id:
            bundles.append(
                DataSourceBundle(
                    source_id=source.source_id,
                    label=source.label,
                    catalog=catalog,
                    analytics=analytics,
                )
            )
            continue
        if not Path(source.catalog_path).is_file():
            # Registered but not provisioned yet: list as unavailable rather
            # than fail boot; it cannot be targeted until its catalog exists.
            bundles.append(
                DataSourceBundle(
                    source_id=source.source_id,
                    label=source.label,
                    available=False,
                )
            )
            continue
        source_catalog = Catalog.load(source.catalog_path)
        bundles.append(
            DataSourceBundle(
                source_id=source.source_id,
                label=source.label,
                catalog=source_catalog,
                analytics=PostgresAnalyticsAdapter(
                    source.analytics_dsn,
                    data_source_id=source_catalog.data_source,
                    dataset_browser=source_catalog.dataset_browser,
                ),
            )
        )
    return CatalystService(
        contracts=contracts,
        # Orchestration now runs in-process (the gateway owns the governed-query
        # engine); the hub is called only as a generic model executor from inside
        # the engine. LocalHub implements the same interface the service expects.
        hub=LocalHub(hub_base_url=config.hub_base_url),
        store=PreviewStore(
            config.preview_store_path,
            execution_lease_seconds=config.execution_lease_seconds,
        ),
        sql_policy=SqlPolicy(max_rows=config.max_rows),
        max_rows=config.max_rows,
        statement_timeout_ms=config.statement_timeout_ms,
        workbench_store=WorkbenchStore(
            config.preview_store_path,
            execution_lease_seconds=config.execution_lease_seconds,
        ),
        data_sources=tuple(bundles),
        default_data_source_id=config.default_data_source_id,
        default_query_profile_id=config.default_query_profile_id,
    )


def create_app(
    *,
    catalyst_service: CatalystService | None = None,
    dashboard_builder: DashboardBuilder | None = None,
) -> FastAPI:
    config = load_config()
    client = A2AClient(config.router_url)
    catalyst = catalyst_service or _default_catalyst_service()
    builder = dashboard_builder or DashboardBuilder(
        config.preview_store_path,
        workbench=catalyst.workbench_store,
        outbox=config.superset_outbox_path,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await catalyst.aclose()
            builder.close()
            await client.aclose()

    app = FastAPI(
        title="Catalyst Gateway",
        version="0.0.1",
        lifespan=lifespan,
    )
    app.state.catalyst = catalyst
    app.state.a2a_client = client
    install_catalyst_routes(app, catalyst)
    install_dashboard_routes(app, builder)

    @app.post("/v1/chat/completions")
    async def chat_completions(payload: dict) -> dict:
        return await client.send_chat_completion(payload)

    return app


app = create_app()
