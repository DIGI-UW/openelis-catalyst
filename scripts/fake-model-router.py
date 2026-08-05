#!/usr/bin/env python3
"""Deterministic OpenAI-compatible backend for MVP CI assembly tests."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


MODELS = tuple(
    model.strip()
    for model in os.getenv(
        "MODEL_ROUTER_MODEL_IDS",
        "gemma-4-12b,gemma-e4b,qwen2.5-14b,qwen2.5-coder-1.5b-instruct-q4_k_m",
    ).split(",")
    if model.strip()
)
MODEL = MODELS[0]


def generation(payload: dict[str, Any]) -> dict[str, Any]:
    request = json.loads(payload["messages"][-1]["content"])
    target = request["target"]
    views = [view["name"] for view in request["catalog"]["views"]]
    max_rows = request["policy"]["maxRows"]
    return {
        "status": "ready",
        "target": {**target, "approvedViews": views},
        "sql": (
            "SELECT result_value, result_unit, issued_at, "
            "specimen_received_at, receipt_to_release_minutes "
            "FROM analytics.lab_result_fact_v1 "
            "WHERE test_name = :test_name AND observed_at >= :start_at "
            f"ORDER BY observed_at LIMIT {max_rows}"
        ),
        "parameters": [
            {
                "name": "test_name",
                "type": "string",
                "source": "question",
                "value": "Viral Load",
            },
            {
                "name": "start_at",
                "type": "date",
                "source": "question",
                "value": "2026-01-01",
            }
        ],
        "expectedColumns": [
            {
                "name": "result_value",
                "logicalType": "decimal",
                "nullable": True,
            },
            {
                "name": "result_unit",
                "logicalType": "string",
                "nullable": True,
            },
            {
                "name": "issued_at",
                "logicalType": "date-time",
                "nullable": True,
            },
            {
                "name": "specimen_received_at",
                "logicalType": "date-time",
                "nullable": True,
            },
            {
                "name": "receipt_to_release_minutes",
                "logicalType": "decimal",
                "nullable": True,
            },
        ],
    }


def completion(payload: dict[str, Any]) -> dict[str, Any]:
    response_format = payload.get("response_format") or {}
    schema_name = (response_format.get("json_schema") or {}).get("name")
    if schema_name == "catalyst_query_candidate":
        content = generation(payload)
    elif schema_name == "catalyst_query_review":
        content = {
            "decision": "approve",
            "checks": [
                {
                    "name": "deterministic_ci_review",
                    "status": "passed",
                    "message": "Deterministic backend approved the fixed MVP query.",
                }
            ],
        }
    else:
        content = {"error": "unsupported deterministic response schema"}
    return {
        "id": "chatcmpl-catalyst-mvp-fake",
        "object": "chat.completion",
        "model": payload.get("model", MODEL),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content, separators=(",", ":")),
                },
                "finish_reason": "stop",
            }
        ],
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self.send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": model, "object": "model", "owned_by": "ci"}
                        for model in MODELS
                    ],
                },
            )
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            self.send_json(200, completion(payload))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})

    def log_message(self, message: str, *args: Any) -> None:
        print(f"fake-model-router: {message % args}", flush=True)


if __name__ == "__main__":
    port = int(os.getenv("MODEL_ROUTER_PORT", "8077"))
    print(f"fake-model-router: simulating {', '.join(MODELS)} on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
