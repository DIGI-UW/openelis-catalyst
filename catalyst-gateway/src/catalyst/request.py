from __future__ import annotations

from typing import Any

from .catalog import Catalog


QUERY_PROFILE_ID = "catalyst-query-gemma-e4b"
QUERY_OUTPUT_CONTRACT = "catalyst.query.v1"


def build_query_request(
    question: str,
    catalog: Catalog,
    *,
    max_rows: int,
    statement_timeout_ms: int,
    request_id: str,
    trace_id: str,
    profile_id: str = QUERY_PROFILE_ID,
) -> dict[str, Any]:
    return {
        "model": profile_id,
        "stream": False,
        "messages": [{"role": "user", "content": question}],
        "catalystQuery": {
            "contractVersion": "catalyst.query.request.v1",
            "target": catalog.request_target(),
            "catalog": catalog.request_catalog(),
            "policy": {
                "allowedOperation": "select",
                "requirePreview": True,
                "maxRows": max_rows,
                "statementTimeoutMs": statement_timeout_ms,
            },
            "correlation": {
                "requestId": request_id,
                "traceId": trace_id,
            },
            "requiredOutputContract": QUERY_OUTPUT_CONTRACT,
        },
    }
