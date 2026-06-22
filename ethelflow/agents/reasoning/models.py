from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional
import uuid


class InlineImage(BaseModel):
    content_type: str
    data_base64: str


class ReasoningRequest(BaseModel):
    # routing
    tenant: str = Field(..., min_length=1)

    # content (same as before)
    document_id: uuid.UUID | None = None
    content_type: str | None = None
    document_ids: list[uuid.UUID] | None = None
    content_types: list[str] | None = None
    images: list[InlineImage] | None = None

    prompt: str | None = None
    messages: list[dict] | None = None

    reasoning_effort: Literal["low", "medium", "high"] | None = None
    stream: bool = False

    # Backward-compatible escape hatch
    deployment: str | None = None

    @model_validator(mode="after")
    def _validate(self):
        if self.document_id and not self.content_type:
            raise ValueError("content_type must be provided if document_id is provided")

        if self.document_ids:
            if not self.content_types or len(self.content_types) != len(self.document_ids):
                raise ValueError("content_types must match document_ids length")

        if (self.prompt is None or self.prompt == "") and (not self.messages):
            raise ValueError("Either prompt or messages must be provided")

        if self.prompt is not None and self.messages is not None:
            # keep your previous rule to avoid ambiguous composition
            raise ValueError("Only one of prompt or messages can be provided")

        return self


class ReasoningResponse(BaseModel):
    response: str
    tenant: str
    provider: str
    deployment: str

