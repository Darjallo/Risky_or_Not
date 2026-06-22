from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CompleteTemplateRequest(BaseModel):
    template: str = Field(..., min_length=1, description="Mustache template string")
    fields: Dict[str, Any] = Field(default_factory=dict, description="Context fields used for rendering")

    # Normalize empty strings/empty containers to False so {{#var}} sections behave as expected.
    normalize_empties: bool = Field(
        default=True,
        description="If true, empty strings/lists/dicts become False for section rendering",
    )


class CompleteTemplateResponse(BaseModel):
    success: bool
    message: str = ""
    rendered: Optional[str] = None

