from __future__ import annotations

import uuid
from typing import List, Optional

from pydantic import BaseModel, Field


class SearchVectorsRequest(BaseModel):
    tenant: str = Field(..., min_length=1)
    space: Optional[str] = None  # None => tenant default from catalog

    document_ids: List[uuid.UUID] = Field(default_factory=list)
    extractor: str = Field(..., min_length=1)
    method: str = Field(..., min_length=1)  # chunking method label

    query_vector: List[float]
    top_k: int = Field(10, ge=1, le=500)


class SearchVectorsResponse(BaseModel):
    success: bool
    message: str = ""

    tenant: Optional[str] = None
    space: Optional[str] = None
    store_table: Optional[str] = None

    chunk_ids: List[uuid.UUID] = Field(default_factory=list)
    distances: List[float] = Field(default_factory=list)

