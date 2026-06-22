from pydantic import BaseModel
import uuid


class FileToTextRequest(BaseModel):
    document_id: uuid.UUID


class FileToTextResponse(BaseModel):
    text: str
