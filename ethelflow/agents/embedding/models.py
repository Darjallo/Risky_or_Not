from __future__ import annotations

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class EmbeddingRequest(BaseModel):
    # Who is asking (used to route via catalog.tenants)
    tenant: str = Field(..., min_length=1)

    # What to embed
    texts: List[str]

    # Optional: explicitly choose embedding space (otherwise tenant default)
    space: Optional[str] = None

    # Backward-compatible escape hatch (discouraged):
    # if provided, service will try to use this deployment directly.
    deployment: Optional[str] = None


class EmbeddingResponse(BaseModel):
    embeddings: List[List[float]]

    # What was actually used
    tenant: str
    space: str
    provider: str
    deployment: str

    # Keep JSON-friendly
    usage: Optional[Dict[str, Any]] = None

