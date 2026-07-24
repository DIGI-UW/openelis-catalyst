"""Foundation tests for the composable governed-query pipeline runner."""

from __future__ import annotations

import pytest

from src.catalyst.pipeline import (
    Pipeline,
    PipelineContext,
    PipelineStep,
    QueryProfile,
    RoleConfig,
)


def _writer_only_profile() -> QueryProfile:
    return QueryProfile(
        id="test-writer-only",
        label="Writer only",
        roles={"query_generate": RoleConfig(model="m-writer", prompt="p-writer")},
    )


def _reviewed_profile() -> QueryProfile:
    return QueryProfile(
        id="test-reviewed",
        label="Writer + reviewer",
        roles={
            "query_generate": RoleConfig(model="m-writer", prompt="p-writer"),
            "query_review": RoleConfig(model="m-reviewer", prompt="p-review"),
        },
        collaborative_review=True,
        model_classes={"query_generate": "writer-class", "query_review": "review-class"},
    )


def _context(profile: QueryProfile) -> PipelineContext:
    request = {
        "model": profile.id,
        "messages": [{"role": "user", "content": "what tests exist?"}],
        "catalystQuery": {"contractVersion": "catalyst.query.request.v1"},
    }
    return PipelineContext(request=request, profile=profile, roles=object())


class _RecordingStep(PipelineStep):
    def __init__(self, name: str, *, applies: bool = True, terminal: bool = False):
        self.name = name
        self._applies = applies
        self._terminal = terminal

    def applies(self, ctx: PipelineContext) -> bool:
        return self._applies

    async def run(self, ctx: PipelineContext) -> None:
        ctx.record_trace(role=self.name)
        if self._terminal:
            ctx.result = {"status": "ready", "by": self.name}


@pytest.mark.asyncio
async def test_runs_steps_in_order_and_finalizes():
    ctx = _context(_writer_only_profile())
    pipeline = Pipeline(
        [_RecordingStep("a"), _RecordingStep("b"), _RecordingStep("z", terminal=True)]
    )

    result = await pipeline.run(ctx)

    assert [entry["role"] for entry in ctx.trace] == ["a", "b", "z"]
    assert result == {"status": "ready", "by": "z"}


@pytest.mark.asyncio
async def test_terminal_step_short_circuits_remaining_steps():
    ctx = _context(_writer_only_profile())
    later = _RecordingStep("later")
    pipeline = Pipeline([_RecordingStep("reject", terminal=True), later])

    result = await pipeline.run(ctx)

    # The second step must never run once a terminal result is set.
    assert [entry["role"] for entry in ctx.trace] == ["reject"]
    assert result["by"] == "reject"


@pytest.mark.asyncio
async def test_inapplicable_step_is_skipped():
    ctx = _context(_writer_only_profile())
    pipeline = Pipeline(
        [
            _RecordingStep("kept"),
            _RecordingStep("skipped", applies=False),
            _RecordingStep("done", terminal=True),
        ]
    )

    await pipeline.run(ctx)

    assert [entry["role"] for entry in ctx.trace] == ["kept", "done"]


@pytest.mark.asyncio
async def test_pipeline_without_terminal_step_raises():
    ctx = _context(_writer_only_profile())
    pipeline = Pipeline([_RecordingStep("a"), _RecordingStep("b")])

    with pytest.raises(RuntimeError, match="without producing a result"):
        await pipeline.run(ctx)


def test_selected_candidate_prefers_repaired():
    ctx = _context(_reviewed_profile())
    ctx.candidate = {"sql": "SELECT 1"}
    assert ctx.selected_candidate == {"sql": "SELECT 1"}
    ctx.repaired = {"sql": "SELECT 2"}
    assert ctx.selected_candidate == {"sql": "SELECT 2"}


def test_writer_only_profile_has_no_review():
    profile = _writer_only_profile()
    assert profile.has_review is False
    with pytest.raises(ValueError, match="no 'query_review' role"):
        _ = profile.reviewer


def test_reviewed_profile_exposes_both_roles():
    profile = _reviewed_profile()
    assert profile.has_review is True
    assert profile.writer.model == "m-writer"
    assert profile.reviewer.model == "m-reviewer"


def test_collaborative_review_requires_distinct_model_classes():
    with pytest.raises(ValueError, match="distinct writer and reviewer"):
        QueryProfile(
            id="bad",
            label="bad",
            roles={
                "query_generate": RoleConfig(model="m", prompt="p"),
                "query_review": RoleConfig(model="m2", prompt="p2"),
            },
            collaborative_review=True,
            model_classes={"query_generate": "same", "query_review": "same"},
        )


def test_nonzero_knobs_rejected():
    with pytest.raises(ValueError, match="temperature 0 and dry 0"):
        QueryProfile(
            id="hot",
            label="hot",
            roles={"query_generate": RoleConfig(model="m", prompt="p", temperature=0.7)},
        )
