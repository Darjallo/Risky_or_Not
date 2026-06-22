# ethelflow/data

This directory is the **data access layer** for EthelFlow services: database session utilities, core SQLModel ORM models, **pod storage** (JSON state), and (legacy) vector helper code.

It is used by multiple agents (FastAPI microservices) via `Depends(get_session)` and by any code that needs to read/write the Postgres schema.

---

## Overview

EthelFlow’s storage pipeline, at a high level:

0. **Pods (JSON state)**
   - `Pod` rows store **small JSON blobs** that represent “state” owned by an API (e.g. ChatAPI conversation history) or shared configuration (e.g. a course environment).
   - Pods are addressed by either:
     - a random UUID (normal “create then pass back the handle”), or
     - a deterministic UUIDv5 (“compute the id from semantic keys”) for “find without listing” use-cases (e.g. environment pods).
   - Pod updates use a simple `rev` counter to support optimistic concurrency.

1. **Assets & documents**
   - `Asset` represents a *logical path* in the virtual filesystem (tenant/collection/subpath/filename).
   - `EthelDocument` represents a *concrete stored version* of an asset (one row per version).

2. **Text extraction**
   - `DocumentText` stores extracted text for a specific `(document_id, extractor)` pair.

3. **Chunking**
   - `ChunkSet` groups chunks created from a `DocumentText` using a chunking `method`.
   - `Chunk` stores each chunk’s text and its `position` within the chunk set.

4. **Embeddings**
   - Embeddings are stored **per embedding space** in dedicated pgvector tables.
   - Which table to use is determined by the **Model Catalog** (tenant + embedding space → dimension + store_table).

5. **Document page images**
   - `DocumentImageSet` stores a rendered page-image configuration for a specific `(document_id, params_hash)` pair (renderer/dpi/layout/groups).
   - `DocumentImage` stores each rendered image artifact, including its page list (`pages`) and storage reference (`s3_key`).

Agents implement these steps as independent microservices, but they all depend on the schema and session utilities here.

---

## Files

- `db_utils.py`
  - Creates the async SQLAlchemy engine and `AsyncSessionLocal`.
  - Exposes:
    - `get_session()` (FastAPI dependency generator)
    - `get_session_ctx()` (async context manager)

- `models.py`
  - The canonical SQLModel models for the EthelFlow Postgres schema:
    - `Asset`, `EthelDocument`, `DocumentText`, `ChunkSet`, `Chunk`
    - `DocumentImageSet`, `DocumentImage`
    - `Pod` (JSON state storage used by APIs)
  - Also includes **legacy** embedding model tables that remain in the DB/codebase.

- `pods.py`
  - A small, API-agnostic **pod store** abstraction over the `Pod` table.
  - Defines:
    - `PodStore` protocol + `PostgresPodStore` implementation
    - `deterministic_pod_id(...)` for UUIDv5 ids (see note below)
    - `PodNotFound`, `PodConflict`
  - Used by:
    - `ethelflow/apis/chatapi` to store conversation state (`pod_type="conversation_context"`) and to load “environment pods” each request (“follow latest”)
    - `ethelflow/apis/admin` to upsert/read environment pods (`pod_type="environment"`)

- `vectors.py` (**legacy / compatibility**)
  - An older helper for “relevant chunk retrieval” using hard-coded assumptions (e.g., fixed dimension = 3072).
  - New services should prefer the **catalog-driven** agents:
    - `search_vectors` + `retrieve_chunks`
  - If you touch `vectors.py`, treat it as legacy code and confirm it matches current schema.

---

## Session utilities (`db_utils.py`)

### Engine/session configuration

`AsyncSessionLocal` is constructed with:

- `postgres_settings.async_url`
- `pool_size=20`
- `max_overflow=20`
- `expire_on_commit=False` (important: objects keep attribute values after commit)
- `autoflush=False`

### Using sessions in services

Most agents use:

```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from ethelflow.data.db_utils import get_session

@app.post("/endpoint")
async def handler(..., session: AsyncSession = Depends(get_session)):
    ...
```

For scripts/tests you can use the context manager:

```python
from ethelflow.data.db_utils import get_session_ctx

async with get_session_ctx() as session:
    ...
```

---

## Pods (`pods.py`)

Pods are intentionally **schema-light**: a pod is a row with identifying fields (tenant, owner_api, pod_type, end_user_id) and a JSON `data` payload. This makes them suitable for:
- conversation/session-like state (e.g., message history)
- “environment” configuration blobs (course template, reference doc ids, future settings)
- any other small API-specific state that should live in Postgres and be accessed by multiple services

### Deterministic ids (UUIDv5)

`pods.py` defines:

- `POD_ID_NAMESPACE = uuid.UUID("2f35a5a4-9c2c-4a50-9b86-3f5f6a7c9a01")`
- `deterministic_pod_id(tenant, owner_api, pod_type, key) -> uuid.UUID`

This is **not a secret**. The constant is only a stable namespace for UUIDv5, so the same semantic keys always map to the same UUID.

**Important:** do not change `POD_ID_NAMESPACE` after it has been committed/deployed, or any deterministic lookups created with the old namespace will no longer work.

### Optimistic concurrency

`PostgresPodStore.update_pod(..., expected_rev=...)` increments `pod.rev` on each update.
If `expected_rev` is provided and does not match, `PodConflict` is raised. Callers that need “last write wins” can omit `expected_rev`.

---

## Core schema (`models.py`)

### `Asset` (logical filesystem entry)

Represents a logical path:

```
/{tenant}/{collection}/{subpath}/{filename}
```

Key fields:
- `tenant`, `collection`, `subpath`, `filename`
- `latest_document_id` → points to most recent `EthelDocument`
- Unique constraint on `(tenant, collection, subpath, filename)`

### `EthelDocument` (stored version of an asset)

One concrete “version” row for an asset:
- `asset_id` (FK → `assets.id`)
- `version` (int)
- `content_type` (e.g., `application/pdf`, `text/html`, …)

**Note:** The binary payload itself lives in object storage (S3). The DB stores metadata and relationships only.

### `DocumentText` (extracted text for a document)

Stores extraction output for:
- `document_id` + `extractor`

Important constraints:
- Unique constraint on `(document_id, extractor)` (one extraction output per extractor per document)

### `ChunkSet` (chunking run)

Groups chunks for a `DocumentText`:
- `text_id` (FK → `document_texts.id`)
- `method` (string label; e.g. `recursive_char_1000_100_htmlstrip`)

Important constraints:
- Unique constraint on `(text_id, method)`

### `Chunk` (chunk text)

- `chunk_set_id` (FK → `chunksets.id`)
- `text` (chunk content)
- `position` (order within the chunk set)

### `DocumentImageSet` (rendered page-image set)

Stores page-image configuration and provenance for a given document version:
- `document_id` (FK → `etheldocuments.id`)
- `renderer` (e.g. `pymupdf`)
- `dpi` (default 150; overrideable)
- `layout` (default `vertical`)
- `image_format` (default `png`)
- `groups` (JSONB grouping spec; explicit page lists/ranges)
- `params_hash` (stable hash of renderer/dpi/layout/format/groups; used for idempotency)
- `manifest` (optional JSONB; stores the render manifest/provenance)

Important constraints:
- Unique constraint on `(document_id, params_hash)` (one set per render configuration per document)

### `DocumentImage` (rendered image artifact)

Stores one image within a `DocumentImageSet`:
- `image_set_id` (FK → `document_image_sets.id`)
- `position` (order within the set)
- `pages` (JSONB explicit page list; e.g. `[2,3]` or `[5,7,8]`)
- `s3_key` (object storage reference for the image bytes)
- Optional metadata: `mime_type`, `byte_size`, `width`, `height`

Important constraints:
- Unique constraint on `(image_set_id, position)`

### Cascades and deletes

- `DocumentText` → `ChunkSet` → `Chunk` are configured with cascade deletes in ORM relationships.
- `DocumentImageSet` → `DocumentImage` is configured with cascade deletes in ORM relationships.
- Database-level `ON DELETE CASCADE` is used for:
  - `document_image_sets.document_id → etheldocuments.id`
  - `document_images.image_set_id → document_image_sets.id`
- Note: DB cascades remove rows, but do not delete S3 objects; services should handle blob cleanup.

---

## Embedding storage design

### Catalog-driven embedding tables (recommended)

New embedding storage is **driven by the model catalog**, not hard-coded in ORM models:

- `store_vectors` resolves:
  - tenant + embedding space → `{dimension, store_table}`
  - then performs an upsert into `store_table` keyed by `chunk_id`

- `search_vectors` resolves the same route and performs ANN search using the pgvector index appropriate for the dimension.

This allows multiple tenants/spaces without changing ORM code.

### Legacy embedding tables (still present)

`models.py` contains:
- `embedding_models` (legacy lookup table)
- `embeddings_text_embedding_3_large` (3072)
- `embeddings_text_embedding_3_small` (1536)

These exist for compatibility, but the newer services are moving toward catalog-defined tables.

### Indexing notes (pgvector)

For higher dimensions (e.g. 3072), the codebase commonly uses an index on:

- `vector::halfvec(dim)` with `halfvec_cosine_ops` and HNSW

Search services must use an `ORDER BY` expression compatible with the index to benefit from ANN.

---

## How agents tie into this package

Most flow-level pipelines follow this DB graph:

```
Asset (logical path)
  └── EthelDocument (version)
        ├── DocumentText (extractor)
        │     └── ChunkSet (method)
        │           └── Chunk (position, text)
        │                 └── Embedding table for space (chunk_id -> vector)
        └── DocumentImageSet (params_hash)
              └── DocumentImage (position, pages, s3_key)
```

Agent responsibilities:

- `file_to_text`:
  - reads `EthelDocument` metadata + downloads from S3
  - produces extracted plain text

- `store_text`:
  - upserts into `DocumentText` for `(document_id, extractor)`

- `chunk_text`:
  - splits `DocumentText.text` into chunk strings

- `store_chunks`:
  - creates/locks `ChunkSet(text_id, method)`
  - replaces or returns `Chunk` rows

- `embedding`:
  - calls the provider model to compute vectors for chunk strings

- `store_vectors`:
  - upserts vectors into catalog-selected embedding store table keyed by `chunk_id`

- `search_vectors`:
  - ANN search in the catalog-selected embedding table
  - joins back through `chunks → chunksets → document_texts` to filter by document/extractor/method

- `retrieve_chunks`:
  - reads `Chunk.text` for a set of chunk IDs

---

## Migration and environment requirements

- Postgres must have the UUID extension enabled if `uuid_generate_v4()` is used as a server default.
- pgvector must be installed for vector columns and HNSW indexes.
- The concrete schema is managed by Alembic migrations (outside this folder). If tables are missing, run:
  - `alembic upgrade head` (from the repo’s configured Alembic directory)

---

## Guidance for developers

### Do not hard-code embedding dimensions or table names

Use the **Model Catalog** to resolve:
- embedding space → `{dimension, store_table}`

If you need vectors inside a service, follow the pattern used in:
- `store_vectors`
- `search_vectors`

### Prefer services over `vectors.py`

`vectors.py` is legacy and may lag behind schema changes. For retrieval in flows:
- Use `search_vectors` + `retrieve_chunks`

### Keep state JSON-friendly across service boundaries

When sending IDs across agent boundaries:
- Prefer UUID strings (`str(uuid)`) in flow state.
- Node adapters often convert `str → uuid.UUID` internally.

### Concurrency and “replace” behavior

If a pipeline is re-run for the same `(document_id, extractor, method)`:
- `store_text` updates/reuses the same `DocumentText` row
- `store_chunks(replace=True)` deletes and recreates `Chunk` rows for the `(text_id, method)` chunkset
- `store_vectors` upserts by `chunk_id`

This makes the pipeline idempotent and safe to rerun.

---

## Quick reference: commonly-used labels

- `extractor`: `"file_to_text"` (or other extraction label)
- `method`: `"recursive_char_1000_100_htmlstrip"` (or other chunking label)
- `embedding_space`: `None` means “use tenant default from catalog"
