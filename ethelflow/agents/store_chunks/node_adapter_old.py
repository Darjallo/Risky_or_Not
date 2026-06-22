from typing import Any, AsyncGenerator, Callable, Dict
import uuid

import aiohttp
import os

from ethelflow.agents.store_chunks.models import StoreChunksRequest, StoreChunksResponse

# STORE_CHUNKS_URL: str = "http://store-chunks.default.svc:8000/store_chunks"
STORE_CHUNKS_URL: str = "http://store-chunks:8000/store_chunks"

def _as_uuid(val: Any, field_name: str) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    if val is None:
        raise ValueError(f"{field_name} is required")
    try:
        return uuid.UUID(str(val))
    except Exception as e:
        raise ValueError(f"Invalid UUID format for {field_name}: {val!r}") from e


def _as_bool(val: Any, default: bool = True) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")


def store_chunks_node(
    text_id_key: str = "text_id",
    chunks_key: str = "chunks",
    method_key: str = "method",
    output_key: str = "store_chunks_response",
    replace_key: str = "replace",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        text_id = _as_uuid(state.get(text_id_key), text_id_key)

        chunks = state.get(chunks_key)
        if not isinstance(chunks, list) or not all(isinstance(c, str) for c in chunks):
            raise ValueError(f"Expected list[str] for {chunks_key}, got {type(chunks)}")

        method = state.get(method_key)
        if not isinstance(method, str) or not method.strip():
            raise ValueError(f"Expected non-empty str for {method_key}, got {method!r}")

        replace = _as_bool(state.get(replace_key), default=True)

        req = StoreChunksRequest(text_id=text_id, chunks=chunks, method=method, replace=replace)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                STORE_CHUNKS_URL,
                json=req.model_dump(mode="json"),
                timeout=60,
            ) as resp:
                payload = await resp.json()
                if resp.status != 200:
                    raise ValueError(f"store_chunks HTTP {resp.status}: {payload}")

        data = StoreChunksResponse.model_validate(payload)
        if not data.success:
            raise ValueError(f"store_chunks failed: {data.message}")

        # Keep state JSON-friendly (UUIDs as strings)
        yield {output_key: data.model_dump(mode="json")}

    return node

