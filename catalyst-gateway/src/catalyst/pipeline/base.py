"""Composable step interface and the runner that drives a query pipeline.

Each governed-query stage is a :class:`PipelineStep`. A profile composes an
ordered list of steps; the :class:`Pipeline` runs them in order, stops at the
first step that produces a terminal result, and lets each step decline to run
when its precondition is not met (``applies``). Making review optional is then a
composition concern — a writer-only profile simply omits the review steps — with
no special-casing inside the runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .context import PipelineContext


class PipelineStep(ABC):
    """One unit of governed-query work.

    Steps are self-guarding: :meth:`applies` decides whether the step is
    relevant to the current context (e.g. a ``Repair`` step only acts when the
    reviewer asked for a repair), so the same composed pipeline handles
    approve / reject / repair without branching in the runner.
    """

    #: Stable identifier used in trace entries and error messages.
    name: str = "step"

    def applies(self, ctx: PipelineContext) -> bool:
        """Whether this step should run for the current context.

        Defaults to always. Steps whose action is conditional on prior state
        override this instead of embedding the guard in :meth:`run`.
        """

        return True

    @abstractmethod
    async def run(self, ctx: PipelineContext) -> None:
        """Advance ``ctx`` state or set ``ctx.result`` to terminate the run."""


class Pipeline:
    """An ordered, terminating sequence of steps."""

    def __init__(self, steps: Sequence[PipelineStep]) -> None:
        self._steps: tuple[PipelineStep, ...] = tuple(steps)

    @property
    def steps(self) -> tuple[PipelineStep, ...]:
        return self._steps

    async def run(self, ctx: PipelineContext) -> dict:
        """Run each applicable step until one produces a terminal result.

        Returns the terminal ``ctx.result``. Raises :class:`RuntimeError` if the
        composed pipeline drains without any step setting a result — that is a
        composition bug (a missing finalize/reject step), not a runtime outcome.
        """

        for step in self._steps:
            if ctx.terminal:
                break
            if not step.applies(ctx):
                continue
            await step.run(ctx)

        if ctx.result is None:
            raise RuntimeError(
                "query pipeline completed without producing a result; "
                "the composed step list is missing a terminal step"
            )
        return ctx.result
