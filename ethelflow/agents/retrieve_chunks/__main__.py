from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from ethelflow.agents.retrieve_chunks.models import RetrieveChunksRequest, RetrieveChunksResponse
from ethelflow.data.db_utils import get_session
from ethelflow.data.models import Chunk, ChunkSet, DocumentText, EthelDocument

logger = logging.getLogger("uvicorn.error")
app = FastAPI()


def _dedup_preserve_order(ids: List[uuid.UUID]) -> List[uuid.UUID]:
    seen = set()
    out: List[uuid.UUID] = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


@app.post("/retrieve_chunks", response_model=RetrieveChunksResponse)
async def retrieve_chunks(req: RetrieveChunksRequest, session: AsyncSession = Depends(get_session)):
    try:
        if not isinstance(req.tenant, str) or not req.tenant.strip():
            return RetrieveChunksResponse(success=False, message="tenant must be a non-empty string")

        if not req.chunk_ids:
            return RetrieveChunksResponse(
                success=True, 
                message="No chunk_ids provided", 
                chunk_ids=[], 
                chunk_texts=[],
                chunk_metadata=[],)

        chunk_ids = _dedup_preserve_order(req.chunk_ids)

        # stmt = select(Chunk.id, Chunk.text, Chunk.metadata).where(Chunk.id.in_(chunk_ids))
        stmt = (
            select(
                Chunk.id,
                Chunk.text,
                Chunk.position,
                Chunk.page_start,
                Chunk.page_end,
                ChunkSet.method,
                DocumentText.document_id,
                EthelDocument.title,
            )
            .join(ChunkSet, ChunkSet.id == Chunk.chunk_set_id)
            .join(DocumentText, DocumentText.id == ChunkSet.text_id)
            .join(EthelDocument, EthelDocument.id == DocumentText.document_id)
            .where(Chunk.id.in_(chunk_ids))
        )
        
        res = await session.execute(stmt)
        rows = res.all()

        # found: Dict[uuid.UUID, str] = {row[0]: row[1] for row in rows}
        found: Dict[uuid.UUID, Dict[str, Any]] = {
            row[0]: {
                "text": row[1],
                "metadata": {
                    "chunk_position": row[2],
                    "page_start": row[3],
                    "page_end": row[4],
                    "method": row[5],
                    "document_id": str(row[6]),
                    "document_name": row[7],
                },
            }
            for row in rows
        }

        # preserve order of first appearance in input; drop missing IDs silently
        out_ids = [cid for cid in chunk_ids if cid in found]
        #out_texts = [found[cid] for cid in out_ids]
        out_texts = [found[cid]["text"] for cid in out_ids]
        out_metadata = [found[cid]["metadata"] for cid in out_ids]

        return RetrieveChunksResponse(
            success=True,
            chunk_ids=out_ids,
            chunk_texts=out_texts,
            chunk_metadata=out_metadata,
            message=f"Returned {len(out_ids)} chunk(s)",
        )

    except Exception as e:
        logger.exception("retrieve_chunks failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

