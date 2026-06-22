from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict
import aiohttp
import uuid
import os


from ethelflow.agents.reasoning.models import ReasoningRequest, ReasoningResponse

# REASONING_URL: str = "http://reasoning.default.svc:8000/reasoning"
# REASONING_WITH_DOCUMENT_URL: str = "http://reasoning.default.svc:8000/reasoning_with_document"

REASONING_URL: str = "http://reasoning:8000/reasoning"
REASONING_WITH_DOCUMENT_URL: str = "http://reasoning:8000/reasoning_with_document"


def reasoning_node(
    tenant_key: str = "tenant",
    prompt_key: str = "prompt",
    stream_key: str = "stream",
    messages_key: str = "messages",
    reasoning_effort_key: str | None = None,
    document_id_key: str | None = None,
    content_type_key: str | None = None,
    document_ids_key: str | None = None,
    content_types_key: str | None = None,
    images_key: str | None = None,
    output_key: str = "reasoning_response",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        tenant = state.get(tenant_key)
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError(f"Expected non-empty str for {tenant_key}, got {tenant!r}")

        prompt = state.get(prompt_key)
        stream = state.get(stream_key, False)

        document_id = state.get(document_id_key) if document_id_key else None
        document_ids = state.get(document_ids_key) if document_ids_key else None
        images = state.get(images_key) if images_key else None
        messages = state.get(messages_key) if messages_key else None
        content_type = state.get(content_type_key) if content_type_key else None
        content_types = state.get(content_types_key) if content_types_key else None
        reasoning_effort = state.get(reasoning_effort_key) if reasoning_effort_key else None

        if prompt is not None and not isinstance(prompt, str):
            raise ValueError(f"Expected str for {prompt_key}, got {type(prompt)}")
        if prompt is None and not messages:
            raise ValueError("Either prompt or messages must be provided")

        url = REASONING_URL
        req = ReasoningRequest(
            tenant=tenant,
            messages=messages,
            prompt=prompt,
            reasoning_effort=reasoning_effort,
            stream=stream,
        )

        if images:
            url = REASONING_WITH_DOCUMENT_URL
            req.images = images
        elif document_ids:
            url = REASONING_WITH_DOCUMENT_URL
            req.document_ids = document_ids
            req.content_types = content_types
        elif document_id:
            url = REASONING_WITH_DOCUMENT_URL
            req.document_id = document_id
            req.content_type = content_type

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=req.model_dump(mode="json"), timeout=300) as response:
                if response.status != 200:
                    raise ValueError(f"Reasoning service returned {response.status}: {await response.text()}")

                if stream:
                    full = ""
                    async for chunk in response.content.iter_any():
                        txt = chunk.decode("utf-8")
                        full += txt
                        yield {output_key: txt}
                    yield {output_key: None}
                    yield {output_key: full}
                else:
                    payload = await response.json()
                    data = ReasoningResponse.model_validate(payload)
                    yield {output_key: data.response}

    return node

