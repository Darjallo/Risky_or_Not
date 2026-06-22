from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict, Optional
import aiohttp

import os

from ethelflow.agents.embedding.models import EmbeddingRequest, EmbeddingResponse

# EMBEDDING_URL: str = "http://embedding.default.svc:8000/embedding"
EMBEDDING_URL: str = "http://embedding:8000/embedding"


def embedding_node(
    input_texts_key: str = "texts",
    tenant_key: str = "tenant",
    space_key: str = "embedding_space",  # optional in state
    output_key: str = "embeddings",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        texts = state.get(input_texts_key)
        if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
            raise ValueError(f"Expected list[str] for {input_texts_key}, got {type(texts)}")

        tenant = state.get(tenant_key)
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError(f"Expected non-empty str for {tenant_key}, got {tenant!r}")

        space: Optional[str] = state.get(space_key)
        if space is not None and (not isinstance(space, str) or not space.strip()):
            raise ValueError(f"Expected str|None for {space_key}, got {space!r}")

        req = EmbeddingRequest(tenant=tenant, texts=texts, space=space)

        async with aiohttp.ClientSession() as session:
            async with session.post(EMBEDDING_URL, json=req.model_dump(mode="json"), timeout=300) as resp:
                if resp.status != 200:
                    raise ValueError(f"Embedding service returned status {resp.status}: {await resp.text()}")
                payload = await resp.json()

        data = EmbeddingResponse.model_validate(payload)
        yield {output_key: data.embeddings}

    return node

