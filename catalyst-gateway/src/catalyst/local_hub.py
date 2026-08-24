"""Catalyst query orchestration using Hub-owned named role profiles.

The Gateway keeps the query-specific work: catalog context, deterministic lint,
review loop, execution, and lineage. The Hub owns the profile selected by ID,
including each role's model, system prompt, and model configuration.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Optional

import httpx

from .hub import HubError
from .session_context import SESSION_CONTEXT_CONTRACT
from .query_engine import EngineProfile, EngineRequest, execute_query_profile

_QUERY_PROFILES_PATH = "/v1/hub/query-profiles"


class LocalHub:
    """Run Catalyst orchestration locally against profiles discovered from Hub."""

    def __init__(
        self,
        *,
        hub_base_url: str,
        health_timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=hub_base_url.rstrip("/"),
            timeout=httpx.Timeout(health_timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _profile_document(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Read the Hub-owned discovery document; reject malformed evidence."""
        try:
            response = await self._client.get(_QUERY_PROFILES_PATH)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, TypeError, ValueError) as error:
            raise HubError(
                "model_inventory_unavailable",
                "Hub query-profile discovery is unavailable.",
            ) from error
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise HubError(
                "model_inventory_unavailable",
                "Hub query-profile discovery returned an invalid document.",
            )
        backend = payload.get("backend")
        if not isinstance(backend, dict) or backend.get("contract_version") != (
            "med-agent-hub.backend-model-inventory.v1"
        ):
            raise HubError(
                "model_inventory_unavailable",
                "Hub query-profile discovery omitted its model-inventory contract.",
            )
        profiles = payload["data"]
        if any(not isinstance(profile, dict) for profile in profiles):
            raise HubError(
                "model_inventory_unavailable",
                "Hub query-profile discovery contains an invalid profile entry.",
            )
        return [deepcopy(profile) for profile in profiles], deepcopy(backend)

    @staticmethod
    def _engine_profile(entry: dict[str, Any]) -> EngineProfile:
        profile_id = entry.get("id")
        label = entry.get("label")
        models = entry.get("role_models")
        knobs = entry.get("role_knobs")
        evidence = entry.get("profileEvidence")
        policies = entry.get("policies") or {}
        if not isinstance(profile_id, str) or not profile_id:
            raise HubError("profile_incompatible", "Hub profile is missing its id.")
        if not isinstance(label, str) or not label:
            raise HubError("profile_incompatible", "Hub profile is missing its label.")
        if not isinstance(models, dict) or "query_generate" not in models:
            raise HubError(
                "profile_incompatible", "Hub profile is missing writer role data."
            )
        if not isinstance(knobs, dict) or "query_generate" not in knobs:
            raise HubError(
                "profile_incompatible", "Hub profile is missing writer knobs."
            )
        if not isinstance(evidence, dict):
            raise HubError("profile_incompatible", "Hub profile is missing evidence.")
        if evidence.get("profileId") != profile_id:
            raise HubError(
                "profile_incompatible", "Hub profile evidence has a mismatched id."
            )
        return EngineProfile(
            id=profile_id,
            label=label,
            models=deepcopy(models),
            knobs=deepcopy(knobs),
            policies=deepcopy(policies) if isinstance(policies, dict) else {},
            profile_evidence=deepcopy(evidence),
        )

    async def list_query_profiles(self) -> list[dict[str, Any]]:
        profiles, _backend = await self._profile_document()
        # The Hub reports availability using its live router catalog. Never
        # expose stale configured-but-missing profiles to Catalyst's UI.
        available = [
            profile for profile in profiles if profile.get("available") is True
        ]
        # Generation runs in this process, against an engine that reads the
        # Phase 1 layered context, so every profile it offers can be sent it.
        # Catalyst withholds the layer from anything that does not say so.
        for profile in available:
            profile.setdefault(
                "supported_request_contracts", [SESSION_CONTEXT_CONTRACT]
            )
        return available

    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(request.get("model") or "")
        profiles, _backend = await self._profile_document()
        selected = next(
            (profile for profile in profiles if profile.get("id") == profile_id), None
        )
        if selected is None or selected.get("available") is not True:
            raise HubError(
                "profile_unavailable",
                f"Hub does not currently offer query profile {profile_id}.",
            )
        catalyst_query = request.get("catalystQuery")
        if not isinstance(catalyst_query, dict):
            raise HubError(
                "profile_incompatible", "Request is missing its catalystQuery context."
            )
        profile = self._engine_profile(selected)
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

        try:
            profiles, backend = await self._profile_document()
        except HubError as error:
            profile_check: dict[str, Any] = {
                "ready": False,
                "unavailableReasons": [error.code],
            }
            return {
                "hub": {
                    "ready": hub_ready,
                    **({"message": hub_message} if hub_message else {}),
                },
                "queryProfile": profile_check,
                "modelRouter": {"ready": False},
            }
        available = [
            profile for profile in profiles if profile.get("available") is True
        ]
        profile_check = {"ready": bool(available)}
        if not available:
            profile_check["unavailableReasons"] = [
                "no_configured_profile_models_available"
            ]
        hub_check: dict[str, Any] = {"ready": hub_ready}
        if hub_message:
            hub_check["message"] = hub_message
        return {
            "hub": hub_check,
            "queryProfile": profile_check,
            "modelRouter": {"ready": backend.get("catalog_reachable") is True},
        }
