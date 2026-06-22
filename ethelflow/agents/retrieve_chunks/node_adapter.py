from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict, List
import aiohttp
import uuid
import os

from ethelflow.agents.retrieve_chunks.models import RetrieveChunksRequest, RetrieveChunksResponse

# RETRIEVE_CHUNKS_URL: str = "http://retrieve-chunks.default.svc:8000/retrieve_chunks"
RETRIEVE_CHUNKS_URL: str = "http://retrieve-chunks:8000/retrieve_chunks"


def _as_uuid_list(xs: Any, field_name: str) -> List[uuid.UUID]:
    if not isinstance(xs, list):
        raise ValueError(f"Expected list for {field_name}, got {type(xs)}")
    out: List[uuid.UUID] = []
    for x in xs:
        if isinstance(x, uuid.UUID):
            out.append(x)
        elif isinstance(x, str):
            out.append(uuid.UUID(x))
        else:
            raise ValueError(f"Expected UUID|str in {field_name}, got {type(x)}")
    return out


def retrieve_chunks_node(
    chunk_ids_key: str = "chunk_ids",
    tenant_key: str = "tenant",
    output_key: str = "retrieve_chunks_response",
    output_texts_key: str = "chunk_texts",
    output_metadata_key: str = "chunk_metadata",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        tenant = state.get(tenant_key)
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError(f"Expected non-empty str for {tenant_key}, got {tenant!r}")

        chunk_ids = _as_uuid_list(state.get(chunk_ids_key, []), chunk_ids_key)

        req = RetrieveChunksRequest(tenant=tenant, chunk_ids=chunk_ids)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                RETRIEVE_CHUNKS_URL,
                json=req.model_dump(mode="json"),
                timeout=300,
            ) as resp:
                payload_text = await resp.text()
                if resp.status != 200:
                    raise ValueError(f"retrieve-chunks HTTP {resp.status}: {payload_text}")
                payload = await resp.json()

        data = RetrieveChunksResponse.model_validate(payload)
        if not data.success:
            raise ValueError(f"retrieve_chunks failed: {data.message}")

        yield {
            output_key: data.model_dump(mode="json"),
            output_texts_key: list(data.chunk_texts),
            output_metadata_key: list(data.chunk_metadata),
        }

    return node

