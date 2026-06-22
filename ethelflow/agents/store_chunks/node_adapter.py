from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict
import aiohttp

from ethelflow.agents.store_chunks.models import StoreChunksRequest, StoreChunksResponse

# In-cluster service URL.
# If your Kubernetes service name/namespace differs, adjust this value.
# Alternative example: "http://store-chunks.default.svc:8000/store_chunks"
STORE_CHUNKS_URL = "http://store-chunks:8000/store_chunks"


def store_chunks_node(
    text_id_key: str = "text_id",
    chunks_key: str = "chunks",
    method_key: str = "method",
    output_key: str = "store_chunks_response",
    chunk_metadata_key: str = "chunk_metadata",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    """
    LangGraph node adapter for the store-chunks microservice.

    This version is page/source-metadata aware. It reads a list of chunk texts
    from `state[chunks_key]` and, optionally, a list of metadata dictionaries
    from `state[chunk_metadata_key]`. The metadata list must be aligned with
    the chunks list by index:

        chunks[i] <-> chunk_metadata[i]

    Typical metadata example:
        {
            "document_id": "...",
            "document_name": "bovine_udder.pdf",
            "content_type": "application/pdf",
            "page_start": 3,
            "page_end": 3,
            "chunk_index_on_page": 0,
            "chunking_method": "recursive_char_1000_100_pageaware"
        }

    Requires the StoreChunksRequest model and the store-chunks service to support
    an optional `chunk_metadata: list[dict]` field.
    """

    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        chunks = state.get(chunks_key, [])
        if not isinstance(chunks, list):
            raise ValueError(f"Expected list for {chunks_key}, got {type(chunks)}")

        # Metadata is optional for backward compatibility with non-page-aware flows.
        metadata = state.get(chunk_metadata_key, [])
        if metadata is None:
            metadata = []
        if not isinstance(metadata, list):
            raise ValueError(
                f"Expected list for {chunk_metadata_key}, got {type(metadata)}"
            )
        if metadata and len(metadata) != len(chunks):
            raise ValueError(
                "chunk metadata length mismatch: "
                f"{len(metadata)} metadata records for {len(chunks)} chunks"
            )

        req = StoreChunksRequest(
            text_id=state.get(text_id_key),
            chunks=chunks,
            method=state.get(method_key),
            chunk_metadata=metadata,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                STORE_CHUNKS_URL,
                json=req.model_dump(mode="json"),
                timeout=300,
            ) as resp:
                payload_text = await resp.text()
                if resp.status != 200:
                    raise ValueError(
                        f"store-chunks HTTP {resp.status}: {payload_text}"
                    )
                payload = await resp.json()

        data = StoreChunksResponse.model_validate(payload)
        yield {output_key: data.model_dump(mode="json")}

    return node
