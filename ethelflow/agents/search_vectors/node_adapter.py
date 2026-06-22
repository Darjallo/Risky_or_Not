from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict, Optional, List
import aiohttp
import uuid
import os

from ethelflow.agents.search_vectors.models import SearchVectorsRequest, SearchVectorsResponse

# SEARCH_VECTORS_URL: str = "http://search-vectors.default.svc:8000/search_vectors"
SEARCH_VECTORS_URL: str = "http://search-vectors:8000/search_vectors"



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


def search_vectors_node(
    document_ids_key: str = "document_ids",
    extractor_key: str = "extractor",
    method_key: str = "method",
    query_vector_key: str = "query_vector",
    tenant_key: str = "tenant",
    space_key: str = "embedding_space",  # optional
    top_k_key: str = "top_k",
    output_key: str = "search_vectors_response",
    output_chunk_ids_key: str = "chunk_ids",  # convenience for downstream
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        tenant = state.get(tenant_key)
        if not isinstance(tenant, str) or not tenant.strip():
            raise ValueError(f"Expected non-empty str for {tenant_key}, got {tenant!r}")

        space: Optional[str] = state.get(space_key)
        if space is not None and (not isinstance(space, str) or not space.strip()):
            raise ValueError(f"Expected str|None for {space_key}, got {space!r}")

        document_ids = _as_uuid_list(state.get(document_ids_key, []), document_ids_key)

        extractor = state.get(extractor_key)
        if not isinstance(extractor, str) or not extractor.strip():
            raise ValueError(f"Expected non-empty str for {extractor_key}, got {extractor!r}")

        method = state.get(method_key)
        if not isinstance(method, str) or not method.strip():
            raise ValueError(f"Expected non-empty str for {method_key}, got {method!r}")

        qvec = state.get(query_vector_key)
        if not isinstance(qvec, list) or not all(isinstance(x, (int, float)) for x in qvec):
            raise ValueError(f"Expected list[float] for {query_vector_key}, got {type(qvec)}")

        top_k = state.get(top_k_key, 10)
        if not isinstance(top_k, int) or top_k < 1:
            raise ValueError(f"Expected int>=1 for {top_k_key}, got {top_k!r}")

        req = SearchVectorsRequest(
            tenant=tenant,
            space=space,
            document_ids=document_ids,
            extractor=extractor,
            method=method,
            query_vector=[float(x) for x in qvec],
            top_k=top_k,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                SEARCH_VECTORS_URL,
                json=req.model_dump(mode="json"),
                timeout=300,
            ) as resp:
                payload_text = await resp.text()
                if resp.status != 200:
                    raise ValueError(f"search-vectors HTTP {resp.status}: {payload_text}")
                payload = await resp.json()

        data = SearchVectorsResponse.model_validate(payload)
        if not data.success:
            raise ValueError(f"search_vectors failed: {data.message}")

        # Provide both the full response and chunk_ids directly for downstream nodes.
        yield {
            output_key: data.model_dump(mode="json"),
            output_chunk_ids_key: [str(cid) for cid in data.chunk_ids],
        }

    return node

