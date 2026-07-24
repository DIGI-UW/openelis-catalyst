"""Local governed-query orchestrator — the gateway running its own engine.

Implements the same interface the gateway's service layer used to call across the
network to the med-agent-hub (``generate_query`` / ``list_query_profiles`` /
``readiness``), but runs the relocated :func:`execute_query_profile` in-process.
Model calls inside the engine go to the hub's generic ``/v1/hub/generate``
executor; profile discovery is served from the gateway-owned registry.

Because both discovery and generation compute their profile evidence with the
same :func:`query_profile_evidence`, the service layer's profile-binding checks
match by construction.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from .query_engine import (
    EngineProfile,
    EngineRequest,
    execute_query_profile,
    query_profile_evidence,
)
from .query_profiles import DEFAULT_PROFILE_ID, PROFILES

_QUERY_STAGES = ("context", "query_generate", "query_lint", "query_review", "query_finalize")
_OUTPUT_CONTRACT = "catalyst.query.v1"


class LocalHubError(RuntimeError):
    def __init__(self, code: str, message: str, *, raw_output: str | None = None) -> None:
        self.code = code
        self.raw_output = raw_output
        super().__init__(message)


class LocalHub:
    """Runs governed queries in-process against the gateway-owned engine."""

    def __init__(
        self,
        *,
        hub_base_url: str,
        profiles: dict[str, EngineProfile] | None = None,
        health_timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._profiles = dict(profiles or PROFILES)
        self._client = httpx.AsyncClient(
            base_url=hub_base_url.rstrip("/"),
            timeout=httpx.Timeout(health_timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _discovery_entry(self, profile: EngineProfile, *, available: bool) -> dict[str, Any]:
        stages = [stage for stage in _QUERY_STAGES if stage != "query_review" or profile.has_review]
        return {
            "id": profile.id,
            "label": profile.label,
            "available": available,
            "required_models": sorted(set(profile.models.values())),
            "role_models": dict(profile.models),
            "role_knobs": {role: dict(knobs) for role, knobs in profile.knobs.items()},
            "stages": stages,
            "unavailable_reasons": [] if available else ["model_unavailable"],
            "capabilities": {"staged": False, "validation": True, "modelRouter": available},
            "outputContracts": [_OUTPUT_CONTRACT],
            "revisionCapable": profile.has_review,
            "profileEvidence": query_profile_evidence(profile),
        }

    async def list_query_profiles(self) -> list[dict[str, Any]]:
        # Availability is optimistic for the demo: the configured model is present
        # on the router. (A model-presence probe can tighten this later.)
        return [
            self._discovery_entry(profile, available=True)
            for profile in self._profiles.values()
        ]

    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(request.get("model") or DEFAULT_PROFILE_ID)
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise LocalHubError(
                "profile_unavailable",
                f"Gateway does not define query profile {profile_id}.",
            )
        catalyst_query = request.get("catalystQuery")
        if not isinstance(catalyst_query, dict):
            raise LocalHubError(
                "profile_incompatible", "Request is missing its catalystQuery context."
            )
        engine_request = EngineRequest(
            catalyst_query=catalyst_query,
            messages=list(request.get("messages") or []),
            profile=profile,
        )
        result: Optional[dict[str, Any]] = None
        async for kind, payload in execute_query_profile(engine_request):
            if kind == "result":
                result = json.loads(payload)
        if result is None:
            raise LocalHubError(
                "hub_invalid_response", "Query engine produced no result."
            )
        return result

    async def readiness(self) -> dict[str, dict[str, Any]]:
        hub_ready = False
        hub_message: str | None = None
        try:
            health = await self._client.get("/health")
            hub_ready = health.is_success
        except httpx.HTTPError as error:
            hub_message = str(error)

        default_available = DEFAULT_PROFILE_ID in self._profiles
        hub_check: dict[str, Any] = {"ready": hub_ready}
        if hub_message:
            hub_check["message"] = hub_message
        return {
            "hub": hub_check,
            "profile": {"ready": default_available},
            "modelRouter": {"ready": hub_ready},
        }
