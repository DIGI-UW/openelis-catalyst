"""Gateway-owned query profile configuration.

Previously the med-agent-hub owned catalyst-query-* profiles in ``levels.yaml``
and hardwired their five-stage shape. Orchestration is now the gateway's
concern, so a :class:`QueryProfile` here declares which model/prompt/knobs fill
each role and — critically — whether the review role exists at all. Whether
review runs is expressed as *composition* (the review role is present or it is
not), not as an immutable hub invariant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

WRITER_ROLE = "query_generate"
REVIEWER_ROLE = "query_review"


@dataclass(frozen=True)
class RoleConfig:
    """Model + prompt + sampling knobs for one role in the pipeline."""

    model: str
    prompt: str
    temperature: float = 0.0
    dry: float = 0.0


@dataclass(frozen=True)
class QueryProfile:
    """A composable governed-query profile owned by the gateway.

    ``roles`` always contains the writer (:data:`WRITER_ROLE`). It contains the
    reviewer (:data:`REVIEWER_ROLE`) only for reviewed profiles; a writer-only
    profile omits it, and the pipeline builder then composes no review steps.
    """

    id: str
    label: str
    roles: Mapping[str, RoleConfig]
    generation_attempts: int = 3
    collaborative_review: bool = False
    model_classes: Mapping[str, str] = field(default_factory=dict)
    allowed_operation: str = "select"

    def __post_init__(self) -> None:
        if WRITER_ROLE not in self.roles:
            raise ValueError(
                f"query profile {self.id!r} must define the {WRITER_ROLE!r} role"
            )
        if not 1 <= self.generation_attempts <= 3:
            raise ValueError(
                f"query profile {self.id!r} generation_attempts must be 1..3"
            )
        for role, config in self.roles.items():
            if config.temperature != 0 or config.dry != 0:
                raise ValueError(
                    f"query profile {self.id!r} role {role!r} must use "
                    "temperature 0 and dry 0 (deterministic governed queries)"
                )
        if self.collaborative_review:
            if not self.has_review:
                raise ValueError(
                    f"query profile {self.id!r} sets collaborative_review "
                    f"without a {REVIEWER_ROLE!r} role"
                )
            writer_class = self.model_classes.get(WRITER_ROLE)
            reviewer_class = self.model_classes.get(REVIEWER_ROLE)
            if not writer_class or not reviewer_class or writer_class == reviewer_class:
                raise ValueError(
                    f"collaborative query profile {self.id!r} requires distinct "
                    "writer and reviewer model classes"
                )

    @property
    def has_review(self) -> bool:
        """Whether this profile runs the reviewer role."""

        return REVIEWER_ROLE in self.roles

    @property
    def writer(self) -> RoleConfig:
        return self.roles[WRITER_ROLE]

    @property
    def reviewer(self) -> RoleConfig:
        if not self.has_review:
            raise ValueError(
                f"query profile {self.id!r} has no {REVIEWER_ROLE!r} role"
            )
        return self.roles[REVIEWER_ROLE]
