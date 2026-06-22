import asyncio
import io
import uuid
from contextlib import asynccontextmanager
from typing import List, Tuple

import fitz  # PyMuPDF
from fastapi import Depends, FastAPI, HTTPException
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ethelflow.agents.file_to_images.models import (
    CleanupTempRequest,
    CleanupTempResponse,
    FileToImagesRequest,
    FileToImagesResponse,
    RenderedImageRef,
)
from ethelflow.assets.s3 import s3_manager
from ethelflow.data.db_utils import get_session
from ethelflow.data.models import EthelDocument


@asynccontextmanager
async def lifespan(app: FastAPI):
    await s3_manager.init()
    yield
    await s3_manager.close()


app = FastAPI(lifespan=lifespan)


def _expand_group(group: List[int]) -> List[int]:
    if not group:
        return []
    if len(group) == 2 and group[1] >= group[0]:
        start, end = group
        return list(range(start, end + 1))
    # otherwise treat as explicit list
    return list(group)


def _render_group_vertical(
    doc: fitz.Document,
    pages_1based: List[int],
    dpi: int,
    image_format: str,
) -> Tuple[bytes, int, int]:
    if not pages_1based:
        raise ValueError("empty page group")

    # Render each page to PIL image at requested DPI, respecting page rotation metadata.
    imgs: List[Image.Image] = []
    widths: List[int] = []
    heights: List[int] = []

    zoom = dpi / 72.0

    for p in pages_1based:
        if p <= 0 or p > doc.page_count:
            raise ValueError(f"page out of range: {p} (doc has {doc.page_count} pages)")
        page = doc.load_page(p - 1)  # 0-based

        # IMPORTANT:
        # Some PyMuPDF versions do not support rotate=... kwarg on get_pixmap().
        # Apply page rotation via the rendering Matrix instead.
        rot = int(page.rotation or 0)  # typically 0/90/180/270
        matrix = fitz.Matrix(zoom, zoom).prerotate(rot)

        pix = page.get_pixmap(matrix=matrix, alpha=True)

        mode = "RGBA" if pix.alpha else "RGB"
        img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        imgs.append(img)
        widths.append(img.width)
        heights.append(img.height)

    max_w = max(widths)
    total_h = sum(heights)

    # No width normalization: allocate canvas with max width and paste images left-aligned.
    canvas_mode = "RGBA" if any(im.mode == "RGBA" for im in imgs) else "RGB"
    bg = (255, 255, 255, 0) if canvas_mode == "RGBA" else (255, 255, 255)
    canvas = Image.new(canvas_mode, (max_w, total_h), bg)

    y = 0
    for im in imgs:
        canvas.paste(im, (0, y))
        y += im.height

    out = io.BytesIO()
    fmt = image_format.upper()
    if fmt == "JPG":
        fmt = "JPEG"
    canvas.save(out, format=fmt)
    data = out.getvalue()
    return data, canvas.width, canvas.height


def _ext_for_format(image_format: str) -> str:
    f = image_format.lower().strip()
    if f == "jpeg":
        return "jpg"
    return f


async def _delete_prefix(prefix: str) -> int:
    """
    Delete all S3 objects under prefix. Best-effort. Returns count of deleted objects.
    Uses the underlying aiobotocore client for list/delete.
    """
    if not prefix:
        return 0
    client = s3_manager.s3_client
    if client is None:
        raise RuntimeError("S3 client not initialized")
    bucket = s3_manager.bucket_name

    deleted = 0
    continuation = None

    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = await client.list_objects_v2(**kwargs)
        contents = resp.get("Contents", [])
        if not contents:
            break

        # delete in batches
        keys = [{"Key": obj["Key"]} for obj in contents]
        # S3 DeleteObjects max 1000
        await client.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
        deleted += len(keys)

        if resp.get("IsTruncated"):
            continuation = resp.get("NextContinuationToken")
        else:
            break

    return deleted


@app.post("/file_to_images", response_model=FileToImagesResponse)
async def file_to_images(
    req: FileToImagesRequest,
    session: AsyncSession = Depends(get_session),
):
    # DB lookup
    statement = select(EthelDocument).where(EthelDocument.id == req.document_id)
    document = (await session.execute(statement)).scalars().one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {document.content_type}")

    if req.layout != "vertical":
        raise HTTPException(status_code=400, detail=f"Unsupported layout: {req.layout} (only 'vertical' supported)")

    if req.renderer != "pymupdf":
        raise HTTPException(status_code=400, detail=f"Unsupported renderer: {req.renderer} (only 'pymupdf' supported)")

    if req.dpi <= 0:
        raise HTTPException(status_code=400, detail="dpi must be > 0")

    # Download PDF bytes from S3 (key = str(document.id))
    try:
        file_object = io.BytesIO()
        await s3_manager.download_file(str(document.id), file_object)
        pdf_bytes = file_object.getvalue()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download document from S3: {e}")

    # Render
    try:
        doc = await asyncio.to_thread(lambda: fitz.open(stream=pdf_bytes, filetype="pdf"))
        temp_prefix = req.temp_prefix
        if not temp_prefix:
            run_id = uuid.uuid4().hex
            temp_prefix = f"tmp/file_to_images/{req.document_id}/{run_id}/"

        images: List[RenderedImageRef] = []
        ext = _ext_for_format(req.image_format)
        mime = "image/png" if ext == "png" else ("image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}")

        for idx, group in enumerate(req.groups, start=1):
            pages = _expand_group(group)
            data, w, h = await asyncio.to_thread(_render_group_vertical, doc, pages, req.dpi, req.image_format)

            key = f"{temp_prefix}{idx}_{req.dpi}.{ext}"
            bio = io.BytesIO(data)
            await s3_manager.upload_file(bio, key)

            images.append(
                RenderedImageRef(
                    position=idx,
                    pages=pages,
                    temp_s3_key=key,
                    mime_type=mime,
                    byte_size=len(data),
                    width=w,
                    height=h,
                )
            )

        return FileToImagesResponse(
            document_id=req.document_id,
            renderer=req.renderer,
            dpi=req.dpi,
            image_format=req.image_format,
            layout=req.layout,
            groups=req.groups,
            temp_prefix=temp_prefix,
            images=images,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to render images: {e}")


@app.post("/cleanup_temp", response_model=CleanupTempResponse)
async def cleanup_temp(req: CleanupTempRequest):
    try:
        deleted = await _delete_prefix(req.temp_prefix)
        return CleanupTempResponse(success=True, deleted=deleted)
    except Exception as e:
        return CleanupTempResponse(success=False, deleted=0, message=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

