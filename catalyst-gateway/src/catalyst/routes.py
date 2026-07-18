from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .service import CatalystService, ServiceResponse


def _json_response(response: ServiceResponse) -> JSONResponse:
    return JSONResponse(status_code=response.status_code, content=response.body)


def _invalid_request(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "invalid_request", "message": message}},
    )


async def _request_object(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        payload = await request.json()
    except ValueError:
        return _invalid_request("Request body must be valid JSON.")
    if not isinstance(payload, dict):
        return _invalid_request("Request body must be a JSON object.")
    return payload


def install_catalyst_routes(app: FastAPI, service: CatalystService) -> None:
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return await service.readiness()

    @app.post("/v1/catalyst/queries")
    async def submit_query(request: Request) -> JSONResponse:
        payload = await _request_object(request)
        if isinstance(payload, JSONResponse):
            return payload
        return _json_response(await service.submit_question(payload))

    @app.get("/v1/catalyst/query-options")
    async def query_options() -> JSONResponse:
        return _json_response(await service.query_options())

    @app.get("/v1/catalyst/dataset")
    async def dataset_overview() -> JSONResponse:
        return _json_response(await service.dataset_overview())

    @app.get("/v1/catalyst/dataset/rows")
    async def dataset_rows(
        test_name: str | None = Query(default=None, alias="testName", min_length=1),
        patient_id: str | None = Query(default=None, alias="patientId", min_length=1),
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> JSONResponse:
        return _json_response(
            await service.dataset_rows(
                test_name=test_name,
                patient_id=patient_id,
                limit=limit,
                offset=offset,
            )
        )

    @app.post("/v1/catalyst/previews/{preview_id}/execute")
    async def execute_preview(preview_id: str, request: Request) -> JSONResponse:
        payload = await _request_object(request)
        if isinstance(payload, JSONResponse):
            return payload
        return _json_response(await service.execute_preview(preview_id, payload))

    @app.get("/v1/catalyst/executions/{preview_id}")
    async def poll_execution(
        preview_id: str,
        idempotency_key: str = Query(alias="idempotencyKey", min_length=1),
    ) -> JSONResponse:
        return _json_response(service.poll_execution(preview_id, idempotency_key))

    @app.post("/v1/catalyst/workbench/sessions")
    async def create_workbench_session(request: Request) -> JSONResponse:
        payload = await _request_object(request)
        if isinstance(payload, JSONResponse):
            return payload
        return _json_response(await service.create_workbench_session(payload))

    @app.get("/v1/catalyst/workbench/sessions/{session_id}")
    async def get_workbench_session(session_id: str) -> JSONResponse:
        return _json_response(service.get_workbench_session(session_id))

    @app.post("/v1/catalyst/workbench/sessions/{session_id}/versions")
    async def create_workbench_version(
        session_id: str,
        request: Request,
    ) -> JSONResponse:
        payload = await _request_object(request)
        if isinstance(payload, JSONResponse):
            return payload
        return _json_response(service.create_workbench_version(session_id, payload))

    @app.post("/v1/catalyst/workbench/versions/{version_id}/validate")
    async def validate_workbench_version(version_id: str) -> JSONResponse:
        return _json_response(service.validate_workbench_version(version_id))

    @app.post("/v1/catalyst/workbench/versions/{version_id}/execute")
    async def execute_workbench_version(
        version_id: str,
        request: Request,
    ) -> JSONResponse:
        payload = await _request_object(request)
        if isinstance(payload, JSONResponse):
            return payload
        return _json_response(
            await service.execute_workbench_version(version_id, payload)
        )

    @app.patch("/v1/catalyst/workbench/sessions/{session_id}/browser-state")
    async def update_workbench_browser_state(
        session_id: str,
        request: Request,
    ) -> JSONResponse:
        payload = await _request_object(request)
        if isinstance(payload, JSONResponse):
            return payload
        return _json_response(
            service.update_workbench_browser_state(session_id, payload)
        )
