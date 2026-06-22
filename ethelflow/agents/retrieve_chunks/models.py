from __future__ import annotations

from typing import List, Any, Dict
import uuid
from pydantic import BaseModel, Field


class RetrieveChunksRequest(BaseModel):
    tenant: str = Field(..., description="Tenant label (kept for consistency; currently unused by DB lookup)")
    chunk_ids: List[uuid.UUID] = Field(default_factory=list)


class RetrieveChunksResponse(BaseModel):
    success: bool
    message: str = ""
    chunk_ids: List[uuid.UUID] = Field(default_factory=list)
    chunk_texts: List[str] = Field(default_factory=list)
    chunk_metadata: List[Dict[str, Any]] = Field(default_factory=list)

