from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict
import aiohttp
import uuid

from ethelflow.agents.file_to_text.models import FileToTextRequest, FileToTextResponse

FILE_TO_TEXT_URL = "http://file-to-text:8000/file_to_text"


def _as_uuid(value: Any, field_name: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        return uuid.UUID(value)
    raise ValueError(f"Expected UUID or UUID string for {field_name}, got {type(value)}")


def file_to_text_node(
    document_id_key: str = "document_id",
    output_key: str = "text",
    output_pages_key: str = "pages",
    output_document_name_key: str = "document_name",
    output_content_type_key: str = "content_type",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        document_id = _as_uuid(state.get(document_id_key), document_id_key)
        request = FileToTextRequest(document_id=document_id)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                FILE_TO_TEXT_URL,
                json=request.model_dump(mode="json"),
                timeout=300,
            ) as response:
                if response.status != 200:
                    error_detail = await response.text()
                    raise ValueError(
                        f"File-to-text service returned status {response.status}: {error_detail}"
                    )

                response_data = await response.json()
                data = FileToTextResponse.model_validate(response_data)

        yield {
            output_key: data.text,
            output_pages_key: [p.model_dump(mode="json") for p in data.pages],
            output_document_name_key: data.document_name,
            output_content_type_key: data.content_type,
        }

    return node
