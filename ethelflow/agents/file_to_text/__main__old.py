import asyncio
import io
from contextlib import asynccontextmanager

from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, HTTPException
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ethelflow.agents.file_to_text.models import FileToTextRequest, FileToTextResponse
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

        text = ""
        if document.content_type == "application/pdf":
            reader = PdfReader(file_object)
            for page in reader.pages:
                text += page.extract_text() or ""
        elif document.content_type == "text/plain":
            text = file_object.read().decode("utf-8")
        elif document.content_type == "text/html":
            html = file_object.read().decode("utf-8")
            text = await html_to_text(html)
        return FileToTextResponse(text=text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
