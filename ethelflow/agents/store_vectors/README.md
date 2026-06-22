# store_vectors

## Overview

`store_vectors` persists embedding vectors for already-created `chunks` into the tenant/space-specific pgvector table defined by the **model catalog**.

Typical pipeline position:

1. `store_chunks` creates `chunksets` + `chunks` and returns `chunk_ids`
2. `embedding` creates `embeddings` (list of vectors)
3. **`store_vectors`** upserts `(chunk_id -> vector)` into the correct embedding table for the tenant/space

Key properties:

- **Catalog-routed**: `(tenant, space)` → `(dimension, store_table)` via `ModelCatalog.tenant_embedding_route(...)`
- **Idempotent upsert**: `ON CONFLICT(chunk_id) DO UPDATE SET vector = EXCLUDED.vector`
- **One vector per chunk per embedding table**: `chunk_id` is the primary key


## Endpoint(s)

- `POST /store_vectors`


## Request/Response

### Request fields (`StoreVectorsRequest`)

- `chunk_ids` (list[uuid], **required**)  
  Chunk UUIDs (must match the chunks you want to attach vectors to).
- `embeddings` (list[list[float]], **required**)  
  Vectors aligned 1:1 with `chunk_ids`.
- `tenant` (str, **required**)  
  Tenant key (must exist in the catalog, e.g. `ethz`).
- `space` (str | null, optional)  
  Embedding space override. If `null`, the tenant default is used.

### Response fields (`StoreVectorsResponse`)

- `success` (bool)
- `message` (str)
- `num_vectors_stored` (int)
- `tenant` (str | null)  
- `space` (str | null)  
- `store_table` (str | null)  


### Example request

```json
{
  "tenant": "ethz",
  "space": null,
  "chunk_ids": [
    "3b0d7a76-7e2a-4f0a-b0a5-2c24c719eaaa",
    "5b7b2f8f-7f0e-4fa5-bd2b-7d8b8b44e111"
  ],
  "embeddings": [
    [0.01, 0.02, 0.03],
    [0.04, 0.05, 0.06]
  ]
}
```

> Note: The vectors in this example are intentionally tiny. In practice the vector length must exactly match the catalog dimension (e.g., 3072 for `ada3_large`).

### Example response

```json
{
  "success": true,
  "message": "",
  "num_vectors_stored": 2,
  "tenant": "ethz",
  "space": "ada3_large",
  "store_table": "embeddings_text_embedding_3_large"
}
```


## Node adapter contract

Module: `ethelflow.agents.store_vectors.node_adapter`

### Inputs (state keys)

By default the adapter reads:

- `embeddings` → list[list[float]]
- `chunk_ids` → list[uuid | str]
- `tenant` → str
- `embedding_space` → str | null  (passed as `space` to the service)

### Outputs (state keys)

- `store_vectors_response` → dict (the full `StoreVectorsResponse`, JSON-serializable)

### Minimal flow snippet

```python
from ethelflow.agents.store_vectors.node_adapter import store_vectors_node

store = store_vectors_node(
    embeddings_key="embeddings",
    chunk_ids_key="chunk_ids",
    tenant_key="tenant",
    space_key="embedding_space",
    output_key="store_vectors_response",
)
```


## Routing and model catalog expectations

`store_vectors` **requires** the model catalog to be mounted and the env var set:

- Mount: `/etc/ethelflow/catalog.yaml`
- Env var: `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`

Routing behavior:

- If `space` is `null`, the catalog’s tenant default embedding space is used.
- The resolved route determines:
  - `route.dimension` (vector length)
  - `route.store_table` (Postgres table to insert into)

The service validates that every vector length matches `route.dimension` and returns `success=false` on mismatch.


## Storage / DB interaction

### Target table

The target table name comes from the catalog: `route.store_table`.

The service constructs a lightweight SQLAlchemy `Table` object (no reflection) with:

- `chunk_id` UUID PRIMARY KEY
- `vector` `pgvector.Vector(dim)` NOT NULL

### Semantics

- Inserts are performed in bulk via `INSERT .. VALUES ..`
- Conflicts are resolved with:
  - `ON CONFLICT(chunk_id) DO UPDATE SET vector = EXCLUDED.vector`

### Requirements / assumptions

- The embedding table **must already exist** (created via Alembic/migrations).
- Postgres connectivity must be configured via the standard `ETHELFLOW_POSTGRES_*` env vars.


## k8s deployment/service name

- **Deployment:** `store-vectors`
- **Service:** `store-vectors`
- **Container command:** `python -m ethelflow.agents.store_vectors`
- **Port:** 8000/TCP

Minimal requirements (typical for your current cluster conventions):

- `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`
- Postgres env vars:
  - `ETHELFLOW_POSTGRES_HOST`, `ETHELFLOW_POSTGRES_PORT`
  - `ETHELFLOW_POSTGRES_DB`, `ETHELFLOW_POSTGRES_USER`, `ETHELFLOW_POSTGRES_PASSWORD`
- ConfigMap mount providing `catalog.yaml` at `/etc/ethelflow/catalog.yaml`


## Troubleshooting

### `chunk_ids length != embeddings length`

You must provide one vector per chunk ID (1:1 alignment). Fix upstream aggregation.

### `Vector dimension mismatch ... expected <dim>`

Your embedding service and the catalog route disagree. Check:

- Which `space` you passed (`embedding_space` state key → `space`)
- The catalog’s `dimension` for that tenant/space
- That your embedding service is producing the same dimension (e.g., 3072 for `text-embedding-3-large`)

### `Unsafe/invalid store table name`

The catalog’s `store_table` must be a safe SQL identifier (letters/numbers/underscore). Fix the catalog entry.

### Postgres errors / missing table

If the service can’t insert, confirm:

- Alembic migrations have created the table referenced by the catalog
- The Postgres env vars are set and correct
- The service can reach `postgres` in-cluster

### Catalog not found / wrong mount

If routing fails or `ModelCatalog.load()` errors:

- Ensure the ConfigMap is mounted at `/etc/ethelflow/catalog.yaml`
- Ensure `ETHELFLOW_MODEL_CATALOG_PATH` is set to that exact path
