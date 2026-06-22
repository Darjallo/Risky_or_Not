from __future__ import annotations

import logging
import math
import re
from typing import List

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text as sa_text

from ethelflow.agents.search_vectors.models import SearchVectorsRequest, SearchVectorsResponse
from ethelflow.data.db_utils import get_session
from ethelflow.model_catalog import ModelCatalog

logger = logging.getLogger("uvicorn.error")

app = FastAPI()

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _vector_literal(vec: List[float]) -> str:
    """
    Convert Python floats to a pgvector literal string: [0.1,0.2,...]
    We'll pass this as a bind param and CAST it in SQL.
    """
    if not vec:
        raise ValueError("query_vector is empty")
    for x in vec:
        if not isinstance(x, (int, float)) or not math.isfinite(float(x)):
            raise ValueError("query_vector contains non-finite value")
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def _table_exists(session: AsyncSession, table_name: str) -> bool:
    res = await session.execute(sa_text("SELECT to_regclass(:t)"), {"t": f"public.{table_name}"})
    return res.scalar_one_or_none() is not None


@app.post("/search_vectors", response_model=SearchVectorsResponse)
async def search_vectors(req: SearchVectorsRequest, session: AsyncSession = Depends(get_session)):
    """
    Given document_ids + extractor + chunking method + embedding space and a query vector,
    return top_k chunk_ids (best matches).
    """
    try:
        catalog = ModelCatalog.load()
        route = catalog.tenant_embedding_route(tenant=req.tenant, space=req.space)

        # Sanity checks
        if len(req.query_vector) != route.dimension:
            return SearchVectorsResponse(
                success=False,
                message=(
                    f"Vector dimension mismatch: got {len(req.query_vector)} "
                    f"expected {route.dimension} for space={route.space}"
                ),
                tenant=req.tenant,
                space=route.space,
                store_table=route.store_table,
            )

        if not req.document_ids:
            return SearchVectorsResponse(
                success=True,
                message="No document_ids provided; returning empty result.",
                tenant=req.tenant,
                space=route.space,
                store_table=route.store_table,
                chunk_ids=[],
                distances=[],
            )

        table = route.store_table
        if not _SAFE_IDENT_RE.match(table):
            return SearchVectorsResponse(
                success=False,
                message=f"Unsafe embedding table name from catalog: {table!r}",
                tenant=req.tenant,
                space=route.space,
                store_table=table,
            )

        if not await _table_exists(session, table):
            return SearchVectorsResponse(
                success=False,
                message=(
                    f"Embedding table {table!r} does not exist in DB "
                    f"(did you run alembic upgrade head?)"
                ),
                tenant=req.tenant,
                space=route.space,
                store_table=table,
            )

        qvec_str = _vector_literal(req.query_vector)
        dim = route.dimension

        # Avoid PostgreSQL :: casts in SQLAlchemy text() (bind parsing issues).
        # Still matches your expression index:
        #   hnsw ((vector::halfvec(dim)) halfvec_cosine_ops)
        if dim > 2000:
            order_expr = (
                f"(CAST(e.vector AS halfvec({dim})) <=> CAST(:qvec AS halfvec({dim})))"
            )
        else:
            order_expr = "(e.vector <=> CAST(:qvec AS vector))"

        sql = sa_text(
            f"""
            SELECT
              e.chunk_id AS chunk_id,
              {order_expr} AS distance
            FROM {table} e
            JOIN chunks c          ON c.id = e.chunk_id
            JOIN chunksets cs      ON cs.id = c.chunk_set_id
            JOIN document_texts dt ON dt.id = cs.text_id
            WHERE dt.document_id = ANY(:doc_ids)
              AND dt.extractor = :extractor
              AND cs.method = :method
            ORDER BY distance ASC
            LIMIT :k
            """
        )

        rows = (
            await session.execute(
                sql,
                {
                    "qvec": qvec_str,
                    "doc_ids": req.document_ids,
                    "extractor": req.extractor,
                    "method": req.method,
                    "k": req.top_k,
                },
            )
        ).all()

        chunk_ids = [r[0] for r in rows]
        distances = [float(r[1]) for r in rows]

        return SearchVectorsResponse(
            success=True,
            tenant=req.tenant,
            space=route.space,
            store_table=table,
            chunk_ids=chunk_ids,
            distances=distances,
        )

    except Exception as e:
        logger.exception("search_vectors failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

