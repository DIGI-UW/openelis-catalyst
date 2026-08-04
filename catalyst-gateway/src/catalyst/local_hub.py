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

from .hub import HubError
from .query_engine import (
    EngineProfile,
    EngineRequest,
    execute_query_profile,
    query_profile_evidence,
)
from .query_profiles import DEFAULT_PROFILE_ID, PROFILES

_QUERY_STAGES = (
    "context",
    "query_generate",
    "query_lint",
    "query_review",
    "query_finalize",
)
_OUTPUT_CONTRACT = "catalyst.query.v1"


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

    @staticmethod
    def _profile_unavailable_reasons(
        profile: EngineProfile,
        advertised_models: set[str] | None,
        inventory_error: str | None,
    ) -> list[str]:
        if inventory_error:
            return [inventory_error]
        assert advertised_models is not None
        return [
            f"model_not_advertised:{model}"
            for model in sorted(set(profile.models.values()) - advertised_models)
        ]

    async def _backend_model_inventory(self) -> tuple[set[str] | None, str | None]:
        """Read the exact model IDs in the Hub-owned router catalog."""
        try:
            response = await self._client.get("/v1/models")
            response.raise_for_status()
        except httpx.HTTPError:
            # The Gateway could not obtain the Hub-owned inventory. That does
            # not prove whether the model router itself is up or down.
            return None, "model_inventory_unavailable"
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return None, "model_inventory_unavailable"
        backend = payload.get("backend") if isinstance(payload, dict) else None
        if not isinstance(backend, dict):
            return None, "model_inventory_unavailable"
        if (
            backend.get("contract_version")
            != "med-agent-hub.backend-model-inventory.v1"
        ):
            return None, "model_inventory_unavailable"
        if backend.get("catalog_reachable") is not True:
            return None, "model_backend_unreachable"
        models = backend.get("advertised_model_ids")
        if not isinstance(models, list) or any(
            not isinstance(model, str) or not model for model in models
        ):
            return None, "model_inventory_unavailable"
        return set(models), None

    def _discovery_entry(
        self, profile: EngineProfile, *, unavailable_reasons: list[str]
    ) -> dict[str, Any]:
        available = not unavailable_reasons
        stages = [
            stage
            for stage in _QUERY_STAGES
            if stage != "query_review" or profile.has_review
        ]
        return {
            "id": profile.id,
            "label": profile.label,
            "available": available,
            "required_models": sorted(set(profile.models.values())),
            "role_models": dict(profile.models),
            "role_knobs": {role: dict(knobs) for role, knobs in profile.knobs.items()},
            "stages": stages,
            "unavailable_reasons": unavailable_reasons,
            "capabilities": {
                "staged": False,
                "validation": True,
                "modelRouter": available,
            },
            "outputContracts": [_OUTPUT_CONTRACT],
            # Revision support comes from the v2 request context consumed by the
            # query engine. It does not depend on whether a second model reviews
            # the writer's complete successor query.
            "revisionCapable": True,
            "profileEvidence": query_profile_evidence(profile),
        }

    async def list_query_profiles(self) -> list[dict[str, Any]]:
        advertised_models, inventory_error = await self._backend_model_inventory()
        return [
            self._discovery_entry(
                profile,
                unavailable_reasons=self._profile_unavailable_reasons(
                    profile, advertised_models, inventory_error
                ),
            )
            for profile in self._profiles.values()
        ]

    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(request.get("model") or DEFAULT_PROFILE_ID)
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise HubError(
                "profile_unavailable",
                f"Gateway does not define query profile {profile_id}.",
            )
        catalyst_query = request.get("catalystQuery")
        if not isinstance(catalyst_query, dict):
            raise HubError(
                "profile_incompatible", "Request is missing its catalystQuery context."
            )
        engine_request = EngineRequest(
            catalyst_query=catalyst_query,
            messages=list(request.get("messages") or []),
            profile=profile,
            max_tokens=int(profile.knobs["query_generate"]["maxTokens"]),
        )
        result: Optional[dict[str, Any]] = None
        async for kind, payload in execute_query_profile(engine_request):
            if kind == "result":
                result = json.loads(payload)
        if result is None:
            raise HubError("hub_invalid_response", "Query engine produced no result.")
        return result

    async def readiness(self) -> dict[str, dict[str, Any]]:
        hub_ready = False
        hub_message: str | None = None
        try:
            health = await self._client.get("/health")
            hub_ready = health.is_success
        except httpx.HTTPError as error:
            hub_message = str(error)

        advertised_models, inventory_error = await self._backend_model_inventory()
        profile_reasons = [
            self._profile_unavailable_reasons(
                profile, advertised_models, inventory_error
            )
            for profile in self._profiles.values()
        ]
        any_profile_available = any(not reasons for reasons in profile_reasons)
        hub_check: dict[str, Any] = {"ready": hub_ready}
        if hub_message:
            hub_check["message"] = hub_message
        profile_check: dict[str, Any] = {"ready": any_profile_available}
        if not any_profile_available:
            profile_check["unavailableReasons"] = (
                [inventory_error]
                if inventory_error
                else ["no_configured_profile_models_available"]
            )
        return {
            "hub": hub_check,
            "queryProfile": profile_check,
            "modelRouter": {"ready": advertised_models is not None},
        }
