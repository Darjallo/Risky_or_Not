# -*- coding: utf-8 -*-
"""
Created on Tue May 12 12:51:43 2026
Updated: metadata-aware RAG source labels for document/page citations.

@author: oppna
"""
from typing import Any, List, Dict
import aiohttp
import os

from ethelflow.tutor.state import TutorState

COMPLETE_TEMPLATE_URL = os.getenv("COMPLETE_TEMPLATE_URL", "http://complete-template:8000/complete_template")

# Flow-owned vanilla template (single source of truth; no files needed)
# This version expects each chunk item to contain a human-readable source_label,
# e.g. "bovine_udder.pdf, p. 3" instead of exposing raw chunk IDs.
VANILLA_TEMPLATE = """You are a helpful tutor for {{subject}}.
Student level: {{student_level}}.
Current topic: {{topic}}.

Use the provided excerpts as the primary basis for your answer.
Cite sources using their source labels, for example: (Source 1: document.pdf, p. 12).
If page information is unavailable, use chunk label, for example: (Source 1: document.pdf, chunk 102).
Do not cite raw chunk IDs. Do not cite anything if no excerpts were used.
If the excerpts are insufficient, say so clearly and answer cautiously.
Stay on topic unless the user clearly shifts.
Answer with:
- a simple explanation
- a tiny example, if appropriate.

{{#history}}
Conversation so far:
{{history}}
{{/history}}

Student question:
{{prompt}}

{{#chunks}}
Relevant excerpts:
{{#chunks}}
[Source {{n}}: {{source_label}}]
{{text}}

{{/chunks}}
{{/chunks}}
"""


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


def _format_source_label(meta: Any) -> str:
    """
    Convert chunk metadata into a human-readable source label.

    Expected metadata examples:
      {"document_name": "bovine_udder.pdf", "page_start": 3, "page_end": 3}
      {"filename": "bovine_udder.pdf", "page": 3}
      {"source_document": "bovine_udder.pdf", "page_start": 3, "page_end": 4}

    Falls back gracefully when metadata is absent or incomplete.
    """
    if not isinstance(meta, dict):
        return "unknown source"

    document_name = (
        meta.get("document_name")
        or meta.get("filename")
        or meta.get("source_document")
        or meta.get("source")
        or meta.get("document_id")
        or "unknown document"
    )
    
    page_start = meta.get("page_start", meta.get("page"))
    page_end = meta.get("page_end", page_start)

    if page_start is not None:
        if page_end is None or page_end == page_start:
            return f"{document_name}, p. {page_start}"
        return f"{document_name}, pp. {page_start}-{page_end}"

    chunk_position = meta.get("chunk_position")
    if chunk_position is not None:
        return f"{document_name}, chunk {chunk_position}"

    return f"{document_name}"


def prepare_prompt_list(state: TutorState) -> TutorState:
    state["prompts"] = [_require_nonempty_str(state.get("last_user_msg"), "last_user_msg")]
    return state


def prepare_query_embedding(state: TutorState) -> TutorState:
    embeddings = state.get("embeddings")
    if not isinstance(embeddings, list) or not embeddings or not isinstance(embeddings[0], list):
        raise ValueError("Expected embeddings from embedding node (list[list[float]])")
    state["query_embedding"] = embeddings[0]
    return state


def prepare_history_text(state: TutorState) -> TutorState:
    """
    Convert canonical messages into a plain transcript string for the template.
    """
    msgs = state.get("history") or []
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


def prepare_template_fields(state: TutorState) -> TutorState:
    prompt = _require_nonempty_str(state.get("last_user_msg"), "last_user_msg")

    chunks = state.get("chunk_texts") or []
    if not isinstance(chunks, list):
        chunks = []

    # Metadata must be provided by retrieve_chunks_node, aligned by index with chunk_texts.
    # If metadata is unavailable, the template still works but source labels become "unknown source".
    chunk_metadata = state.get("chunk_metadata") or []
    if not isinstance(chunk_metadata, list):
        chunk_metadata = []

    chunk_items: List[Dict[str, Any]] = []
    for i, text in enumerate(chunks):
        if not isinstance(text, str) or not text.strip():
            continue

        meta = chunk_metadata[i] if i < len(chunk_metadata) else {}
        source_label = _format_source_label(meta)

        chunk_items.append(
            {
                "n": i + 1,
                "text": text.strip(),
                "metadata": meta,
                "source_label": source_label,
            }
        )

    fields: Dict[str, Any] = {
        "prompt": prompt,
        "history": state.get("history_text", ""),
        "chunks": chunk_items,
        "chunks_joined": "\n\n".join([c["text"] for c in chunk_items]),
        "source_labels": [c["source_label"] for c in chunk_items],
        "top_k": state.get("top_k", 10),
        "extractor": state.get("extractor", ""),
        "method": state.get("method", ""),
        "tenant": state.get("tenant", ""),
        "embedding_space": state.get("embedding_space") or "",
        "subject": state.get("subject", ""),
        "student_level": state.get("student_level", ""),
        "topic": state.get("current_topic_id", ""),
    }

    state["source_labels"] = [c["source_label"] for c in chunk_items]
    state["template_fields"] = fields
    return state


async def render_template(state: TutorState) -> TutorState:
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
