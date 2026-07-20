from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .contracts import ContractError, ContractRegistry
from .request import QUERY_OUTPUT_CONTRACT, QUERY_PROFILE_ID


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

    async def list_query_profiles(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/v1/models")
        body = self._json_object(response, "profile discovery")
        models = body.get("data")
        if not isinstance(models, list):
            raise HubError(
                "profile_incompatible",
                "Hub model discovery must contain a data array.",
            )
        return [
            model
            for model in models
            if isinstance(model, dict)
            and str(model.get("id", "")).startswith("catalyst-query-")
        ]

    async def discover_query_profile(
        self, profile_id: str = QUERY_PROFILE_ID
    ) -> dict[str, Any]:
        profile = next(
            (
                model
                for model in await self.list_query_profiles()
                if model.get("id") == profile_id
            ),
            None,
        )
        if profile is None or profile.get("available") is not True:
            raise HubError(
                "profile_unavailable",
                f"Hub does not advertise available profile {profile_id}.",
            )
        capabilities = profile.get("capabilities")
        output_contracts = profile.get("outputContracts")
        if output_contracts is None and isinstance(capabilities, dict):
            output_contracts = capabilities.get("outputContracts")
        if (
            not isinstance(output_contracts, list)
            or not all(isinstance(item, str) for item in output_contracts)
            or QUERY_OUTPUT_CONTRACT not in output_contracts
        ):
            raise HubError(
                "profile_incompatible",
                f"Profile {profile_id} does not advertise " f"{QUERY_OUTPUT_CONTRACT}.",
            )
        return profile

    async def generate_query(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            context = request.get("catalystQuery")
            request_contract = (
                context.get("contractVersion") if isinstance(context, dict) else None
            )
            self.contracts.validate(
                (
                    "catalyst-query-request-v2.schema.json"
                    if request_contract == "catalyst.query.request.v2"
                    else "catalyst-query-request-v1.schema.json"
                ),
                request,
            )
        except ContractError as error:
            raise HubError("profile_incompatible", str(error)) from error

        await self.discover_query_profile(str(request["model"]))
        response = await self._request(
            "POST",
            "/v1/chat/completions",
            json=request,
        )
        try:
            completion = self._json_object(response, "query completion")
        except HubError as error:
            raise HubError(
                error.code,
                str(error),
                raw_output=response.text,
            ) from error
        raw_output = self._completion_raw_output(completion)
        try:
            contract_completion = dict(completion)
            for evidence_field in (
                "profileEvidence",
                "modelInvocations",
                "totalInvocationDurationMs",
            ):
                contract_completion.pop(evidence_field, None)
            self.contracts.validate(
                "catalyst-query-completion-v1.schema.json",
                contract_completion,
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
                raw_output=raw_output,
            ) from error
        evidence = {
            key: query.pop(key)
            for key in (
                "profileEvidence",
                "modelInvocations",
                "totalModelInvocationDurationMs",
            )
            if key in query
        }
        evidence["exactHubResponse"] = response.text
        evidence["hubResponseContentType"] = response.headers.get(
            "content-type", "application/json"
        )
        for key in (
            "profileEvidence",
            "modelInvocations",
            "totalModelInvocationDurationMs",
        ):
            if key in completion and key not in evidence:
                evidence[key] = completion[key]
        if evidence:
            query = {**query, "_hubEvidence": evidence}
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
                profile = await self.discover_query_profile(QUERY_PROFILE_ID)
                profile_ready = True
                capabilities = profile.get("capabilities", {})
                model_router = capabilities.get("modelRouter")
                model_router_ready = (
                    profile.get("available") is True
                    or model_router is True
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

    @staticmethod
    def _completion_raw_output(completion: dict[str, Any]) -> str | None:
        """Best-effort extraction for manual recovery from malformed completions."""

        try:
            content = completion["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        return content if isinstance(content, str) else None

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
                raw_output=response.text,
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
