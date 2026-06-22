from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Any, Dict, Literal, Optional


class IntentOption(BaseModel):
    description: str = Field(..., min_length=1)
    examples: list[str] | None = None


class IntentOptionsSpec(BaseModel):
    version: int = 1
    default_intent: str = "chat"
    options: Dict[str, IntentOption] = Field(default_factory=dict)
    confidence_threshold: float = 0.70


class IntentRequest(BaseModel):
    # routing
    tenant: str = Field(..., min_length=1)

    # content
    prompt: str | None = None
    messages: list[dict] | None = None

    # decision configuration
    intent_options: dict = Field(..., description="JSON object describing allowed intents/options")
    stream: bool = False  # not used for now, kept for symmetry
    deployment: str | None = None  # escape hatch, like reasoning

    @model_validator(mode="after")
    def _validate(self):
        if (self.prompt is None or self.prompt == "") and (not self.messages):
            raise ValueError("Either prompt or messages must be provided")
        if self.prompt is not None and self.messages is not None:
            raise ValueError("Only one of prompt or messages can be provided")
        if not isinstance(self.intent_options, dict) or not self.intent_options:
            raise ValueError("intent_options must be a non-empty JSON object")
        return self


class IntentResult(BaseModel):
    intent: str
    topic: str | None = None
    confidence: float = 0.0
    language: str | None = None
    reason: str | None = None


class IntentResponse(BaseModel):
    result: IntentResult
    tenant: str
    provider: str
    deployment: str
    raw: str | None = None

