from typing import Literal
from uuid import UUID
from pydantic import BaseModel, model_validator


class ExecutionRequest(BaseModel):
    image: str
    type: Literal["python", "r", "maxima"]
    code_b64: str
    stream: bool | None = False

    @model_validator(mode="after")
    def validate(self):
        if not self.code_b64:
            raise ValueError("code_b64 is required for all execution types")
        return self


class ExecutionResult(BaseModel):
    execution_id: UUID
    return_code: int
    stdout: str
    stderr: str

