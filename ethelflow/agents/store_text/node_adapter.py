from typing import Any, AsyncGenerator, Callable, Dict
import aiohttp
import uuid
import os

from ethelflow.agents.store_text.models import StoreTextRequest, StoreTextResponse

# STORE_TEXT_URL: str = "http://store-text.default.svc:8000/store_text"
STORE_TEXT_URL: str = "http://store-text:8000/store_text"

def _as_uuid(val: Any, field_name: str) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    if val is None:
        raise ValueError(f"{field_name} is required")
    try:
        return uuid.UUID(str(val))
    except Exception as e:
        raise ValueError(f"Invalid UUID format for {field_name}: {val!r}") from e


def store_text_node(
    document_id_key: str = "document_id",
    extractor_key: str = "extractor",
    text_key: str = "text",
    output_text_id_key: str = "text_id",
    output_key: str = "store_text_response",
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        document_id = _as_uuid(state.get(document_id_key), document_id_key)

        extractor = state.get(extractor_key)
        if not isinstance(extractor, str) or not extractor.strip():
            raise ValueError(f"Expected non-empty str for {extractor_key}, got {extractor!r}")

        text = state.get(text_key)
        if text is not None and not isinstance(text, str):
            raise ValueError(f"Expected str|None for {text_key}, got {type(text)}")

        req = StoreTextRequest(document_id=document_id, extractor=extractor, text=text)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                STORE_TEXT_URL,
                json=req.model_dump(mode="json"),
                timeout=60,
            ) as resp:
                if resp.status != 200:
                    raise ValueError(f"Store text service returned status {resp.status}")
                payload = await resp.json()

        data = StoreTextResponse.model_validate(payload)
        if not data.success or not data.text_id:
            raise ValueError(f"store_text failed: {data.message}")

        yield {
            output_key: data.model_dump(mode="json"),
            output_text_id_key: str(data.text_id),  # convenient for next node
        }

    return node

