"""Gateway-owned governed-query orchestration.

The pipeline composes small, self-guarding steps (context, generate, lint,
review, repair, finalize) into a governed-query run. The med-agent-hub is now
called only as a generic role executor; all Catalyst-specific orchestration,
linting, and evidence assembly live here.
"""

from __future__ import annotations

from .base import Pipeline, PipelineStep
from .context import PipelineContext
from .profile import QueryProfile, RoleConfig

__all__ = [
    "Pipeline",
    "PipelineStep",
    "PipelineContext",
    "QueryProfile",
    "RoleConfig",
]
