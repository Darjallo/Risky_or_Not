# search_vectors

## Overview
`search_vectors` performs approximate nearest-neighbor (ANN) retrieval over stored pgvector embeddings, returning the best-matching **chunk IDs** (and their distances) for a **query vector**, constrained to a set of **document IDs**, a **text extractor**, and a **chunking method**.

At a high level, this is the “R” step in RAG retrieval:
1) embed the query elsewhere (embedding service)  
2) **search** the embedding store for relevant chunks (this service)  
3) fetch chunk texts (retrieve_chunks service)

This service is catalog-routed: it uses `ModelCatalog` to resolve the tenant’s embedding **space**, **dimension**, and **store table**.

---

## Endpoint(s)
- `POST /search_vectors`

Kubernetes DNS (inside cluster):
- `http://search-vectors.default.svc:8000/search_vectors`

---

## Request/Response

### Request fields
`SearchVectorsRequest`
- `tenant` (str, required): tenant label used for model-catalog routing.
- `space` (str | null, optional): embedding space override. If `null`, uses the tenant default from the catalog.
- `document_ids` (list[uuid], optional): restrict search to these document IDs. Empty list returns an empty result (success).
- `extractor` (str, required): extractor label used when `document_texts` were created (e.g. `"file_to_text"`).
- `method` (str, required): chunking method label used when chunks were created (e.g. `"recursive_char_1000_100_htmlstrip"`).
- `query_vector` (list[float], required): query embedding vector. **Must match** the catalog dimension for the resolved (tenant, space).
- `top_k` (int, default 10, 1–500): number of best matches to return.

### Response fields
`SearchVectorsResponse`
- `success` (bool)
- `message` (str)
- `tenant` (str | null): echoed tenant
- `space` (str | null): resolved space (tenant default if request omitted it)
- `store_table` (str | null): resolved embedding table name from the catalog
- `chunk_ids` (list[uuid]): best matching chunk ids
- `distances` (list[float]): distances aligned with `chunk_ids`

### Example request
```json
{
  "tenant": "ethz",
  "space": null,
  "document_ids": [
    "4a70cc94-93a2-4ef2-b751-e2a6399fdf6b",
    "697a9f34-72fe-4ed6-81c6-05eabf0747c7"
  ],
  "extractor": "file_to_text",
  "method": "recursive_char_1000_100_htmlstrip",
  "query_vector": [0.0123, -0.0456, 0.0789],
  "top_k": 10
}
```

### Example response
```json
{
  "success": true,
  "message": "",
  "tenant": "ethz",
  "space": "ada3_large",
  "store_table": "embeddings_text_embedding_3_large",
  "chunk_ids": [
    "c62a2f5a-1bf8-4a65-a5b0-8e6c5f24f0c4",
    "95b521a1-1957-44a4-bebb-1c351b7d64a1"
  ],
  "distances": [0.1123, 0.1289]
}
```

---

## Node adapter contract

### Node factory
`search_vectors_node(...)` in `ethelflow/agents/search_vectors/node_adapter.py`

### Inputs (state keys)
By default, the node reads:
- `tenant` (via `tenant_key`, default `"tenant"`)
- `embedding_space` (via `space_key`, default `"embedding_space"`, optional)
- `document_ids` (via `document_ids_key`, default `"document_ids"`)
- `extractor` (via `extractor_key`, default `"extractor"`)
- `method` (via `method_key`, default `"method"`)
- `query_vector` (via `query_vector_key`, default `"query_vector"`)
- `top_k` (via `top_k_key`, default `"top_k"`, default 10)

Notes:
- `document_ids` accepts `uuid.UUID` objects or UUID strings.
- `query_vector` must be a list of numbers; it is coerced to floats.

### Outputs (state keys)
The node yields:
- `search_vectors_response` (via `output_key`, default `"search_vectors_response"`): full response JSON
- `chunk_ids` (via `output_chunk_ids_key`, default `"chunk_ids"`): **list[str]** chunk IDs (stringified UUIDs) for convenience downstream

---

## Routing/Model catalog expectations

- The service loads `ModelCatalog` and resolves:
  - tenant default embedding space (if request `space` is `null`)
  - embedding dimension (must match `len(query_vector)`)
  - store table name (where embeddings are stored)

Expected catalog fields (conceptually):
- `tenants.<tenant>.embedding_default_space`
- `embedding_routes.<space>.dimension`
- `embedding_routes.<space>.store_table`
- provider/deployment routing is not used here (this is DB-only).

---

## Storage/DB interaction

### Tables involved
- Embedding store table from catalog (e.g. `embeddings_text_embedding_3_large`), with columns:
  - `chunk_id` (uuid)
  - `vector` (pgvector)
- `chunks` (maps `chunk_id` to chunkset)
- `chunksets` (has `method` and `text_id`)
- `document_texts` (has `document_id` and `extractor`)

### Query behavior
- Restricts by:
  - `document_texts.document_id IN document_ids`
  - `document_texts.extractor = extractor`
  - `chunksets.method = method`
- Orders by cosine distance (`<=>`) and returns top_k chunk IDs.

### ANN index usage & halfvec
- If `dimension > 2000`, the service uses `halfvec(dim)` casts in the ORDER BY expression to match the HNSW index that was built on `vector::halfvec(dim)` with `halfvec_cosine_ops`.
- If `dimension <= 2000`, it uses `vector` distance directly.

This is purely a performance optimization (matching the index expression) — it should not change the logical filtering.

---

## k8s deployment/service name
- Deployment: `search-vectors`
- Service: `search-vectors`
- Namespace: `default`
- Container port: `8000`
- Path: `/search_vectors`

---

## Troubleshooting

### “Vector dimension mismatch”
- Your `query_vector` length must match the catalog dimension for the resolved (tenant, space).  
  Fix by ensuring your embedding step uses the same `space` as the store table you are querying.

### “Embedding table ... does not exist”
- The catalog points to a table that is not present in Postgres.
  - Run migrations (`alembic upgrade head`)
  - or adjust the catalog’s `store_table` to match existing tables.

### Empty results
- Verify you are using the same `(extractor, method)` that was used when generating chunks and embeddings.
- Verify the `document_ids` you pass actually have `document_texts` rows for that extractor and chunksets for that method.

### HTTP 500 from service
- Check the pod logs for the `search-vectors` deployment.
- Ensure Postgres is reachable and pgvector is installed/enabled.
