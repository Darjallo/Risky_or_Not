from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import uuid


class StoreImageItem(BaseModel):
    position: int
    pages: List[int]  # 1-based page numbers

    temp_s3_key: str

    mime_type: str = "image/png"
    byte_size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class StoreImagesRequest(BaseModel):
    document_id: uuid.UUID

    renderer: str = "pymupdf"
    dpi: int = 150
    image_format: str = "png"
    layout: str = "vertical"

    groups: List[List[int]] = Field(..., min_length=1)

    # Full manifest/provenance that you want preserved (typically FileToImagesResponse JSON)
    manifest: Dict[str, Any]

    # Items to store permanently (usually manifest["images"])
    images: List[StoreImageItem]

    override: bool = False
    cleanup_temp: bool = False  # optional; flows can also call cleanup_temp explicitly


class StoreImagesResponse(BaseModel):
    success: bool
    message: str = ""

    image_set_id: uuid.UUID | None = None
    image_ids: List[uuid.UUID] = Field(default_factory=list)

    created: bool = False
    updated: bool = False

