from __future__ import annotations

from typing import Any

from .catalog import Catalog


# Default governed-query profile: writer-only (no independent review) per the
# product default. The self-checked writer+reviewer profile is the opt-in option.
QUERY_PROFILE_ID = "catalyst-query-gemma-4-12b-q4"
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


def build_revision_query_request(
    instruction: str,
    catalog: Catalog,
    *,
    revision: dict[str, Any],
    max_rows: int,
    statement_timeout_ms: int,
    request_id: str,
    trace_id: str,
    profile_id: str,
) -> dict[str, Any]:
    """Build the one-message v2 request for a complete successor query."""

    request = build_query_request(
        instruction,
        catalog,
        max_rows=max_rows,
        statement_timeout_ms=statement_timeout_ms,
        request_id=request_id,
        trace_id=trace_id,
        profile_id=profile_id,
    )
    request["catalystQuery"]["contractVersion"] = "catalyst.query.request.v2"
    request["catalystQuery"]["revision"] = revision
    return request
