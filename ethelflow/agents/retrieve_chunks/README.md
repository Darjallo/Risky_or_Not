# retrieve-chunks

## Overview
`retrieve-chunks` is a small utility service that **fetches the original text for stored chunk IDs** from Postgres.

Key behaviors:
- Accepts a list of `chunk_ids` (UUIDs).
- **Deduplicates** chunk IDs (preserves first-seen order).
- Returns each matching chunk **at most once**.
- **Silently drops missing IDs** (i.e., chunk IDs not found in the DB are omitted from the output).

This service is typically used **after vector search**, to turn retrieved `chunk_id` hits into the actual chunk text for prompting.

---

## Endpoint(s)

### `POST /retrieve_chunks`
Retrieve chunk texts for the given chunk IDs.

**Service URL inside cluster**
- `http://retrieve-chunks.default.svc:8000/retrieve_chunks`

---

## Request/Response

### Request model: `RetrieveChunksRequest`

Fields:
- `tenant` *(string, required)*: tenant label (kept for API consistency; **currently not used** in the DB query)
- `chunk_ids` *(list[uuid], optional)*: list of chunk UUIDs

Example JSON:
```json
{
  "tenant": "ethz",
  "chunk_ids": [
    "b7c33a2d-9e7e-4c51-bd32-4fb2d79c8e44",
    "b7c33a2d-9e7e-4c51-bd32-4fb2d79c8e44",
    "0c8aa5d7-4a8e-4c8c-8fb4-ea58b62c0f3b"
  ]
}
```

### Response model: `RetrieveChunksResponse`

Fields:
- `success` *(bool)*: whether the operation succeeded
- `message` *(string)*: informational message
- `chunk_ids` *(list[uuid])*: chunk IDs returned (deduped, ordered, missing dropped)
- `chunk_texts` *(list[string])*: texts aligned with `chunk_ids` by index

Example JSON:
```json
{
  "success": true,
  "message": "Returned 2 chunk(s)",
  "chunk_ids": [
    "b7c33a2d-9e7e-4c51-bd32-4fb2d79c8e44",
    "0c8aa5d7-4a8e-4c8c-8fb4-ea58b62c0f3b"
  ],
  "chunk_texts": [
    "First chunk text ...",
    "Second chunk text ..."
  ]
}
```

---

## Node adapter contract

File: `ethelflow/agents/retrieve_chunks/node_adapter.py`

### Default state input keys
- `tenant` (configurable via `tenant_key`)
- `chunk_ids` (configurable via `chunk_ids_key`)

Accepted input types:
- `chunk_ids` may contain `uuid.UUID` objects and/or UUID strings.

### Default state output keys
- `retrieve_chunks_response` (configurable via `output_key`): full response as JSON-friendly dict
- `chunk_texts` (configurable via `output_texts_key`): list of returned chunk texts

Example in a flow:
```python
retrieve = retrieve_chunks_node(
    chunk_ids_key="hit_chunk_ids",
    tenant_key="tenant",
    output_key="retrieve_chunks_response",
    output_texts_key="chunk_texts",
)
```

---

## Routing / Model catalog expectations
- This service **does not consult the model catalog**.
- `tenant` is accepted for API consistency and future-proofing, but the current implementation reads directly from the shared Postgres schema.

---

## Storage / DB interaction
Reads from:
- `chunks` table (SQLModel `Chunk`)

Query behavior:
- Performs `SELECT Chunk.id, Chunk.text WHERE Chunk.id IN (:chunk_ids)`
- Deduplicates input IDs first (preserves order).
- Builds an ID→text map from DB results.
- Returns outputs in the original first-seen order.
- Missing IDs are omitted (no error).

---

## k8s deployment/service name
- Deployment: `retrieve-chunks`
- Service: `retrieve-chunks`
- Container command: `python -m ethelflow.agents.retrieve_chunks`
- Port: `8000`

---

## Troubleshooting

### Returns fewer chunks than requested
- This is expected if:
  - Input contained **duplicates** (service returns each chunk once), and/or
  - Some `chunk_ids` **do not exist** in the database (missing IDs are silently dropped).

### `success=false` with message about tenant
- Ensure `tenant` is provided and is a non-empty string.

### HTTP 500 / database errors
- Confirm Postgres env vars are present in the deployment:
  - `ETHELFLOW_POSTGRES_HOST/PORT/DB/USER/PASSWORD`
- Check the pod logs:
  - `microk8s kubectl -n default logs deploy/retrieve-chunks`

### Timeouts
- Node adapter uses a 300s timeout for the HTTP call.
- If you expect very large `chunk_ids` lists, consider chunking requests client-side.
