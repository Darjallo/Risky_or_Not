from datetime import UTC, datetime

from fastapi import FastAPI
from langchain.text_splitter import RecursiveCharacterTextSplitter

from ethelflow.agents.chunk_text.models import ChunkingRequest, ChunkingResponse

app = FastAPI()


@app.post("/chunk_text")
async def chunk_text(req: ChunkingRequest):
    # XXX: consider using .from_tiktok_encoder() if we want to chunk by tokens
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=req.chunk_size,
        chunk_overlap=req.chunk_overlap,
        add_start_index=False,  # we only need raw strings
    )

    chunks = splitter.split_text(req.text)

    return ChunkingResponse(
        created=datetime.now(UTC),
        chunks=chunks,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
