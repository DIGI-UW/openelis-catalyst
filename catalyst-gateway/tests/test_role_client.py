"""Tests for the gateway's RoleClient against the hub generic executor."""

from __future__ import annotations

import json

import httpx
import pytest

from src.catalyst.pipeline.roles import RoleClient, RoleError


def _client(handler) -> RoleClient:
    return RoleClient("http://hub", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_generate_forwards_payload_and_returns_content():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "m", "content": '{"status":"ready"}'})

    client = _client(handler)
    result = await client.generate(
        role="writer",
        model="gemma-4-12b-q4",
        messages=[{"role": "user", "content": "list tests"}],
        response_format={"type": "json_schema"},
        temperature=0,
        dry_multiplier=0,
    )
    await client.aclose()

    assert seen["url"].endswith("/v1/hub/generate")
    assert seen["body"]["model"] == "gemma-4-12b-q4"
    assert seen["body"]["response_format"] == {"type": "json_schema"}
    assert result.content == '{"status":"ready"}'
    assert result.invocation["role"] == "writer"
    assert result.invocation["outcome"] == "succeeded"
    assert result.invocation["httpStatus"] == 200
    assert isinstance(result.invocation["wallMs"], int)


@pytest.mark.asyncio
async def test_generate_raises_on_hub_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="model backend returned 500")

    client = _client(handler)
    with pytest.raises(RoleError) as excinfo:
        await client.generate(
            role="writer", model="m", messages=[{"role": "user", "content": "x"}]
        )
    await client.aclose()
    assert excinfo.value.code == "hub_error"


@pytest.mark.asyncio
async def test_generate_raises_on_empty_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "m", "content": "   "})

    client = _client(handler)
    with pytest.raises(RoleError) as excinfo:
        await client.generate(
            role="reviewer", model="m", messages=[{"role": "user", "content": "x"}]
        )
    await client.aclose()
    assert excinfo.value.code == "hub_invalid_response"
