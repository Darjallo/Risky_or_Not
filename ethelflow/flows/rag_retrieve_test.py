import uuid
from typing import List, TypedDict

from langgraph.graph import StateGraph

from ethelflow.agents.embedding.node_adapter import embedding_node
from ethelflow.agents.search_vectors.node_adapter import search_vectors_node
from ethelflow.agents.retrieve_chunks.node_adapter import retrieve_chunks_node


class RAGRetrieveTestState(TypedDict, total=False):
    # Inputs
    tenant: str
    embedding_space: str  # optional override (otherwise tenant default in catalog)
    document_ids: List[uuid.UUID] | List[str]
    prompt: str
    extractor: str
    method: str
    top_k: int

    # Internal
    prompts: List[str]
    embeddings: List[List[float]]
    query_embedding: List[float]

    search_vectors_response: dict
    hit_chunk_ids: List[str] | List[uuid.UUID]

    retrieve_chunks_response: dict
    chunk_texts: List[str]  # final output (for now)


def prepare_prompt_list(state: RAGRetrieveTestState) -> RAGRetrieveTestState:
    prompt = state.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    state["prompts"] = [prompt]
    return state


def prepare_query_embedding(state: RAGRetrieveTestState) -> RAGRetrieveTestState:
    embeddings = state.get("embeddings")
    if (
        not isinstance(embeddings, list)
        or not embeddings
        or not isinstance(embeddings[0], list)
    ):
        raise ValueError("Expected embeddings from embedding node (list[list[float]])")
    state["query_embedding"] = embeddings[0]
    return state


async def run(
    thread_id: uuid.UUID,
    context=None,
    stream: bool = False,
    checkpointer=None,
    command=None,
):
    """
    Flow: rag_retrieve_test

    Inputs expected in context:
      - tenant (required)
      - document_ids (required; list[uuid] or list[str])
      - prompt (required; str)
      - embedding_space (optional; str)
      - extractor (optional; default "file_to_text")
      - method (optional; default "recursive_char_1000_100_htmlstrip")
      - top_k (optional; default 10)

    Output (for now):
      - chunk_texts: list[str]
    """
    context = context or {}

    initial_state: RAGRetrieveTestState = {
        "tenant": context.get("tenant"),  # critical
        "embedding_space": context.get("embedding_space"),
        "document_ids": context.get("document_ids"),
        "prompt": context.get("prompt"),
        "extractor": context.get("extractor", "file_to_text"),
        "method": context.get("method", "recursive_char_1000_100_htmlstrip"),
        "top_k": int(context.get("top_k", 10)),
    }

    # Fail fast
    if not isinstance(initial_state.get("tenant"), str) or not initial_state["tenant"].strip():
        raise ValueError("context.tenant is required and must be a non-empty string")
    if not isinstance(initial_state.get("document_ids"), list) or not initial_state["document_ids"]:
        raise ValueError("context.document_ids is required and must be a non-empty list")
    if not isinstance(initial_state.get("prompt"), str) or not initial_state["prompt"].strip():
        raise ValueError("context.prompt is required and must be a non-empty string")

    workflow = StateGraph(RAGRetrieveTestState)

    workflow.add_node("prepare_prompt_list", prepare_prompt_list)

    embed = embedding_node(
        input_texts_key="prompts",
        tenant_key="tenant",
        space_key="embedding_space",
        output_key="embeddings",
    )
    workflow.add_node("embedding", embed)

    workflow.add_node("prepare_query_embedding", prepare_query_embedding)

    # ✅ Minimal fix: adapter expects query_vector_key, but our state key is query_embedding
    search = search_vectors_node(
        document_ids_key="document_ids",
        extractor_key="extractor",
        method_key="method",
        tenant_key="tenant",
        space_key="embedding_space",
        query_vector_key="query_embedding",  # <-- FIX
        top_k_key="top_k",
        output_key="search_vectors_response",
        output_chunk_ids_key="hit_chunk_ids",
    )
    workflow.add_node("search_vectors", search)

    retrieve = retrieve_chunks_node(
        chunk_ids_key="hit_chunk_ids",
        tenant_key="tenant",
        output_key="retrieve_chunks_response",
        output_texts_key="chunk_texts",
    )
    workflow.add_node("retrieve_chunks", retrieve)

    workflow.set_entry_point("prepare_prompt_list")
    workflow.add_edge("prepare_prompt_list", "embedding")
    workflow.add_edge("embedding", "prepare_query_embedding")
    workflow.add_edge("prepare_query_embedding", "search_vectors")
    workflow.add_edge("search_vectors", "retrieve_chunks")
    workflow.set_finish_point("retrieve_chunks")

    app = workflow.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": str(thread_id)}}

    if stream:
        async for item in app.astream(initial_state, config=config):
            yield item
    else:
        yield await app.ainvoke(initial_state, config=config)

