"""Mutable state threaded through a Catalyst query pipeline.

The gateway now owns query orchestration (previously the med-agent-hub baked the
whole generate -> lint -> review -> repair state machine into ``catalyst_query.py``).
A :class:`PipelineContext` carries the immutable request inputs plus the state
that accumulates as each :class:`~src.catalyst.pipeline.base.PipelineStep` runs,
and the terminal ``result`` once the pipeline has decided an outcome.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .profile import QueryProfile
    from .roles import RoleClient


class PipelineContext:
    """Carries request inputs and accumulating state across pipeline steps.

    A step reads the inputs and prior state, performs one unit of work (a model
    call, a deterministic gate, evidence assembly), and either advances the
    state or sets :attr:`result` to short-circuit the remaining steps. The
    runner stops as soon as ``result`` is set — a rejected pre-check and a
    finalized success both terminate the same way.
    """

    def __init__(
        self,
        *,
        request: Mapping[str, Any],
        profile: "QueryProfile",
        roles: "RoleClient",
    ) -> None:
        self.request = request
        self.profile = profile
        self.roles = roles

        extension = request.get("catalystQuery")
        self.extension: Mapping[str, Any] = (
            extension if isinstance(extension, Mapping) else {}
        )
        messages = request.get("messages") or []
        self.question: str = (
            messages[0].get("content", "") if messages else ""
        )
        self.is_revision: bool = (
            self.extension.get("contractVersion") == "catalyst.query.request.v2"
        )

        # Accumulating state (populated by steps as they run).
        self.candidate: Optional[dict[str, Any]] = None
        self.lint_history: list[dict[str, Any]] = []
        self.review: Optional[dict[str, Any]] = None
        self.repaired: Optional[dict[str, Any]] = None
        self.model_collaboration: Optional[dict[str, Any]] = None
        self.invocations: list[dict[str, Any]] = []
        self.trace: list[dict[str, Any]] = []

        # Terminal outcome — ``None`` until a step decides the result.
        self.result: Optional[dict[str, Any]] = None

    @property
    def terminal(self) -> bool:
        """True once a step has produced a terminal result."""

        return self.result is not None

    @property
    def selected_candidate(self) -> Optional[dict[str, Any]]:
        """The candidate a downstream step should treat as authoritative.

        Repair replaces the writer's candidate; until then the writer's
        candidate stands.
        """

        return self.repaired if self.repaired is not None else self.candidate

    def record_trace(self, **fields: Any) -> None:
        """Append one ordered trace entry (mirrors the hub's ``steps`` list)."""

        self.trace.append(dict(fields))

    def record_invocation(self, invocation: dict[str, Any]) -> None:
        """Append one model-invocation evidence entry."""

        self.invocations.append(invocation)
