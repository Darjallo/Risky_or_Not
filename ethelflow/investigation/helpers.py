# -*- coding: utf-8 -*-
"""
Created on Thu Apr 16 17:21:51 2026

@author: oppna
"""

from typing import Dict, Any, Optional, List
import random
import json
import pandas as pd

from ethelflow.investigation.state import InvestigationState 


def _extract_user_message(context: Dict[str, Any]) -> str:
    """Extract the latest user message from supported context shapes."""
    user_message = context.get("user_message")
    if user_message is None:
        user_message = context.get("input")

    if user_message is None:
        messages = context.get("messages", [])
        if isinstance(messages, list):
            for msg in reversed(messages):
                if not isinstance(msg, dict):
                    continue
                if str(msg.get("role", "")).strip().lower() != "user":
                    continue

                content = msg.get("content", "")
                if isinstance(content, str):
                    user_message = content
                    break

                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part["text"])
                    user_message = "\n".join(parts).strip()
                    break

    if not isinstance(user_message, str):
        raise ValueError("'user_message', 'input', or user message in 'messages' must be a string")

    user_message = user_message.strip()
    if not user_message:
        raise ValueError(f"Could not extract user message from context: keys={list(context.keys())}")
    return user_message


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)]



# def _stage_docs_up_to(stage_documents: Any, current_stage: int) -> List[str]:
#     """
#     Accepts either:
#     - {"1": [doc_id], "2": [doc_id]}
#     - {1: [doc_id], 2: [doc_id]}
#     - [{"stage": 1, "document_ids": [...]}, ...]
#     """
#     docs: List[str] = []

#     if isinstance(stage_documents, dict):
#         for key, value in stage_documents.items():
#             try:
#                 stage = int(key)
#             except Exception:
#                 continue
#             if stage <= current_stage:
#                 docs.extend(_as_list(value))
#         return docs

#     if isinstance(stage_documents, list):
#         for item in stage_documents:
#             if not isinstance(item, dict):
#                 continue
#             try:
#                 stage = int(item.get("stage", 0))
#             except Exception:
#                 continue
#             if stage <= current_stage:
#                 docs.extend(_as_list(item.get("document_ids", [])))
#         return docs

#     return docs



def _stage_docs_up_to(stage_docs: Any, current_stage: int) -> list[str]:
    if not isinstance(stage_docs, dict):
        return []

    out: list[str] = []

    for s in range(1, int(current_stage) + 1):
        out.extend(_stage_docs_exact(stage_docs, s))

    # remove duplicates, preserve order
    seen = set()
    unique = []
    for doc_id in out:
        if doc_id not in seen:
            seen.add(doc_id)
            unique.append(doc_id)

    return unique

def _stage_docs_exact(stage_docs: Any, stage: int) -> list[str]:
    if not isinstance(stage_docs, dict):
        return []

    docs = (
        stage_docs.get(stage)
        or stage_docs.get(str(stage))
        or []
    )

    return _as_list(docs)

# def _stage_docs_exact(stage_documents: Any, stage_number: int) -> List[str]:
    
#     if isinstance(stage_documents, dict):
#         return _as_list(stage_documents.get(str(stage_number), stage_documents.get(stage_number, [])))

#     if isinstance(stage_documents, list):
#         docs: List[str] = []
#         for item in stage_documents:
#             if not isinstance(item, dict):
#                 continue
#             try:
#                 stage = int(item.get("stage", 0))
#             except Exception:
#                 continue
#             if stage == stage_number:
#                 docs.extend(_as_list(item.get("document_ids", [])))
#         return docs

#     return []


def _format_source_label(meta: Any) -> str:
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

    return str(document_name)


def _format_context_block(texts: List[str], metas: List[Dict[str, Any]], max_chars: int = 14000) -> str:
    blocks: List[str] = []
    total = 0

    for i, text in enumerate(texts or [], start=1):
        if not isinstance(text, str) or not text.strip():
            continue
        meta = metas[i - 1] if i - 1 < len(metas or []) else {}
        label = _format_source_label(meta)
        block = f"[Source {i}: {label}]\n{text.strip()}"
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)

    return "\n\n---\n\n".join(blocks).strip()


def _history_to_text(history: List[Dict[str, str]], max_turns: int = 10) -> str:
    safe = []
    for msg in history[-max_turns:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).strip()
        content = str(msg.get("content", "")).strip()
        if role and content:
            safe.append(f"{role}: {content}")
    return "\n".join(safe)



#_________________________



def get_topic(course: dict, topic_id: str) -> dict:
    topics = course.get("topics", [])
    for t in topics:
        # t must be a dict; if it's not, your COURSE["topics"] isn't the right structure
        if isinstance(t, dict) and t.get("topic_id") == topic_id:
            return t
    raise KeyError(f"topic_id '{topic_id}' not found. Available: {[x.get('topic_id') for x in topics if isinstance(x, dict)]}")






# def init_state(
#     subject: str="Plant Biology",
#     level: str="high school",
#     goals: str="prepare for exams",
#     course_id: str="plant_biology_101",
#     ) -> TutorState:

#     provider = PROVIDERS[course_id]
#     topics = provider.list_topics()
#     if not topics:
#         raise ValueError(f"No topics found for course_id={course_id!r}")
#     topic_order = [t["topic_id"] for t in topics]
#     first_topic_id = topic_order[0]


#     return TutorState(
#         course_id=course_id,

#         asked_question_ids=[],
#         current_topic_id=first_topic_id,
#         topic_order=topic_order, 

#         subject=subject,
#         student_level=level,
#         goals=goals,

#         history=[],
#         last_user_msg="",

#         mastery={first_topic_id: 0.0},

#         pending_quiz=None,
#         last_quiz_score=None,

#         coins=0,
#         coins_awarded_last=0,
#         grading_prompt="",
#         grading_raw_output="",
#         grading_result={},
#         grading_question_id=None,

#         draft_response=(
#             "## 👋 Welcome\n"
#             "Hello! I’m your tutor.\n"
#             "Are you ready to start a new lesson?"
#         ),
#         response_type="greeting",

#         quit=False,
#         last_analysis={},

#         awaiting_check=False,
#         last_check_question="",
#         last_check_question_id=None,
#         check_attempts=0,
#         ready_to_advance=False,

#     )

