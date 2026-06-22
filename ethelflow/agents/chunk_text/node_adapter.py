from __future__ import annotations

from typing import Any, AsyncGenerator, Callable, Dict, List

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception:  # fallback for older LangChain installations
    from langchain.text_splitter import RecursiveCharacterTextSplitter


def chunk_pages_node(
    pages_key: str = "pages",
    document_id_key: str = "document_id",
    document_name_key: str = "document_name",
    content_type_key: str = "content_type",
    output_chunks_key: str = "chunks",
    output_page_start_key: str = "chunk_page_start",
    output_page_end_key: str = "chunk_page_end",
    output_metadata_key: str = "chunk_metadata",
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
) -> Callable[[Dict[str, Any]], AsyncGenerator[Dict[str, Any], None]]:
    """
    Version A: chunk each extracted page separately.

    Input state['pages'] format:
      [ {'page': 1, 'text': '...'}, {'page': 2, 'text': '...'} ]

    Output:
      state['chunks'] = list[str]
      state['chunk_metadata'] = list[dict], aligned by index with chunks.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    async def node(state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        pages = state.get(pages_key)
        if not isinstance(pages, list):
            raise ValueError(f"Expected list for {pages_key}, got {type(pages)}")

        document_id = state.get(document_id_key)
        document_name = state.get(document_name_key) or str(document_id or "unknown_document")
        content_type = state.get(content_type_key)

        chunks: List[str] = []
        page_start: List[dict] = []
        page_end: List[dict] = []
        metadata: List[dict] = []

        for page_obj in pages:
            if not isinstance(page_obj, dict):
                continue

            page_no = page_obj.get("page")
            page_text = page_obj.get("text") or ""

            if not isinstance(page_no, int):
                # Skip malformed page records; they cannot be cited reliably.
                continue

            if not page_text.strip():
                continue

            page_chunks = splitter.split_text(page_text)

            for chunk_index_on_page, chunk_text in enumerate(page_chunks, start=1):
                if not chunk_text.strip():
                    continue

                chunks.append(chunk_text)
                page_start.append(page_no)
                page_end.append(page_no)
                metadata.append({
                    "document_id": str(document_id) if document_id is not None else None,
                    "document_name": document_name,
                    "content_type": content_type,
                    "page_start": page_no,
                    "page_end": page_no,
                    "chunk_index_on_page": chunk_index_on_page,
                    "chunking_method": f"recursive_char_{chunk_size}_{chunk_overlap}_pageaware",
                })

        yield {
            output_chunks_key: chunks,
            output_page_start_key: page_start,
            output_page_end_key: page_end,
            output_metadata_key: metadata,
        }

    return node
