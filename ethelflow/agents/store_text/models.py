from pydantic import BaseModel
from typing import Optional
import uuid


class StoreTextRequest(BaseModel):
    document_id: uuid.UUID
    extractor: str          # e.g. "pdfminer", "ocr", "bs4"
    text: Optional[str] = None


class StoreTextResponse(BaseModel):
    success: bool
    message: str = ""
    text_id: uuid.UUID | None = None
    created: bool = False

