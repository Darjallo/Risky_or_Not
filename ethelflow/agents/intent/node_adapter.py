from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict
import aiohttp
import os

from ethelflow.agents.intent.models import IntentRequest, IntentResponse

# INTENT_URL: str = "http://intent.default.svc:8000/intent"
INTENT_URL: str = os.getenv("INTENT_URL", "http://intent:8000/intent")



def intent_node(
    tenant_key: str = "tenant",
    prompt_key: str | None = "prompt",
    messages_key: str | None = "messages",
    intent_options_key: str = "intent_options",
    output_key: str = "intent_response",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    """
    Adapter for intent service.

    IMPORTANT:
    - Pass EITHER prompt OR messages, not both.
    - Use prompt_key=None (Option A) to omit prompt entirely and send messages only.
    - Use messages_key=None (Option B) to omit messages and send prompt only.
    """

    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        tenant = state.get(tenant_key)
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError(f"Expected non-empty str for {tenant_key}, got {tenant!r}")
        tenant = tenant.strip()

        prompt = state.get(prompt_key) if prompt_key else None
        messages = state.get(messages_key) if messages_key else None
        intent_options = state.get(intent_options_key)

        if not isinstance(intent_options, dict) or not intent_options:
            raise ValueError(f"Expected non-empty dict for {intent_options_key}")

        if prompt is not None and not isinstance(prompt, str):
            raise ValueError(f"Expected str for {prompt_key}, got {type(prompt)}")
        if messages is not None and not isinstance(messages, list):
            raise ValueError(f"Expected list for {messages_key}, got {type(messages)}")

        # Enforce "only one of prompt or messages"
        if (prompt is not None and prompt.strip()) and messages:
            raise ValueError("Only one of prompt or messages can be provided")

        if (prompt is None or (isinstance(prompt, str) and not prompt.strip())) and not messages:
            raise ValueError("Either prompt or messages must be provided")

        payload: Dict[str, Any] = {
            "tenant": tenant,
            "intent_options": intent_options,
            "stream": False,
        }
        if messages:
            payload["messages"] = messages
        else:
            payload["prompt"] = (prompt or "").strip()

        req = IntentRequest(**payload)

        async with aiohttp.ClientSession() as session:
            async with session.post(INTENT_URL, json=req.model_dump(mode="json"), timeout=60) as response:
                if response.status != 200:
                    raise ValueError(f"Intent service returned {response.status}: {await response.text()}")
                payload = await response.json()
                data = IntentResponse.model_validate(payload)
                yield {output_key: data.model_dump(mode="python")}

    return node

