import json
from typing import Any, Dict, Optional

import httpx
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import A2ACardResolver
from a2a.types import Message, Part, Role, TextPart, TransportProtocol


class A2AClient:
    def __init__(self, router_url: str) -> None:
        self._router_url = router_url
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def _create_client(self):
        resolver = A2ACardResolver(self._http_client, self._router_url)
        agent_card = await resolver.get_agent_card()
        client_config = ClientConfig(
            httpx_client=self._http_client,
            supported_transports=[TransportProtocol.jsonrpc],
            use_client_preference=False,
        )
        return ClientFactory(client_config).create(agent_card)

    @staticmethod
    def _extract_user_message(payload: Dict[str, Any]) -> Optional[str]:
        messages = payload.get("messages") or []
        for message in reversed(messages):
            if message.get("role") == "user":
                return message.get("content")
        return None

    async def send_chat_completion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        query = self._extract_user_message(payload) or ""
        client = await self._create_client()
        message = Message(
            messageId=payload.get("id") or "catalyst-m0",
            role=Role.user,
            parts=[Part(root=TextPart(text=query))],
        )

        final_task = None
        async for event in client.send_message(message):
            final_task = event[0] if isinstance(event, tuple) else event

        response_text = ""
        if final_task and getattr(final_task, "artifacts", None):
            parts = final_task.artifacts[-1].parts
            if parts and hasattr(parts[0].root, "text"):
                response_text = parts[0].root.text

        completion_id = payload.get("id", "catalyst-m0")

        # feature 011: the FHIR sidecar agent returns a JSON-encoded
        # sidecar_response.schema.json payload (answer/facts/citations/
        # uiBlocks/provenance). Merge it into the OpenAI-shaped envelope so
        # `answer` doubles as choices[0].message.content and generic OpenAI
        # clients keep working, while Catalyst-aware clients (the sidecar UI,
        # the harness adapter) read the additive fields. Older agents
        # (sqlgen's plain-SQL-text artifact) fall back to the legacy
        # plain-text envelope below.
        sidecar_response: Optional[Dict[str, Any]] = None
        try:
            parsed = json.loads(response_text)
            if isinstance(parsed, dict) and "answer" in parsed:
                sidecar_response = parsed
        except (ValueError, TypeError):
            sidecar_response = None

        if sidecar_response is not None:
            envelope = dict(sidecar_response)
            envelope["id"] = completion_id
            envelope["object"] = "chat.completion"
            envelope["choices"] = [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": sidecar_response["answer"]},
                    "finish_reason": "stop",
                }
            ]
            return envelope

        return {
            "id": completion_id,
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }
            ],
        }
