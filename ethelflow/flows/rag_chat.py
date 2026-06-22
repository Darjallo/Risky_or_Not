import os
import uuid
from typing import Any, Dict, List, Optional, TypedDict

import aiohttp
from langgraph.graph import StateGraph

from ethelflow.agents.embedding.node_adapter import embedding_node
from ethelflow.agents.search_vectors.node_adapter import search_vectors_node
from ethelflow.agents.retrieve_chunks.node_adapter import retrieve_chunks_node

# K8s service DNS names (match your other agents)
# COMPLETE_TEMPLATE_URL = "http://complete-template.default.svc:8000/complete_template"
# REASONING_URL = "http://reasoning.default.svc:8000/reasoning"
COMPLETE_TEMPLATE_URL = "http://complete-template:8000/complete_template"
REASONING_URL = "http://reasoning:8000/reasoning"

# Default template path INSIDE the container image.
# Put a file here in-repo so it gets copied into the image at build time.
DEFAULT_TEMPLATE_PATH = "/app/ethelflow/templates/rag_chat.mustache"


class RAGChatState(TypedDict, total=False):
    # Inputs
    tenant: str
    embedding_space: Optional[str]
    document_ids: List[uuid.UUID] | List[str]  # optional; if absent -> no retrieval
    prompt: str
    history: Any  # list[dict(role,content)] preferred, but we'll be forgiving
    template_path: Optional[str]
    template: Optional[str]  # if provided, used instead of template_path
    extractor: str
    method: str
    top_k: int
    reasoning_effort: Optional[str]

    # Internal for retrieval
    prompts: List[str]
    embeddings: List[List[float]]
    query_embedding: List[float]

    search_vectors_response: dict
    hit_chunk_ids: List[str] | List[uuid.UUID]

    retrieve_chunks_response: dict
    chunk_texts: List[str]

    # Template assembly
    history_text: str
    template_fields: Dict[str, Any]
    final_prompt: str


def _require_nonempty_str(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return v


def _read_template_from_disk(path: str) -> str:
    # Allow relative paths (resolved from /app by default in image)
    if not os.path.isabs(path):
        path = os.path.join("/app", path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def prepare_prompt_list(state: RAGChatState) -> RAGChatState:
    prompt = _require_nonempty_str(state.get("prompt"), "prompt")
    state["prompts"] = [prompt]
    return state


def prepare_query_embedding(state: RAGChatState) -> RAGChatState:
    embeddings = state.get("embeddings")
    if (
        not isinstance(embeddings, list)
        or not embeddings
        or not isinstance(embeddings[0], list)
    ):
        raise ValueError("Expected embeddings from embedding node (list[list[float]])")
    state["query_embedding"] = embeddings[0]
    return state


def prepare_history_text(state: RAGChatState) -> RAGChatState:
    """
    Convert history into a plain transcript string for the template.
    Accepts:
      - list[{"role":"user"|"assistant"|"system", "content": "..."}]
      - list[str]
      - str
      - None
    """
    h = state.get("history")
    if h is None:
        state["history_text"] = ""
        return state

    if isinstance(h, str):
        state["history_text"] = h.strip()
        return state

    if isinstance(h, list):
        # list[str]
        if all(isinstance(x, str) for x in h):
            state["history_text"] = "\n".join(x.strip() for x in h if x and x.strip())
            return state

        # list[dict]
        out_lines: List[str] = []
        for item in h:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if role == "user":
                out_lines.append(f"User: {content}")
            elif role == "assistant":
                out_lines.append(f"Assistant: {content}")
            elif role == "system":
                out_lines.append(f"System: {content}")
            else:
                out_lines.append(content)

        state["history_text"] = "\n".join(out_lines).strip()
        return state

    # fallback
    state["history_text"] = str(h).strip()
    return state


def prepare_template_fields(state: RAGChatState) -> RAGChatState:
    prompt = _require_nonempty_str(state.get("prompt"), "prompt")
    chunks = state.get("chunk_texts") or []
    if not isinstance(chunks, list):
        chunks = []

    # Give Mustache something nice to iterate over (with index)
    chunk_items = [{"n": i + 1, "text": t} for i, t in enumerate(chunks) if isinstance(t, str) and t.strip()]

    fields: Dict[str, Any] = {
        "prompt": prompt,
        "history": state.get("history_text", ""),
        "chunks": chunk_items,                      # iterable section
        "chunks_joined": "\n\n".join([c["text"] for c in chunk_items]),
        "top_k": state.get("top_k", 10),
        "extractor": state.get("extractor", ""),
        "method": state.get("method", ""),
        "tenant": state.get("tenant", ""),
        "embedding_space": state.get("embedding_space") or "",
    }

    state["template_fields"] = fields
    return state


async def render_template(state: RAGChatState) -> RAGChatState:
    """
    Call complete-template service.
    - If state["template"] is provided, use it.
    - Else read from state["template_path"] (or DEFAULT_TEMPLATE_PATH).
    """
    template_text = state.get("template")
    if template_text is None:
        template_path = state.get("template_path") or DEFAULT_TEMPLATE_PATH
        template_text = _read_template_from_disk(template_path)

    fields = state.get("template_fields")
    if not isinstance(fields, dict):
        raise ValueError("template_fields missing or invalid")

    req = {
        "template": template_text,
        "fields": fields,
        # important: make empty strings falsey for sections
        "normalize_empties": True,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(COMPLETE_TEMPLATE_URL, json=req, timeout=60) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise ValueError(f"complete-template HTTP {resp.status}: {text}")
            data = await resp.json()

    if not data.get("success"):
        raise ValueError(f"complete-template failed: {data}")

    rendered = data.get("rendered")
    if not isinstance(rendered, str):
        raise ValueError("complete-template returned no rendered string")

    state["final_prompt"] = rendered
    return state


async def _call_reasoning_once(tenant: str, prompt: str, reasoning_effort: Optional[str]) -> str:
    body: Dict[str, Any] = {"tenant": tenant, "prompt": prompt, "stream": False}
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort

    async with aiohttp.ClientSession() as session:
        async with session.post(REASONING_URL, json=body, timeout=300) as resp:
            txt = await resp.text()
            if resp.status != 200:
                raise ValueError(f"reasoning HTTP {resp.status}: {txt}")
            data = await resp.json()

    # Be tolerant about response shape
    if isinstance(data, dict):
        if isinstance(data.get("response"), str):
            return data["response"]
        if isinstance(data.get("text"), str):
            return data["text"]
    return str(data)


async def _stream_reasoning(tenant: str, prompt: str, reasoning_effort: Optional[str]):
    """
    Yield *strings only* so /flow streaming does not crash.
    """
    body: Dict[str, Any] = {"tenant": tenant, "prompt": prompt, "stream": True}
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort

    async with aiohttp.ClientSession() as session:
        async with session.post(REASONING_URL, json=body, timeout=300) as resp:
            if resp.status != 200:
                raise ValueError(f"reasoning HTTP {resp.status}: {await resp.text()}")
            async for chunk in resp.content.iter_any():
                if not chunk:
                    continue
                yield chunk.decode("utf-8", errors="replace")


async def run(
    thread_id: uuid.UUID,
    context=None,
    stream: bool = False,
    checkpointer=None,
    command=None,
):
    """
    Flow: rag_chat

    Required in context:
      - tenant (str)
      - prompt (str)

    Optional:
      - history (list[{"role","content"}] or list[str] or str)
      - document_ids (list[uuid|str])  [if absent/empty -> retrieval skipped]
      - template_path (str)            [inside container]
      - template (str)                [inline template text; overrides template_path]
      - extractor (default "file_to_text")
      - method (default "recursive_char_1000_100_pageaware")
      - embedding_space (default None => catalog tenant default)
      - top_k (default 10)
      - reasoning_effort (optional)
    """
    context = context or {}

    tenant = _require_nonempty_str(context.get("tenant"), "tenant")
    prompt = _require_nonempty_str(context.get("prompt"), "prompt")

    initial_state: RAGChatState = {
        "tenant": tenant,
        "prompt": prompt,
        "history": context.get("history"),
        "document_ids": context.get("document_ids") or [],

        "template_path": context.get("template_path"),
        "template": context.get("template"),

        "extractor": context.get("extractor", "file_to_text"),
        "method": context.get("method", "recursive_char_1000_100_pageaware"),
        "embedding_space": context.get("embedding_space"),
        "top_k": int(context.get("top_k", 10)),
        "reasoning_effort": context.get("reasoning_effort"),
    }

    # Build retrieval + template graph (NO reasoning node inside graph)
    workflow = StateGraph(RAGChatState)

    workflow.add_node("prepare_prompt_list", prepare_prompt_list)

    embed = embedding_node(
        input_texts_key="prompts",
        tenant_key="tenant",
        space_key="embedding_space",
        output_key="embeddings",
    )
    workflow.add_node("embedding", embed)

    workflow.add_node("prepare_query_embedding", prepare_query_embedding)

    # retrieval nodes only run if document_ids provided
    search = search_vectors_node(
        document_ids_key="document_ids",
        extractor_key="extractor",
        method_key="method",
        tenant_key="tenant",
        space_key="embedding_space",
        query_vector_key="query_embedding",  # adapter expects this name
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

    workflow.add_node("prepare_history_text", prepare_history_text)
    workflow.add_node("prepare_template_fields", prepare_template_fields)
    workflow.add_node("render_template", render_template)

    workflow.set_entry_point("prepare_prompt_list")
    workflow.add_edge("prepare_prompt_list", "embedding")
    workflow.add_edge("embedding", "prepare_query_embedding")

    # If no docs, we still want a template (with empty chunks)
    # We'll implement this as: always run search/retrieve, but they should behave with empty inputs.
    workflow.add_edge("prepare_query_embedding", "search_vectors")
    workflow.add_edge("search_vectors", "retrieve_chunks")
    workflow.add_edge("retrieve_chunks", "prepare_history_text")
    workflow.add_edge("prepare_history_text", "prepare_template_fields")
    workflow.add_edge("prepare_template_fields", "render_template")
    workflow.set_finish_point("render_template")

    app = workflow.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": str(thread_id)}}

    # 1) Always compute final_prompt non-streaming (stable + debuggable)
    state = await app.ainvoke(initial_state, config=config)
    final_prompt = _require_nonempty_str(state.get("final_prompt"), "final_prompt")
    reasoning_effort = state.get("reasoning_effort")

    # 2) Then do reasoning (optionally streaming) — ONLY output strings in stream mode
    if stream:
        async for txt in _stream_reasoning(tenant=tenant, prompt=final_prompt, reasoning_effort=reasoning_effort):
            yield txt
    else:
        answer = await _call_reasoning_once(tenant=tenant, prompt=final_prompt, reasoning_effort=reasoning_effort)
        yield {
            "answer": answer,
            "chunks": state.get("chunk_texts", []),
            "final_prompt": final_prompt,  # very useful during debugging
            "search_vectors_response": state.get("search_vectors_response", {}),
        }

