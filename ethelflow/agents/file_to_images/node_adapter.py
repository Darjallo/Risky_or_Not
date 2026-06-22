from typing import Any, AsyncGenerator, Callable, Dict, List
import aiohttp
import uuid
import os

from ethelflow.agents.file_to_images.models import (
    CleanupTempRequest,
    CleanupTempResponse,
    FileToImagesRequest,
    FileToImagesResponse,
)

# FILE_TO_IMAGES_URL: str = "http://file-to-images.default.svc:8000/file_to_images"
# CLEANUP_TEMP_URL: str = "http://file-to-images.default.svc:8000/cleanup_temp"
FILE_TO_IMAGES_URL: str = "http://file-to-images:8000/file_to_images"
CLEANUP_TEMP_URL: str = "http://file-to-images:8000/cleanup_temp"

def _as_uuid(val: Any, field_name: str) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    if val is None:
        raise ValueError(f"{field_name} is required")
    try:
        return uuid.UUID(str(val))
    except Exception as e:
        raise ValueError(f"Invalid UUID format for {field_name}: {val!r}") from e


def file_to_images_node(
    document_id_key: str = "document_id",
    groups_key: str = "groups",
    dpi_key: str = "dpi",
    image_format_key: str = "image_format",
    layout_key: str = "layout",
    renderer_key: str = "renderer",
    temp_prefix_key: str = "temp_prefix",
    output_manifest_key: str = "image_manifest",
    timeout_s: int = 300,
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        document_id = _as_uuid(state.get(document_id_key), document_id_key)

        groups = state.get(groups_key)
        if not isinstance(groups, list) or not groups:
            raise ValueError(f"Expected non-empty list for {groups_key}, got {groups!r}")

        dpi = state.get(dpi_key, 150)
        image_format = state.get(image_format_key, "png")
        layout = state.get(layout_key, "vertical")
        renderer = state.get(renderer_key, "pymupdf")
        temp_prefix = state.get(temp_prefix_key)

        req = FileToImagesRequest(
            document_id=document_id,
            groups=groups,
            dpi=int(dpi),
            image_format=str(image_format),
            layout=str(layout),
            renderer=str(renderer),
            temp_prefix=temp_prefix if isinstance(temp_prefix, str) else None,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                FILE_TO_IMAGES_URL,
                json=req.model_dump(mode="json"),
                timeout=timeout_s,
            ) as resp:
                if resp.status != 200:
                    detail = await resp.text()
                    raise ValueError(f"file_to_images returned {resp.status}: {detail}")
                payload = await resp.json()

        data = FileToImagesResponse.model_validate(payload)

        # Provide both full manifest and convenient keys for downstream nodes
        yield {
            output_manifest_key: data.model_dump(mode="json"),
            temp_prefix_key: data.temp_prefix,
        }

    return node


def cleanup_temp_node(
    temp_prefix_key: str = "temp_prefix",
    output_key: str = "cleanup_temp_response",
    timeout_s: int = 120,
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        temp_prefix = state.get(temp_prefix_key)
        if not isinstance(temp_prefix, str) or not temp_prefix.strip():
            # nothing to do
            yield {output_key: {"success": True, "deleted": 0, "message": ""}}
            return

        req = CleanupTempRequest(temp_prefix=temp_prefix)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                CLEANUP_TEMP_URL,
                json=req.model_dump(mode="json"),
                timeout=timeout_s,
            ) as resp:
                payload = await resp.json()

        data = CleanupTempResponse.model_validate(payload)
        yield {output_key: data.model_dump(mode="json")}

    return node

