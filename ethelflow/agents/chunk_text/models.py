from typing import List
from pydantic import BaseModel, field_validator, model_validator
from datetime import datetime


class ChunkingRequest(BaseModel):
    text: str
    chunk_size: int
    chunk_overlap: int

    @field_validator("chunk_size", "chunk_overlap")
    @classmethod
    def validate_positive(cls, v):
        if v <= 0:
            raise ValueError("Value must be greater than 0")
        return v

    @model_validator(mode="after")
    def validate_chunk_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return self


class ChunkingResponse(BaseModel):
    created: datetime
    chunks: List[str] = []
