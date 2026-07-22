from fastapi import FastAPI

from src import gateway
from src.config import load_config


def test_gateway_exposes_chat_completions_endpoint():
    app = gateway.create_app()
    assert isinstance(app, FastAPI)
    paths = {route.path for route in app.router.routes}
    assert "/v1/chat/completions" in paths
    assert "/health" in paths


def test_config_builds_default_dataset_registry():
    config = load_config()
    ids = [dataset.dataset_id for dataset in config.datasets]
    assert config.default_dataset_id in ids
    default = next(d for d in config.datasets if d.dataset_id == config.default_dataset_id)
    assert default.analytics_dsn == config.analytics_dsn
    assert default.catalog_path == config.catalog_path


def test_datasets_endpoint_registered_and_lists_default():
    app = gateway.create_app()
    paths = {route.path for route in app.router.routes}
    assert "/v1/catalyst/datasets" in paths

    response = app.state.catalyst.datasets()
    assert response.status_code == 200
    body = response.body
    assert body["contractVersion"] == "catalyst.datasets.v1"
    ids = [dataset["id"] for dataset in body["datasets"]]
    assert body["defaultDatasetId"] in ids
