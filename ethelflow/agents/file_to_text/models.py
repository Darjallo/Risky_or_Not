from __future__ import annotations

import uuid
from typing import List, Optional
from pydantic import BaseModel


class FileToTextRequest(BaseModel):
    document_id: uuid.UUID


class PageText(BaseModel):
    page: int
    text: str


class FileToTextResponse(BaseModel):
    # Backward compatible: existing flows can still read .text
    text: str

    # New: page-aware extraction output for page-aware chunking
    pages: List[PageText] = []

    # Optional source label for metadata/citations
    document_id: Optional[uuid.UUID] = None
    document_name: Optional[str] = None
    content_type: Optional[str] = None
