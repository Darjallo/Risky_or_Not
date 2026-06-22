import asyncio
import io
from contextlib import asynccontextmanager

from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, HTTPException
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ethelflow.agents.file_to_text.models import FileToTextRequest, FileToTextResponse, PageText
from ethelflow.assets.s3 import s3_manager
from ethelflow.data.db_utils import get_session
from ethelflow.data.models import EthelDocument


@asynccontextmanager
async def lifespan(app: FastAPI):
    await s3_manager.init()
    yield
    await s3_manager.close()


app = FastAPI(lifespan=lifespan)


def _html_to_text_sync(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


async def html_to_text(html: str) -> str:
    return await asyncio.to_thread(_html_to_text_sync, html)


def _document_display_name(document: EthelDocument) -> str:
    """Best-effort document name without assuming one exact DB field name."""
    for attr in ("filename", "file_name", "name", "title", "original_filename"):
        value = getattr(document, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return str(document.id)


@app.post("/file_to_text", response_model=FileToTextResponse)
async def file_to_text(
    req: FileToTextRequest,
    session: AsyncSession = Depends(get_session),
):
    statement = select(EthelDocument).where(EthelDocument.id == req.document_id)
    document = (await session.execute(statement)).scalars().one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if document.content_type not in ["application/pdf", "text/plain", "text/html"]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported content type: {document.content_type}",
        )

    try:
        file_object = io.BytesIO()
        await s3_manager.download_file(str(document.id), file_object)
        file_object.seek(0)

        pages: list[PageText] = []
        text = ""

        if document.content_type == "application/pdf":
            reader = PdfReader(file_object)
            text_parts: list[str] = []

            for page_index, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                pages.append(PageText(page=page_index, text=page_text))

                # Keep backward compatibility, but add page markers so even legacy chunking
                # can show rough provenance in the raw text.
                text_parts.append(f"\n\n--- PAGE {page_index} ---\n\n{page_text}")

            text = "".join(text_parts).strip()

        elif document.content_type == "text/plain":
            text = file_object.read().decode("utf-8")
            pages = [PageText(page=1, text=text)]

        elif document.content_type == "text/html":
            html = file_object.read().decode("utf-8")
            text = await html_to_text(html)
            pages = [PageText(page=1, text=text)]

        return FileToTextResponse(
            text=text,
            pages=pages,
            document_id=document.id,
            document_name=_document_display_name(document),
            content_type=document.content_type,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
