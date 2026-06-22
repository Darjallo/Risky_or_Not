from typing import Callable, Dict, Any, AsyncGenerator
from ethelflow.agents.file_to_text.models import FileToTextRequest, FileToTextResponse
import aiohttp
import uuid
import os

# FILE_TO_TEXT_URL = "http://file-to-text.default.svc:8000/file_to_text"
FILE_TO_TEXT_URL = "http://file-to-text:8000/file_to_text"

def file_to_text_node(
    document_id_key: str = "document_id",
    output_key: str = "text",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        try:
            document_id = uuid.UUID((state.get(document_id_key)))
        except ValueError as e:
            raise ValueError(
                f"Invalid UUID format for {document_id_key}: {state.get(document_id_key)}"
            ) from e
        # if not isinstance(document_id, uuid.UUID):
        #     raise ValueError(
        #         f"Expected a UUID for {document_id_key}, but got {type(document_id)}"
        #     )

        request = FileToTextRequest(document_id=document_id)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                FILE_TO_TEXT_URL, json=request.model_dump(mode="json")
            ) as response:
                if response.status != 200:
                    error_detail = await response.text()
                    raise ValueError(
                        f"File-to-text service returned status {response.status}: {error_detail}"
                    )

                response_data = await response.json()
                data = FileToTextResponse.model_validate(response_data)

        yield {output_key: data.text}

    return node
