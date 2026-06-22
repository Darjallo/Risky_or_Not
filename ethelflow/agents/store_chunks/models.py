# from typing import List, Optional
# import uuid

# from pydantic import BaseModel, Field


# class StoreChunksRequest(BaseModel):
#     # chunksets hang off document_texts.id
#     text_id: uuid.UUID
#     chunks: List[str]
#     method: str

#     # If true, delete any existing chunksets for (text_id, method) first
#     replace: bool = True


# class StoreChunksResponse(BaseModel):
#     success: bool
#     message: str = ""
#     chunk_set_id: Optional[uuid.UUID] = None
#     chunk_ids: List[uuid.UUID] = Field(default_factory=list)


# for text chunks with metadata about page

from __future__ import annotations

from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class StoreChunksRequest(BaseModel):
    text_id: uuid.UUID | str
    chunks: List[str] = Field(default_factory=list)
    method: str
    replace: bool = True
    

    # New, optional, aligned with chunks by index.
    chunk_metadata: List[Dict[str, Any]] = Field(default_factory=list)


class StoreChunksResponse(BaseModel):
    success: bool = True
    message: str = ""
    chunk_ids: List[uuid.UUID] = Field(default_factory=list)

