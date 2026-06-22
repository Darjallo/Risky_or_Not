import logging

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, cast, text, select, Float, func

from ethelflow.data.models import (
    Chunk,
    ChunkSet,
    EthelDocument,
    TextEmbedding3LargeEmbedding,
)

logger = logging.getLogger("uvicorn.error")


# Retrieve the most relevant chunks based on a query vector
async def get_relevant_chunks(
    session: AsyncSession,
    query_vector: list[float],
    top_k: int = 10,
    distance_threshold: float = 0.4,
    embedding_model: SQLModel = TextEmbedding3LargeEmbedding,
    ef_search: int = 100,
    retrieve_entire_docs: bool = False,
    retrieve_entire_docs_hits: int = 2,
    method=None,
):
    # FIXME: vector length is hardcoded here, should be derived from the model
    # we use the literal op "<=>" for cosine distance, otherwise Postgres doeesn't know that the index can be used
    distance = (
        cast(embedding_model.vector, HALFVEC(3072))
        .op("<=>")(cast(query_vector, HALFVEC(3072)))
        .cast(Float)
        .label("distance")
    )

    logger.info(
        f"Querying for relevant chunks, top_k={top_k}, distance_threshold={distance_threshold}, method={method}"
    )

    stmt = (
        select(Chunk.text, EthelDocument.id, distance)
        .select_from(embedding_model)
        .join(Chunk, Chunk.id == embedding_model.chunk_id)
        .join(ChunkSet, ChunkSet.id == Chunk.chunk_set_id)
        .join(EthelDocument, EthelDocument.id == ChunkSet.document_id)
        .where(distance <= distance_threshold)
        .order_by(distance)
        .limit(top_k)
    )

    if method:
        stmt = stmt.where(ChunkSet.method == method)

    async with session.begin():
        await session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
        rows = (await session.execute(stmt)).all()

    documents_count = {}
    for row in rows:
        documents_count[row.id] = documents_count.get(row.id, 0) + 1

    logger.info(
        f"Found {len(rows)} relevant chunks from {len(documents_count)} docs, average distance: {sum(r.distance for r in rows) / len(rows) if rows else 0}"
    )
    for doc_id, count in documents_count.items():
        logger.info(f"Document {doc_id} contributed {count} chunks")

    # Second pass: get all chunks for documents that have enough relevant chunks, if enabled
    if retrieve_entire_docs and any(
        count >= retrieve_entire_docs_hits for count in documents_count.values()
    ):
        doc_ids = [
            doc_id
            for doc_id, count in documents_count.items()
            if count >= retrieve_entire_docs_hits
        ]
        stmt_all_chunks = (
            select(
                EthelDocument.id,
                EthelDocument.title,
                func.string_agg(Chunk.text, " ").label("full_text"),
            )
            .join(ChunkSet, ChunkSet.document_id == EthelDocument.id)
            .join(Chunk, Chunk.chunk_set_id == ChunkSet.id)
            .where(EthelDocument.id.in_(doc_ids))
            .group_by(EthelDocument.id, EthelDocument.title)
        )
        if method:
            stmt_all_chunks = stmt_all_chunks.where(ChunkSet.method == method)

        rows_all_chunks = (await session.execute(stmt_all_chunks)).all()

        # Remove previous chunks from these documents
        rows = [r for r in rows if r.id not in doc_ids]

        logger.info(f"After second pass, total chunks retrieved: {len(rows)}")

        results = [{"text": r.full_text} for r in rows_all_chunks] + [
            {"text": r.text} for r in rows
        ]
    else:
        results = [{"text": r.text} for r in rows]

    return results
