import logging

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ethelflow.agents.store_text.models import StoreTextRequest, StoreTextResponse
from ethelflow.data.db_utils import get_session
from ethelflow.data.models import DocumentText

logger = logging.getLogger("uvicorn.error")
app = FastAPI()


@app.post("/store_text", response_model=StoreTextResponse)
async def store_text(req: StoreTextRequest, session: AsyncSession = Depends(get_session)):
    if not req.extractor or not isinstance(req.extractor, str):
        return StoreTextResponse(success=False, message="extractor must be a non-empty string")

    try:
        stmt = (
            select(DocumentText)
            .where(DocumentText.document_id == req.document_id)
            .where(DocumentText.extractor == req.extractor)
        )
        res = await session.execute(stmt)
        row = res.scalar_one_or_none()

        if row:
            # update existing
            row.text = req.text
            await session.commit()
            return StoreTextResponse(success=True, text_id=row.id, created=False)

        # create new
        row = DocumentText(document_id=req.document_id, extractor=req.extractor, text=req.text)
        session.add(row)
        await session.commit()
        await session.refresh(row)

        return StoreTextResponse(success=True, text_id=row.id, created=True)

    except Exception as e:
        await session.rollback()
        logger.exception("store_text failed")
        return StoreTextResponse(success=False, message=str(e))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

