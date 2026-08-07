from fastapi import FastAPI

from src import gateway
from src.config import load_config


def test_gateway_exposes_only_catalyst_product_endpoints():
    app = gateway.create_app()
    assert isinstance(app, FastAPI)
    paths = {route.path for route in app.router.routes}
    assert "/v1/chat/completions" not in paths
    assert "/health" in paths
    assert "/v1/catalyst/workbench/sessions" in paths


def test_config_builds_default_data_source_registry():
    config = load_config()
    ids = [source.source_id for source in config.data_sources]
    assert config.default_data_source_id in ids
    default = next(
        s for s in config.data_sources if s.source_id == config.default_data_source_id
    )
    assert default.analytics_dsn == config.analytics_dsn
    assert default.catalog_path == config.catalog_path


def test_data_sources_endpoint_registered_and_lists_default():
    app = gateway.create_app()
    paths = {route.path for route in app.router.routes}
    assert "/v1/catalyst/data-sources" in paths

    response = app.state.catalyst.data_sources()
    assert response.status_code == 200
    body = response.body
    assert body["contractVersion"] == "catalyst.data-sources.v1"
    ids = [source["id"] for source in body["dataSources"]]
    assert body["defaultDataSourceId"] in ids
