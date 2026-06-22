from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: Any  # keep permissive for now (string or richer)


class ChatCompletionsRequest(BaseModel):
    model: str = Field(default="rag_intent_chat")
    messages: List[ChatMessage]
    stream: bool = False
    user: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ResponsesRequest(BaseModel):
    model: str = Field(default="rag_intent_chat")
    input: Any  # OpenAI allows string or structured input; keep permissive
    conversation: Optional[str] = None
    stream: bool = False
    user: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    document_ids: Optional[List[str]] = None


