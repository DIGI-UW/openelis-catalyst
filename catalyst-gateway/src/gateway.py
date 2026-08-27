from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI

from .catalyst.analytics import SqlAnalyticsAdapter
from .catalyst.catalog import Catalog
from .catalyst.dialects import resolve_dialect_adapter
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
    bundles: list[DataSourceBundle] = []
    for source in config.data_sources:
        # Configuration selects; code implements. The Gateway resolves the
        # adapter a source names and hands it to the one connection
        # implementation -- it never asks which engine this is.
        adapter = resolve_dialect_adapter(source.dialect_adapter)
        bundles.append(
            DataSourceBundle(
                source_id=source.source_id,
                label=source.label,
                catalog=Catalog.for_source(
                    data_source=source.source_id,
                    dialect=source.dialect,
                ),
                analytics=SqlAnalyticsAdapter(
                    source.connection_uri,
                    dialect=adapter,
                    data_source_id=source.source_id,
                ),
            )
        )
    return CatalystService(
        contracts=contracts,
        # The gateway owns governed-query orchestration and asks Hub to execute
        # the named writer/reviewer roles from its configured query profile.
        # LocalHub implements the same interface the service expects.
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
    catalyst = catalyst_service or _default_catalyst_service()
    builder = dashboard_builder or DashboardBuilder(
        config.preview_store_path,
        # Production always configures this store. The cast retains the
        # existing narrow legacy-service test seam, whose routes never invoke
        # Dashboard Builder.
        workbench=cast(WorkbenchStore, catalyst.workbench_store),
        outbox=config.superset_outbox_path,
        receipts=config.superset_receipts_path,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await catalyst.aclose()
            builder.close()

    app = FastAPI(
        title="Catalyst Gateway",
        version="0.0.1",
        lifespan=lifespan,
    )
    app.state.catalyst = catalyst
    install_catalyst_routes(app, catalyst)
    install_dashboard_routes(app, builder)

    return app


app = create_app()
