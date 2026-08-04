"""Shared hub error type.

Governed-query orchestration now runs in-process (see ``local_hub.LocalHub`` and
``query_engine``); the old ``HubClient`` HTTP client was removed. ``HubError`` is
retained as the error type the service layer raises and catches for hub failures.
"""

from __future__ import annotations


class HubError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        raw_output: str | None = None,
    ) -> None:
        self.code = code
        self.raw_output = raw_output
        super().__init__(message)
