# store-chunks

## Overview
`store-chunks` persists text chunks for a **single extracted text version** (`document_texts.id`) and a **chunking method label**.

Conceptually:

- A `ChunkSet` groups chunks for `(text_id, method)`
- Individual `Chunk` rows store the actual chunk text and an integer `position` for stable ordering

The service supports **idempotent replacement** (default) and a **read-only mode** (`replace=false`) that returns the existing chunk IDs without modifying the database.

## Endpoint(s)
- `POST /store_chunks`

Service listens on port **8000**.

## Request/Response

### Request: `StoreChunksRequest`
Fields:
- `text_id` (uuid, required): The `document_texts.id` this chunk set belongs to
- `chunks` (list[str], required): The chunk texts to store
- `method` (str, required): Chunking method label (e.g. `recursive_char_1000_100_htmlstrip`)
- `replace` (bool, optional, default `true`):  
  - `true`: delete and recreate all chunks for `(text_id, method)`  
  - `false`: return existing chunks for `(text_id, method)` (ordered by `position`) without changes

Example:
```json
{
  "text_id": "fa63335e-2613-43c4-9994-1ef1e350420a",
  "chunks": ["First chunk...", "Second chunk..."],
  "method": "recursive_char_1000_100_htmlstrip",
  "replace": true
}
```

### Response: `StoreChunksResponse`
Fields:
- `success` (bool)
- `message` (str, optional)
- `chunk_set_id` (uuid, optional): The `chunksets.id` for `(text_id, method)`
- `chunk_ids` (list[uuid]): The chunk IDs, ordered by `position`

Example:
```json
{
  "success": true,
  "message": "",
  "chunk_set_id": "e562ebee-0502-4460-bb5d-28d42fd9d541",
  "chunk_ids": [
    "c4c6c0e0-2ce0-4e2d-9a65-1b1a3d8302a1",
    "31c3520d-2a9e-4b30-9dfb-5b38e7d8c2a0"
  ]
}
```

## Node adapter contract (LangGraph)
Node: `ethelflow.agents.store_chunks.node_adapter.store_chunks_node(...)`

### Expected state keys (inputs)
Configurable via parameters; defaults shown:
- `text_id` (default key: `text_id`): UUID (or UUID-like string) of `document_texts.id`
- `chunks` (default key: `chunks`): `list[str]`
- `method` (default key: `method`): `str`
- `replace` (default key: `replace`): optional truthy value (`true/false/1/0/...`)

### Produced state keys (outputs)
- `store_chunks_response` (default key: `output_key`): JSON-serializable dict matching `StoreChunksResponse`

Notes:
- The adapter keeps the response JSON-friendly (`uuid` serialized as strings).

## Routing / model catalog expectations (tenant/space)
None.

`store-chunks` is a pure persistence service and does not require `tenant`, `space`, or catalog routing. It stores chunks only under the provided `text_id` and `method`.

## Storage / DB interaction
Tables (from `ethelflow.data.models`):
- `chunksets`
  - `id` (uuid, PK)
  - `text_id` (uuid, FK → `document_texts.id`, `ON DELETE CASCADE`)
  - `method` (str)
- `chunks`
  - `id` (uuid, PK)
  - `chunk_set_id` (uuid, FK → `chunksets.id`, `ON DELETE CASCADE`)
  - `text` (str)
  - `position` (int)

Behavior:
- **Replace mode (`replace=true`)**:
  - Locks the existing `chunksets` row for `(text_id, method)` using `SELECT ... FOR UPDATE` (serializes concurrent replacements).
  - Deletes all `chunks` for that chunk set.
  - Inserts the new `chunks` with `position = 0..n-1`.
- **Non-replace (`replace=false`)**:
  - Returns existing `chunks.id` ordered by `position`.

Important:
- If you have vector embeddings keyed by `chunks.id`, you typically want cascading deletes from chunks → embeddings. (Your embedding tables reference `chunks.id` with `ON DELETE CASCADE`.)

## k8s deployment/service name
- Deployment: `store-chunks`
- Service: `store-chunks`
- Container command:
  - `python -m ethelflow.agents.store_chunks`
- Port:
  - `8000`

## Troubleshooting

### 500s referencing foreign keys
- Ensure `text_id` exists in `document_texts.id` for the document/version you are chunking.

### Concurrency weirdness or duplicate chunksets
- There should be a **unique constraint** on `(text_id, method)` in `chunksets`.  
  If it’s missing, concurrent writers can create duplicates. Add it via Alembic and re-run migrations.

### You get empty `chunk_ids`
- If `chunks` is an empty list, the service will delete existing rows (replace mode) and create none.  
  Verify your upstream chunking step produces content before calling `store-chunks`.
