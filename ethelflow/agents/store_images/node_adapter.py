from typing import Any, AsyncGenerator, Callable, Dict, List
import aiohttp
import uuid
import os

from ethelflow.agents.store_images.models import StoreImagesRequest, StoreImagesResponse, StoreImageItem

# STORE_IMAGES_URL: str = "http://store-images.default.svc:8000/store_images"
STORE_IMAGES_URL: str = "http://store-images:8000/store_images"

def _as_uuid(val: Any, field_name: str) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    if val is None:
        raise ValueError(f"{field_name} is required")
    try:
        return uuid.UUID(str(val))
    except Exception as e:
        raise ValueError(f"Invalid UUID format for {field_name}: {val!r}") from e


def store_images_node(
    document_id_key: str = "document_id",
    manifest_key: str = "image_manifest",          # FileToImagesResponse JSON
    override_key: str = "override_images",
    cleanup_temp_key: str = "cleanup_temp",
    output_image_set_id_key: str = "image_set_id",
    output_key: str = "store_images_response",
    timeout_s: int = 300,
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        document_id = _as_uuid(state.get(document_id_key), document_id_key)

        manifest = state.get(manifest_key)
        if not isinstance(manifest, dict):
            raise ValueError(f"Expected dict for {manifest_key}, got {type(manifest)}")

        # Pull parameters + images list from the manifest returned by file_to_images
        renderer = manifest.get("renderer", "pymupdf")
        dpi = manifest.get("dpi", 150)
        image_format = manifest.get("image_format", "png")
        layout = manifest.get("layout", "vertical")
        groups = manifest.get("groups")
        images = manifest.get("images")

        if not isinstance(groups, list) or not groups:
            raise ValueError("manifest.groups missing or invalid")
        if not isinstance(images, list) or not images:
            raise ValueError("manifest.images missing or invalid")

        items: List[StoreImageItem] = []
        for im in images:
            # im should be dict from FileToImagesResponse
            items.append(StoreImageItem.model_validate(im))

        override = bool(state.get(override_key, False))
        cleanup_temp = bool(state.get(cleanup_temp_key, False))

        req = StoreImagesRequest(
            document_id=document_id,
            renderer=str(renderer),
            dpi=int(dpi),
            image_format=str(image_format),
            layout=str(layout),
            groups=groups,
            manifest=manifest,
            images=items,
            override=override,
            cleanup_temp=cleanup_temp,
        )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                STORE_IMAGES_URL,
                json=req.model_dump(mode="json"),
                timeout=timeout_s,
            ) as resp:
                if resp.status != 200:
                    detail = await resp.text()
                    raise ValueError(f"store_images returned {resp.status}: {detail}")
                payload = await resp.json()

        data = StoreImagesResponse.model_validate(payload)
        if not data.success or not data.image_set_id:
            raise ValueError(f"store_images failed: {data.message}")

        yield {
            output_key: data.model_dump(mode="json"),
            output_image_set_id_key: str(data.image_set_id),
        }

    return node

