from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .contracts import ContractError, ContractRegistry
from .request import QUERY_OUTPUT_CONTRACT, QUERY_PROFILE_ID


class HubError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class HubClient:
    def __init__(
        self,
        base_url: str,
        contracts: ContractRegistry,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.contracts = contracts
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def discover_query_profile(self) -> dict[str, Any]:
        response = await self._request("GET", "/v1/models")
        body = self._json_object(response, "profile discovery")
        models = body.get("data")
        if not isinstance(models, list):
            raise HubError(
                "profile_incompatible",
                "Hub model discovery must contain a data array.",
            )
        profile = next(
            (
                model
                for model in models
                if isinstance(model, dict) and model.get("id") == QUERY_PROFILE_ID
            ),
            None,
        )
        if profile is None or profile.get("available") is not True:
            raise HubError(
                "profile_unavailable",
                f"Hub does not advertise available profile {QUERY_PROFILE_ID}.",
            )
        capabilities = profile.get("capabilities")
        output_contracts = (
            capabilities.get("outputContracts")
            if isinstance(capabilities, dict)
            else None
        )
        if (
            not isinstance(output_contracts, list)
            or not all(isinstance(item, str) for item in output_contracts)
            or QUERY_OUTPUT_CONTRACT not in output_contracts
        ):
            raise HubError(
                "profile_incompatible",
                f"Profile {QUERY_PROFILE_ID} does not advertise "
                f"{QUERY_OUTPUT_CONTRACT}.",
            )
        return profile

    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            self.contracts.validate(
                "catalyst-query-request-v1.schema.json",
                request,
            )
        except ContractError as error:
            raise HubError("profile_incompatible", str(error)) from error

        await self.discover_query_profile()
        response = await self._request(
            "POST",
            "/v1/chat/completions",
            json=request,
        )
        completion = self._json_object(response, "query completion")
        try:
            self.contracts.validate(
                "catalyst-query-completion-v1.schema.json",
                completion,
            )
            content = completion["choices"][0]["message"]["content"]
            query = json.loads(content)
            if not isinstance(query, dict):
                raise ValueError("completion content is not a JSON object")
            self.contracts.validate("catalyst-query-v1.schema.json", query)
        except (
            ContractError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            ValueError,
        ) as error:
            raise HubError(
                "hub_invalid_response",
                f"Hub returned an invalid structured query completion: {error}",
            ) from error
        return query

    async def readiness(self) -> dict[str, dict[str, Any]]:
        hub_ready = False
        profile_ready = False
        model_router_ready = False
        hub_message: str | None = None
        profile_message: str | None = None
        try:
            health = await self._request("GET", "/health")
            hub_ready = health.is_success
        except HubError as error:
            hub_message = str(error)

        if hub_ready:
            try:
                profile = await self.discover_query_profile()
                profile_ready = True
                capabilities = profile.get("capabilities", {})
                model_router = capabilities.get("modelRouter")
                model_router_ready = (
                    model_router is True
                    or isinstance(model_router, dict)
                    and model_router.get("available") is True
                )
            except HubError as error:
                profile_message = str(error)

        hub_check: dict[str, Any] = {"ready": hub_ready}
        if hub_message:
            hub_check["message"] = hub_message
        profile_check: dict[str, Any] = {"ready": profile_ready}
        if profile_message:
            profile_check["message"] = profile_message
        return {
            "hub": hub_check,
            "queryProfile": profile_check,
            "modelRouter": {"ready": model_router_ready},
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            raise HubError("hub_timeout", "Hub request timed out.") from error
        except httpx.RequestError as error:
            raise HubError("hub_unavailable", f"Hub is unavailable: {error}") from error
        except asyncio.CancelledError as error:
            raise HubError("hub_cancelled", "Hub request was cancelled.") from error
        if not response.is_success:
            raise HubError(
                "hub_unavailable",
                f"Hub returned HTTP {response.status_code}.",
            )
        return response

    @staticmethod
    def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HubError(
                "hub_invalid_response",
                f"Hub {label} was not valid JSON.",
            ) from error
        if not isinstance(body, dict):
            raise HubError(
                "hub_invalid_response",
                f"Hub {label} must be a JSON object.",
            )
        return body
