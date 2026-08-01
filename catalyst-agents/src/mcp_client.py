"""Real MCP protocol client for catalyst-agents -> catalyst-mcp (feature 011).

Replaces the M0.2 stub (hardcoded schema strings, no network call — see git
history) that bypassed the MCP protocol entirely. Per the source brief
(M10-C): "Catalyst agents must call FHIR/schema tools via the MCP protocol,
not the stub mcp_client.get_schema() bypass."

Uses the `mcp` package's streamable-http client against catalyst-mcp's
running FastMCP server (MCP_URL, default http://localhost:9102/mcp).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _mcp_url() -> str:
    base = os.getenv("MCP_URL", "http://localhost:9102").rstrip("/")
    return base if base.endswith("/mcp") else f"{base}/mcp"


@asynccontextmanager
async def _session() -> AsyncIterator[ClientSession]:
    async with streamablehttp_client(_mcp_url()) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool by name and return its structured result as a dict.

    Real tool-level errors (see catalyst-mcp/src/tools/fhir_tools.py) come
    back as {"error": ..., "detail": ...} payloads from the tool itself, not
    as MCP protocol errors — this function only raises for actual
    transport/protocol failures (MCP server unreachable, unknown tool),
    which callers should treat as distinct from a tool-level "not found".
    """
    async with _session() as session:
        result = await session.call_tool(tool_name, arguments)

    if result.isError:
        detail = "; ".join(
            getattr(block, "text", str(block)) for block in result.content
        )
        return {"error": "mcp_tool_error", "detail": detail}

    for block in result.content:
        if hasattr(block, "text"):
            import json

            try:
                return json.loads(block.text)
            except (ValueError, TypeError):
                return {"text": block.text}

    return {}


async def get_query_context(query: str) -> dict[str, Any]:
    return await call_tool("get_query_context", {"user_query": query})
