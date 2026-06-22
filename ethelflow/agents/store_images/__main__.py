import hashlib
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ethelflow.agents.store_images.models import StoreImagesRequest, StoreImagesResponse
from ethelflow.assets.s3 import s3_manager
from ethelflow.data.db_utils import get_session
from ethelflow.data.models import DocumentImage, DocumentImageSet, EthelDocument

logger = logging.getLogger("uvicorn.error")
app = FastAPI()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await s3_manager.init()
    yield
    await s3_manager.close()


app = FastAPI(lifespan=lifespan)


def _params_hash(renderer: str, dpi: int, image_format: str, layout: str, groups) -> str:
    payload = {
        "renderer": renderer,
        "dpi": int(dpi),
        "image_format": image_format,
        "layout": layout,
        "groups": groups,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _ext_for_format(image_format: str) -> str:
    f = image_format.lower().strip()
    if f == "jpeg":
        return "jpg"
    return f


async def _copy_then_delete(src_key: str, dst_key: str) -> None:
    client = s3_manager.s3_client
    if client is None:
        raise RuntimeError("S3 client not initialized")
    bucket = s3_manager.bucket_name

    await client.copy_object(
        Bucket=bucket,
        Key=dst_key,
        CopySource={"Bucket": bucket, "Key": src_key},
    )
    await client.delete_object(Bucket=bucket, Key=src_key)


async def _delete_keys(keys: List[str]) -> None:
    client = s3_manager.s3_client
    if client is None:
        raise RuntimeError("S3 client not initialized")
    bucket = s3_manager.bucket_name
    # batch delete up to 1000
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        await client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )


@app.post("/store_images", response_model=StoreImagesResponse)
async def store_images(req: StoreImagesRequest, session: AsyncSession = Depends(get_session)):
    try:
        # Validate doc exists
        stmt_doc = select(EthelDocument).where(EthelDocument.id == req.document_id)
        document = (await session.execute(stmt_doc)).scalars().one_or_none()
        if not document:
            return StoreImagesResponse(success=False, message="Document not found")

        if req.renderer != "pymupdf":
            return StoreImagesResponse(success=False, message="Unsupported renderer (only 'pymupdf')")

        if req.layout != "vertical":
            return StoreImagesResponse(success=False, message="Unsupported layout (only 'vertical')")

        if req.dpi <= 0:
            return StoreImagesResponse(success=False, message="dpi must be > 0")

        phash = _params_hash(req.renderer, req.dpi, req.image_format, req.layout, req.groups)

        # Find existing image set for same params
        stmt = (
            select(DocumentImageSet)
            .where(DocumentImageSet.document_id == req.document_id)
            .where(DocumentImageSet.params_hash == phash)
        )
        res = await session.execute(stmt)
        image_set = res.scalar_one_or_none()

        created = False
        updated = False

        if image_set and not req.override:
            # No-op: keep existing
            # (Optionally still cleanup temp if requested)
            if req.cleanup_temp:
                temp_keys = [it.temp_s3_key for it in req.images]
                await _delete_keys(temp_keys)
            # return existing image IDs if you want (optional)
            ids_stmt = select(DocumentImage.id).where(DocumentImage.image_set_id == image_set.id)
            ids = [row[0] for row in (await session.execute(ids_stmt)).all()]
            return StoreImagesResponse(
                success=True,
                image_set_id=image_set.id,
                image_ids=ids,
                created=False,
                updated=False,
            )

        if image_set and req.override:
            # Delete existing image rows + best-effort delete old S3 objects
            old_stmt = select(DocumentImage).where(DocumentImage.image_set_id == image_set.id)
            old_images = (await session.execute(old_stmt)).scalars().all()
            old_keys = [im.s3_key for im in old_images if im.s3_key]
            for im in old_images:
                await session.delete(im)
            # Update metadata/manifest on set
            image_set.renderer = req.renderer
            image_set.dpi = req.dpi
            image_set.image_format = req.image_format
            image_set.layout = req.layout
            image_set.groups = req.groups  # JSONB
            image_set.manifest = req.manifest  # JSONB
            updated = True
            await session.commit()

            # best-effort blob cleanup (do not fail the whole request)
            if old_keys:
                try:
                    await _delete_keys(old_keys)
                except Exception:
                    logger.exception("best-effort delete of old image blobs failed")

        if not image_set:
            image_set = DocumentImageSet(
                document_id=req.document_id,
                renderer=req.renderer,
                dpi=req.dpi,
                image_format=req.image_format,
                layout=req.layout,
                groups=req.groups,        # JSONB
                params_hash=phash,
                manifest=req.manifest,    # JSONB
            )
            session.add(image_set)
            await session.commit()
            await session.refresh(image_set)
            created = True

        # Move temp objects to permanent location and write rows
        ext = _ext_for_format(req.image_format)
        image_ids: List = []

        permanent_prefix = f"documents/{req.document_id}/images/{image_set.id}/"

        for item in req.images:
            dst_key = f"{permanent_prefix}{item.position}_{req.dpi}.{ext}"
            await _copy_then_delete(item.temp_s3_key, dst_key)

            row = DocumentImage(
                image_set_id=image_set.id,
                position=item.position,
                pages=item.pages,  # JSONB list is fine
                s3_key=dst_key,
                mime_type=item.mime_type,
                byte_size=item.byte_size,
                width=item.width,
                height=item.height,
            )
            session.add(row)

        await session.commit()

        # collect IDs
        ids_stmt = select(DocumentImage.id).where(DocumentImage.image_set_id == image_set.id)
        ids = [row[0] for row in (await session.execute(ids_stmt)).all()]

        # optional cleanup (mostly redundant because we "move" via copy+delete)
        if req.cleanup_temp:
            temp_keys = [it.temp_s3_key for it in req.images]
            try:
                await _delete_keys(temp_keys)
            except Exception:
                logger.exception("best-effort cleanup_temp failed")

        return StoreImagesResponse(
            success=True,
            image_set_id=image_set.id,
            image_ids=ids,
            created=created,
            updated=updated,
        )

    except Exception as e:
        await session.rollback()
        logger.exception("store_images failed")
        return StoreImagesResponse(success=False, message=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

