from __future__ import annotations

import uuid
from typing import List, Optional

from pydantic import BaseModel, Field


class StoreVectorsRequest(BaseModel):
    chunk_ids: List[uuid.UUID]
    embeddings: List[List[float]]

    tenant: str = Field(..., min_length=1)
    space: Optional[str] = None  # if None, use tenant default from catalog


class StoreVectorsResponse(BaseModel):
    success: bool
    message: str = ""
    num_vectors_stored: int = 0

    tenant: Optional[str] = None
    space: Optional[str] = None
    store_table: Optional[str] = None

