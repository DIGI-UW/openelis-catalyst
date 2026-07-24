"""Client for the hub's generic role executor (``POST /v1/hub/generate``).

The gateway owns orchestration; it calls the hub once per role for a single
structured completion. :class:`RoleClient` is the thin seam the model-calling
steps depend on — it issues the hub call and returns the raw model content plus
one invocation-evidence record. Steps build the messages and interpret the
content; the client stays domain-agnostic so it is trivial to fake in tests.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

import httpx


class RoleError(RuntimeError):
    """The hub role executor failed or returned an unusable response."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RoleResult:
    """One role completion: the raw model content and its invocation evidence."""

    def __init__(self, content: str, invocation: dict[str, Any]) -> None:
        self.content = content
        self.invocation = invocation


class RoleClient:
    """Issues single structured completions against the hub's generic executor."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 1800.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate(
        self,
        *,
        role: str,
        model: str,
        messages: list[dict[str, Any]],
        response_format: Optional[Mapping[str, Any]] = None,
        temperature: float = 0.0,
        dry_multiplier: float = 0.0,
        max_tokens: Optional[int] = None,
    ) -> RoleResult:
        """Run one hub completion for ``role`` and return content + evidence."""

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "dry_multiplier": dry_multiplier,
        }
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        started = time.monotonic()
        try:
            response = await self._client.post("/v1/hub/generate", json=payload)
        except httpx.HTTPError as error:
            raise RoleError(
                "hub_unreachable", f"hub role executor request failed: {error}"
            ) from error
        elapsed_ms = round((time.monotonic() - started) * 1000)

        invocation: dict[str, Any] = {
            "role": role,
            "model": model,
            "httpStatus": response.status_code,
            "wallMs": elapsed_ms,
        }

        if response.status_code >= 400:
            invocation["outcome"] = "failed"
            raise RoleError(
                "hub_error",
                f"hub role executor returned {response.status_code}: "
                f"{response.text[:400]}",
            )
        try:
            body = response.json()
        except ValueError as error:
            invocation["outcome"] = "failed"
            raise RoleError(
                "hub_invalid_response", "hub role executor returned non-JSON"
            ) from error
        content = body.get("content") if isinstance(body, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            invocation["outcome"] = "failed"
            raise RoleError(
                "hub_invalid_response",
                "hub role executor returned no assistant content",
            )
        invocation["outcome"] = "succeeded"
        return RoleResult(content.strip(), invocation)
