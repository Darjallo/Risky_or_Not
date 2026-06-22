from __future__ import annotations

import os
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional, TypedDict

import aiohttp
from langgraph.graph import StateGraph

from ethelflow.agents.embedding.node_adapter import embedding_node
from ethelflow.agents.search_vectors.node_adapter import search_vectors_node
from ethelflow.agents.retrieve_chunks.node_adapter import retrieve_chunks_node
from ethelflow.agents.intent.node_adapter import intent_node

# services
# COMPLETE_TEMPLATE_URL = "http://complete-template.default.svc:8000/complete_template"
# REASONING_URL = "http://reasoning.default.svc:8000/reasoning"

COMPLETE_TEMPLATE_URL = os.getenv("COMPLETE_TEMPLATE_URL", "http://complete-template:8000/complete_template")
REASONING_URL = os.getenv("REASONING_URL", "http://reasoning:8000/reasoning")



# Flow-owned vanilla template (single source of truth; no files needed)
VANILLA_TEMPLATE = """You are a helpful assistant.

{{#history}}
Conversation so far:
{{history}}
{{/history}}

User question:
{{prompt}}

{{#chunks}}
Relevant excerpts:
{{#chunks}}
[{{n}}]
{{text}}

{{/chunks}}
{{/chunks}}

Answer clearly and cite the excerpt numbers when useful.
"""


# Flow-owned default intent options (clients/env can override by providing ctx.intent_options)
DEFAULT_INTENT_OPTIONS: Dict[str, Any] = {
    "version": 1,
    "default_intent": "chat",
    "options": {
        "simulation": {
            "description": "User wants the system to generate or run a simulation (interactive or computed).",
            "examples": ["simulate", "model this", "run a simulation", "numerically solve"],
        },
        "exercise": {
            "description": "User wants an interactive exercise/problem (practice, hints, answer checking).",
            "examples": ["give me an exercise", "quiz me", "practice problems", "check my answer"],
        },
        "visualization": {
            "description": "User wants a visualization (plot/diagram/image).",
            "examples": ["plot", "visualize", "draw", "show me a graph", "make an image"],
        },
    },
    "confidence_threshold": 0.70,
}


class DebugRAGIntentState(TypedDict, total=False):
    # raw context (in/out)
    context_in: Dict[str, Any]
    context_out: Dict[str, Any]

    # required
    tenant: str

    # canonical conversation inputs
    messages: List[Dict[str, Any]]
    user_text: str

    # intent
    intent_options: Dict[str, Any]
    intent_response: Dict[str, Any]
    intent: str
    intent_confidence: float
    intent_topic: Optional[str]
    intent_language: Optional[str]
    intent_threshold: float
    intent_matched: bool

    # rag inputs
    document_ids: List[uuid.UUID] | List[str]
    extractor: str
    method: str
    embedding_space: Optional[str]
    top_k: int
    reasoning_effort: Optional[str]

    template_path: Optional[str]
    template: Optional[str]

    # retrieval internals
    prompts: List[str]
    embeddings: List[List[float]]
    query_embedding: List[float]

    search_vectors_response: dict
    hit_chunk_ids: List[str] | List[uuid.UUID]

    retrieve_chunks_response: dict
    chunk_texts: List[str]

    # template assembly
    history_text: str
    template_fields: Dict[str, Any]
    final_prompt: str

    # output
    assistant_answer: str


def _require_nonempty_str(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return v.strip()


def _read_template_from_disk(path: str) -> str:
    # optional escape hatch: if someone *does* provide a template_path later,
    # we support it but fall back to VANILLA_TEMPLATE if missing.
    if not os.path.isabs(path):
        path = os.path.join("/app", path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_user_text(messages: list[dict]) -> str:
    """
    Extract latest user text from canonical messages.
    Supports either content=str or content=[{"type":"text","text":...}, ...]
    """
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue
        if str(m.get("role", "")).lower() != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c.strip()
        if isinstance(c, list):
            parts: List[str] = []
            for item in c:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            if parts:
                return "\n".join(parts).strip()
    return ""


def normalize_context(state: DebugRAGIntentState) -> DebugRAGIntentState:
    ctx = state.get("context_in") or {}
    if not isinstance(ctx, dict):
        raise ValueError("context must be an object")

    tenant = ctx.get("tenant") or state.get("tenant")
    tenant = _require_nonempty_str(tenant, "tenant")
    state["tenant"] = tenant

    # Canonical messages
    messages = ctx.get("messages")
    if not isinstance(messages, list):
        messages = []

    # Back-compat: prompt/history shape (optional)
    prompt = ctx.get("prompt")
    history = ctx.get("history")
    if prompt and not messages:
        messages = []
        if isinstance(history, list):
            for h in history:
                if isinstance(h, dict) and "role" in h and "content" in h:
                    messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": str(prompt)})

    if not messages:
        raise ValueError("context.messages is required (or supply legacy prompt/history keys)")

    user_text = _extract_user_text(messages)
    if not user_text:
        raise ValueError("Could not extract latest user message text")

    state["messages"] = messages
    state["user_text"] = user_text

    # intent options (default lives in flow; client/env may override)
    intent_options = ctx.get("intent_options")
    if not isinstance(intent_options, dict) or not intent_options:
        intent_options = deepcopy(DEFAULT_INTENT_OPTIONS)
    state["intent_options"] = intent_options

    # threshold (allow override)
    thr = 0.70
    if isinstance(intent_options.get("confidence_threshold"), (int, float)):
        thr = float(intent_options["confidence_threshold"])
    state["intent_threshold"] = thr

    # rag config (canonical)
    rag = ctx.get("rag") if isinstance(ctx.get("rag"), dict) else {}
    state["document_ids"] = rag.get("document_ids") or ctx.get("document_ids") or []
    state["extractor"] = rag.get("extractor", ctx.get("extractor", "file_to_text"))
    state["method"] = rag.get("method", ctx.get("method", "recursive_char_1000_100_htmlstrip"))
    state["embedding_space"] = rag.get("embedding_space", ctx.get("embedding_space"))
    state["top_k"] = int(rag.get("top_k", ctx.get("top_k", 10)))
    state["reasoning_effort"] = rag.get("reasoning_effort", ctx.get("reasoning_effort"))

    # template (canonical under rag, but allow top-level like old flow)
    state["template_path"] = rag.get("template_path", ctx.get("template_path"))
    state["template"] = rag.get("template", ctx.get("template"))

    return state


def prepare_prompt_list(state: DebugRAGIntentState) -> DebugRAGIntentState:
    state["prompts"] = [_require_nonempty_str(state.get("user_text"), "user_text")]
    return state


def prepare_query_embedding(state: DebugRAGIntentState) -> DebugRAGIntentState:
    embeddings = state.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings or not isinstance(embeddings[0], list):
        raise ValueError("Expected embeddings from embedding node (list[list[float]])")
    state["query_embedding"] = embeddings[0]
    return state


def prepare_history_text(state: DebugRAGIntentState) -> DebugRAGIntentState:
    """
    Convert canonical messages into a plain transcript string for the template.
    """
    msgs = state.get("messages") or []
    out: List[str] = []
    for item in msgs:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = item.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    t = c.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            content = "\n".join(parts).strip() if parts else ""
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue
        if role == "user":
            out.append(f"User: {content}")
        elif role == "assistant":
            out.append(f"Assistant: {content}")
        elif role == "system":
            out.append(f"System: {content}")
        else:
            out.append(content)

    state["history_text"] = "\n".join(out).strip()
    return state


def prepare_template_fields(state: DebugRAGIntentState) -> DebugRAGIntentState:
    prompt = _require_nonempty_str(state.get("user_text"), "user_text")
    chunks = state.get("chunk_texts") or []
    if not isinstance(chunks, list):
        chunks = []

    chunk_items = [{"n": i + 1, "text": t} for i, t in enumerate(chunks) if isinstance(t, str) and t.strip()]

    fields: Dict[str, Any] = {
        "prompt": prompt,
        "history": state.get("history_text", ""),
        "chunks": chunk_items,
        "chunks_joined": "\n\n".join([c["text"] for c in chunk_items]),
        "top_k": state.get("top_k", 10),
        "extractor": state.get("extractor", ""),
        "method": state.get("method", ""),
        "tenant": state.get("tenant", ""),
        "embedding_space": state.get("embedding_space") or "",
    }

    state["template_fields"] = fields
    return state


async def render_template(state: DebugRAGIntentState) -> DebugRAGIntentState:
    template_text = state.get("template")

    if template_text is None:
        # Optional override: template_path (if provided). Otherwise use vanilla.
        template_path = state.get("template_path")
        if isinstance(template_path, str) and template_path.strip():
            try:
                template_text = _read_template_from_disk(template_path.strip())
            except FileNotFoundError:
                template_text = VANILLA_TEMPLATE
        else:
            template_text = VANILLA_TEMPLATE

    fields = state.get("template_fields")
    if not isinstance(fields, dict):
        raise ValueError("template_fields missing or invalid")

    req = {"template": template_text, "fields": fields, "normalize_empties": True}

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


def decide_intent_branch(state: DebugRAGIntentState) -> str:
    intent = str(state.get("intent") or "").strip()
    conf = float(state.get("intent_confidence") or 0.0)
    thr = float(state.get("intent_threshold") or 0.70)

    spec = state.get("intent_options") or {}
    options = spec.get("options") if isinstance(spec, dict) else None
    allowed = set(options.keys()) if isinstance(options, dict) else set()

    matched = (intent in allowed) and (conf >= thr)
    state["intent_matched"] = matched
    return "intent" if matched else "chat"


def build_intent_answer(state: DebugRAGIntentState) -> DebugRAGIntentState:
    intent = state.get("intent") or "chat"
    topic = state.get("intent_topic") or "something"
    state["assistant_answer"] = f"*** I would make a {intent} about {topic} ***"
    return state


def finalize(state: DebugRAGIntentState) -> DebugRAGIntentState:
    """
    Common terminal node so both branches can finish cleanly.
    """
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

    if isinstance(data, dict):
        if isinstance(data.get("response"), str):
            return data["response"]
        if isinstance(data.get("text"), str):
            return data["text"]
    return str(data)


async def run(
    thread_id: uuid.UUID,
    context=None,
    stream: bool = False,
    checkpointer=None,
    command=None,
):
    """
    Flow: rag_intent_chat

    Input: canonical context dict (caller owns memory)
    Output (non-stream): dict with:
      - answer (str)
      - context (updated canonical context)
      - intent metadata
    """
    context = context or {}
    if not isinstance(context, dict):
        raise ValueError("context must be a dict")

    initial_state: DebugRAGIntentState = {"context_in": context}

    workflow = StateGraph(DebugRAGIntentState)

    workflow.add_node("normalize_context", normalize_context)

    # intent classification node (Option A): pass messages only (no prompt)
    workflow.add_node(
        "classify_intent",
        intent_node(
            tenant_key="tenant",
            prompt_key=None,              # <-- Option A: omit prompt
            messages_key="messages",
            intent_options_key="intent_options",
            output_key="intent_response",
        ),
    )

    def unpack_intent(state: DebugRAGIntentState) -> DebugRAGIntentState:
        resp = state.get("intent_response")
        try:
            result = (resp or {}).get("result") or {}
            state["intent"] = str(result.get("intent") or "chat")
            state["intent_topic"] = result.get("topic")
            state["intent_language"] = result.get("language")
            state["intent_confidence"] = float(result.get("confidence") or 0.0)
        except Exception:
            state["intent"] = "chat"
            state["intent_confidence"] = 0.0
        return state

    workflow.add_node("unpack_intent", unpack_intent)

    # chat/RAG path nodes
    workflow.add_node("prepare_prompt_list", prepare_prompt_list)

    embed = embedding_node(
        input_texts_key="prompts",
        tenant_key="tenant",
        space_key="embedding_space",
        output_key="embeddings",
    )
    workflow.add_node("embedding", embed)

    workflow.add_node("prepare_query_embedding", prepare_query_embedding)

    search = search_vectors_node(
        document_ids_key="document_ids",
        extractor_key="extractor",
        method_key="method",
        tenant_key="tenant",
        space_key="embedding_space",
        query_vector_key="query_embedding",
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

    workflow.add_node("build_intent_answer", build_intent_answer)
    workflow.add_node("finalize", finalize)

    workflow.set_entry_point("normalize_context")
    workflow.add_edge("normalize_context", "classify_intent")
    workflow.add_edge("classify_intent", "unpack_intent")

    workflow.add_conditional_edges(
        "unpack_intent",
        decide_intent_branch,
        {
            "intent": "build_intent_answer",
            "chat": "prepare_prompt_list",
        },
    )

    # chat path
    workflow.add_edge("prepare_prompt_list", "embedding")
    workflow.add_edge("embedding", "prepare_query_embedding")
    workflow.add_edge("prepare_query_embedding", "search_vectors")
    workflow.add_edge("search_vectors", "retrieve_chunks")
    workflow.add_edge("retrieve_chunks", "prepare_history_text")
    workflow.add_edge("prepare_history_text", "prepare_template_fields")
    workflow.add_edge("prepare_template_fields", "render_template")

    # converge to a single finish node
    workflow.add_edge("build_intent_answer", "finalize")
    workflow.add_edge("render_template", "finalize")
    workflow.set_finish_point("finalize")

    app = workflow.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": str(thread_id)}}
    state = await app.ainvoke(initial_state, config=config)

    tenant = _require_nonempty_str(state.get("tenant"), "tenant")

    # Determine assistant answer
    if isinstance(state.get("assistant_answer"), str) and state["assistant_answer"].strip():
        answer = state["assistant_answer"].strip()
    else:
        final_prompt = _require_nonempty_str(state.get("final_prompt"), "final_prompt")
        answer = await _call_reasoning_once(
            tenant=tenant,
            prompt=final_prompt,
            reasoning_effort=state.get("reasoning_effort"),
        )

    # Update canonical context_out
    ctx_out = dict(state.get("context_in") or {})
    msgs = ctx_out.get("messages")
    if not isinstance(msgs, list):
        msgs = []

    # ensure last user is present (caller should do it, but be tolerant)
    if not msgs or str(msgs[-1].get("role", "")).lower() != "user":
        msgs.append({"role": "user", "content": state.get("user_text", "")})

    msgs.append({"role": "assistant", "content": answer})
    ctx_out["messages"] = msgs

    routing = ctx_out.get("routing_state")
    if not isinstance(routing, dict):
        routing = {}
    routing.update(
        {
            "intent": state.get("intent"),
            "confidence": float(state.get("intent_confidence") or 0.0),
            "language": state.get("intent_language"),
            "topic": state.get("intent_topic"),
        }
    )
    ctx_out["routing_state"] = routing

    dbg = ctx_out.get("debug")
    if not isinstance(dbg, dict):
        dbg = {}
    dbg["intent_response"] = state.get("intent_response")
    dbg["intent_matched"] = bool(state.get("intent_matched"))
    dbg["chunk_texts"] = state.get("chunk_texts", [])
    dbg["search_vectors_response"] = state.get("search_vectors_response", {})
    dbg["final_prompt"] = state.get("final_prompt", None)
    ctx_out["debug"] = dbg

    if stream:
        yield answer
        return

    yield {
        "answer": answer,
        "context": ctx_out,
        "intent": state.get("intent"),
        "topic": state.get("intent_topic"),
        "intent_confidence": float(state.get("intent_confidence") or 0.0),
        "intent_matched": bool(state.get("intent_matched")),
    }

