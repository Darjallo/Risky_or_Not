from __future__ import annotations

import uuid
from typing import Any, Dict, List, TypedDict

from langgraph.graph import StateGraph

from ethelflow.agents.embedding.node_adapter import embedding_node
from ethelflow.agents.file_to_text.node_adapter import file_to_text_node
from ethelflow.agents.store_chunks.models import StoreChunksResponse
from ethelflow.agents.store_chunks.node_adapter import store_chunks_node
from ethelflow.agents.store_text.node_adapter import store_text_node
from ethelflow.agents.store_vectors.node_adapter import store_vectors_node

# New page-aware chunking node. Put chunk_pages_node_adapter.py somewhere importable, e.g.
# ethelflow/agents/chunk_pages/node_adapter.py, then update this import accordingly.
# try:
#     from ethelflow.agents.chunk_pages.node_adapter import chunk_pages_node
# except Exception:
#     # Allows local testing if this file is in the same directory as chunk_pages_node_adapter.py
#     from chunk_pages_node_adapter import chunk_pages_node

from ethelflow.agents.chunk_text.node_adapter import chunk_pages_node


class E2EEmbeddingPageAwareState(TypedDict, total=False):
    tenant: str
    embedding_space: str | None

    document_id: uuid.UUID | str
    extractor: str
    method: str

    text: str
    pages: List[Dict[str, Any]]
    document_name: str | None
    content_type: str | None

    text_id: str
    store_text_response: dict

    chunks: List[str]
    chunk_metadata: List[dict]

    store_chunks_response: dict
    chunk_ids: List[uuid.UUID]

    embeddings: List[List[float]]
    store_vectors_response: dict


def prepare_for_store_vectors(state: E2EEmbeddingPageAwareState) -> E2EEmbeddingPageAwareState:
    store_chunks_response = StoreChunksResponse.model_validate(state["store_chunks_response"])
    state["chunk_ids"] = store_chunks_response.chunk_ids
    return state


def make_store_chunks_node_with_optional_metadata(**kwargs):
    """
    Use a metadata-aware store_chunks_node if available.

    This assumes you update store_chunks_node to accept chunk_metadata_key.
    If your current store_chunks_node does not yet support this argument, this
    fallback keeps the flow runnable, but metadata will NOT be saved until the
    store_chunks service/model are updated.
    """
    try:
        return store_chunks_node(**kwargs, chunk_metadata_key="chunk_metadata")
    except TypeError:
        # Backward-compatible fallback; metadata is generated but not persisted.
        return store_chunks_node(**kwargs)


async def run(thread_id: uuid.UUID, context=None, stream: bool = False, checkpointer=None, command=None):
    context = context or {}

    tenant = context.get("tenant")
    if not isinstance(tenant, str) or not tenant.strip():
        raise ValueError("context['tenant'] is required (non-empty str)")

    initial_state: E2EEmbeddingPageAwareState = {
        "tenant": tenant,
        "embedding_space": context.get("embedding_space"),
        "document_id": context.get("document_id"),
        "extractor": context.get("extractor", "file_to_text_pageaware"),
        "method": context.get("method", "recursive_char_1000_100_pageaware"),
    }

    workflow = StateGraph(E2EEmbeddingPageAwareState)

    file_to_text = file_to_text_node(
        document_id_key="document_id",
        output_key="text",
        output_pages_key="pages",
        output_document_name_key="document_name",
        output_content_type_key="content_type",
    )

    store_text = store_text_node(
        document_id_key="document_id",
        extractor_key="extractor",
        text_key="text",
        output_text_id_key="text_id",
        output_key="store_text_response",
    )

    chunk_pages = chunk_pages_node(
        pages_key="pages",
        document_id_key="document_id",
        document_name_key="document_name",
        content_type_key="content_type",
        output_chunks_key="chunks",
        output_metadata_key="chunk_metadata",
        chunk_size=1000,
        chunk_overlap=100,
    )

    store_chunks = make_store_chunks_node_with_optional_metadata(
        text_id_key="text_id",
        chunks_key="chunks",
        method_key="method",
        output_key="store_chunks_response",
    )

    embedding = embedding_node(
        input_texts_key="chunks",
        tenant_key="tenant",
        space_key="embedding_space",
        output_key="embeddings",
    )

    store_vectors = store_vectors_node(
        embeddings_key="embeddings",
        chunk_ids_key="chunk_ids",
        tenant_key="tenant",
        space_key="embedding_space",
    )

    workflow.add_node("file_to_text", file_to_text)
    workflow.add_node("store_text", store_text)
    workflow.add_node("chunk_pages", chunk_pages)
    workflow.add_node("store_chunks", store_chunks)
    workflow.add_node("embedding", embedding)
    workflow.add_node("prepare_for_store_vectors", prepare_for_store_vectors)
    workflow.add_node("store_vectors", store_vectors)

    workflow.set_entry_point("file_to_text")
    workflow.add_edge("file_to_text", "store_text")
    workflow.add_edge("store_text", "chunk_pages")
    workflow.add_edge("chunk_pages", "store_chunks")
    workflow.add_edge("store_chunks", "embedding")
    workflow.add_edge("embedding", "prepare_for_store_vectors")
    workflow.add_edge("prepare_for_store_vectors", "store_vectors")
    workflow.set_finish_point("store_vectors")

    app = workflow.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": str(thread_id)}}

    if stream:
        async for item in app.astream(initial_state, config=config):
            yield item
    else:
        yield await app.ainvoke(initial_state, config=config)
