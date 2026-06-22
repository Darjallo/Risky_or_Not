from __future__ import annotations

import logging
import re
from typing import Dict, Tuple

from fastapi import Depends, FastAPI
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pgvector.sqlalchemy import Vector

from ethelflow.agents.store_vectors.models import StoreVectorsRequest, StoreVectorsResponse
from ethelflow.data.db_utils import get_session
from ethelflow.model_catalog import ModelCatalog

logger = logging.getLogger("uvicorn.error")
app = FastAPI()

_TABLE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Cache constructed SQLAlchemy Table objects per (table_name, dimension)
_TABLE_CACHE: Dict[Tuple[str, int], Table] = {}


def _embedding_table(table_name: str, dim: int) -> Table:
    """
    Create a lightweight SQLAlchemy Table definition (no reflection),
    just enough for inserts with correct pgvector binding.
    """
    key = (table_name, dim)
    if key in _TABLE_CACHE:
        return _TABLE_CACHE[key]

    md = MetaData()
    t = Table(
        table_name,
        md,
        Column("chunk_id", PG_UUID(as_uuid=True), primary_key=True, nullable=False),
        Column("vector", Vector(dim), nullable=False),
        schema="public",
    )
    _TABLE_CACHE[key] = t
    return t


@app.post("/store_vectors", response_model=StoreVectorsResponse)
async def store_vectors(req: StoreVectorsRequest, session: AsyncSession = Depends(get_session)):
    """
    Store vectors for a tenant's embedding space.

    - Catalog is the source of truth:
        tenant -> default_space
        space -> dimension + store.table
    - One vector per chunk per embedding space table:
        PRIMARY KEY (chunk_id)
    - Idempotent: ON CONFLICT(chunk_id) DO UPDATE
    """
    try:
        # Basic consistency checks
        if len(req.chunk_ids) != len(req.embeddings):
            return StoreVectorsResponse(
                success=False,
                message=f"chunk_ids length ({len(req.chunk_ids)}) != embeddings length ({len(req.embeddings)})",
                num_vectors_stored=0,
                tenant=req.tenant,
                space=req.space,
            )

        catalog = ModelCatalog.load()
        route = catalog.tenant_embedding_route(tenant=req.tenant, space=req.space)

        table_name = route.store_table
        if not isinstance(table_name, str) or not _TABLE_IDENT_RE.match(table_name):
            return StoreVectorsResponse(
                success=False,
                message=f"Unsafe/invalid store table name: {table_name!r}",
                num_vectors_stored=0,
                tenant=req.tenant,
                space=route.space,
                store_table=table_name,
            )

        # Dimension sanity check
        for i, v in enumerate(req.embeddings):
            if len(v) != route.dimension:
                return StoreVectorsResponse(
                    success=False,
                    message=f"Vector dimension mismatch at i={i}: got {len(v)} expected {route.dimension} for space={route.space}",
                    num_vectors_stored=0,
                    tenant=req.tenant,
                    space=route.space,
                    store_table=table_name,
                )

        t = _embedding_table(table_name, route.dimension)

        rows = [{"chunk_id": cid, "vector": vec} for cid, vec in zip(req.chunk_ids, req.embeddings)]

        stmt = pg_insert(t).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.c.chunk_id],
            set_={"vector": stmt.excluded.vector},
        )

        await session.execute(stmt)
        await session.commit()

        return StoreVectorsResponse(
            success=True,
            num_vectors_stored=len(rows),
            tenant=req.tenant,
            space=route.space,
            store_table=table_name,
        )

    except Exception as e:
        await session.rollback()
        logger.exception("store_vectors failed")
        return StoreVectorsResponse(
            success=False,
            message=str(e),
            num_vectors_stored=0,
            tenant=req.tenant,
            space=req.space,
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

