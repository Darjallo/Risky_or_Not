from pydantic import BaseModel, Field
from typing import List, Optional
import uuid


class RenderedImageRef(BaseModel):
    position: int
    pages: List[int]  # 1-based page numbers
    temp_s3_key: str

    mime_type: str = "image/png"
    byte_size: int
    width: int
    height: int


class FileToImagesRequest(BaseModel):
    document_id: uuid.UUID

    # Grouping spec:
    # Each inner list can be:
    # - [start, end] interpreted as inclusive range (works for [2,3] and [4,6])
    # - [5,7,8] interpreted as explicit page list
    groups: List[List[int]] = Field(..., min_length=1)

    dpi: int = 150
    image_format: str = "png"
    layout: str = "vertical"
    renderer: str = "pymupdf"

    # Optional: caller can choose the temp prefix; otherwise generated.
    # Recommended to pass through the flow state and cleanup at end.
    temp_prefix: Optional[str] = None


class FileToImagesResponse(BaseModel):
    document_id: uuid.UUID
    renderer: str
    dpi: int
    image_format: str
    layout: str
    groups: List[List[int]]

    temp_prefix: str
    images: List[RenderedImageRef]


class CleanupTempRequest(BaseModel):
    temp_prefix: str


class CleanupTempResponse(BaseModel):
    success: bool
    deleted: int = 0
    message: str = ""

