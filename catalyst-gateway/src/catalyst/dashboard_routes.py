"""HTTP boundary for the supervised local dashboard builder."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .dashboard_builder import DashboardBuilder, DashboardBuilderError


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


async def _body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return _error(400, "invalid_request", "Request body must be valid JSON.")
    if not isinstance(payload, dict):
        return _error(400, "invalid_request", "Request body must be a JSON object.")
    return payload


def _collection(kind: str, entities: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "contractVersion": "catalyst.dashboard-builder.v1",
        "kind": kind,
        "items": entities,
    }


def install_dashboard_routes(app: FastAPI, builder: DashboardBuilder) -> None:
    root = "/v1/catalyst/dashboard-builder"

    @app.get(f"{root}/datasets")
    async def list_datasets() -> dict[str, Any]:
        return _collection("dataset", builder.list("dataset"))

    @app.post(f"{root}/datasets")
    async def save_dataset(request: Request) -> JSONResponse:
        payload = await _body(request)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            session_id = str(payload["sessionId"])
            execution_id = str(payload["executionId"])
            title = str(payload.get("title") or "")
            entity = builder.save_dataset(
                session_id=session_id, execution_id=execution_id, title=title
            )
        except KeyError as error:
            return _error(400, "invalid_request", f"Missing field: {error.args[0]}.")
        except DashboardBuilderError as error:
            return _error(422, "dataset_not_saveable", str(error))
        return JSONResponse(status_code=201, content=entity)

    @app.get(f"{root}/widgets")
    async def list_widgets() -> dict[str, Any]:
        return _collection("widget", builder.list("widget"))

    @app.post(f"{root}/widgets")
    async def save_widget(request: Request) -> JSONResponse:
        payload = await _body(request)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            entity = builder.save_widget(
                dataset_version_id=str(payload["datasetVersionId"]),
                title=str(payload.get("title") or ""),
                presentation_kind=(
                    str(payload["presentationKind"])
                    if payload.get("presentationKind") is not None
                    else None
                ),
            )
        except KeyError as error:
            return _error(400, "invalid_request", f"Missing field: {error.args[0]}.")
        except DashboardBuilderError as error:
            return _error(422, "widget_not_saveable", str(error))
        return JSONResponse(status_code=201, content=entity)

    @app.get(f"{root}/dashboards")
    async def list_dashboards() -> dict[str, Any]:
        return _collection("dashboard", builder.list("dashboard"))

    @app.post(f"{root}/dashboards")
    async def save_dashboard(request: Request) -> JSONResponse:
        payload = await _body(request)
        if isinstance(payload, JSONResponse):
            return payload
        try:
            widget_version_ids = payload["widgetVersionIds"]
            if not isinstance(widget_version_ids, list) or not all(
                isinstance(item, str) and item for item in widget_version_ids
            ):
                return _error(
                    400, "invalid_request", "widgetVersionIds must be a list of IDs."
                )
            entity = builder.save_dashboard(
                title=str(payload.get("title") or ""),
                widget_version_ids=widget_version_ids,
            )
        except KeyError as error:
            return _error(400, "invalid_request", f"Missing field: {error.args[0]}.")
        except DashboardBuilderError as error:
            return _error(422, "dashboard_not_saveable", str(error))
        return JSONResponse(status_code=201, content=entity)

    @app.post(f"{root}/dashboards/{{dashboard_version_id}}/publish")
    async def publish_dashboard(dashboard_version_id: str) -> JSONResponse:
        try:
            publication = builder.publish(dashboard_version_id)
        except DashboardBuilderError as error:
            return _error(422, "publication_not_saveable", str(error))
        return JSONResponse(status_code=201, content=publication)

    @app.get(f"{root}/dashboards/{{dashboard_version_id}}/publication")
    async def dashboard_publication(dashboard_version_id: str) -> JSONResponse:
        publication = builder.publication(dashboard_version_id)
        if publication is None:
            return _error(
                404,
                "publication_not_found",
                "No bundle was published for this dashboard version.",
            )
        return JSONResponse(status_code=200, content=publication)

    @app.get(f"{root}/dashboards/{{dashboard_id}}/bundle")
    async def download_bundle(dashboard_id: str) -> Response:
        dashboards = [
            item for item in builder.list("dashboard") if item["id"] == dashboard_id
        ]
        if not dashboards:
            return _error(404, "dashboard_not_found", "Dashboard was not found.")
        publication = builder.publication(dashboards[0]["versionId"])
        if publication is None:
            return _error(
                404,
                "publication_not_found",
                "No bundle was published for this dashboard.",
            )
        filename = publication["pointer"]["bundle"]["fileName"]
        path = builder.outbox / filename
        if not path.is_file():
            return _error(
                404,
                "bundle_not_found",
                "Published bundle is not present in the outbox.",
            )
        return FileResponse(path, media_type="application/zip", filename=filename)
