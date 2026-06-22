# embedding

## Overview
`embedding` is a microservice that turns one or more text strings into vector embeddings using the provider/model routing defined in the **EthelFlow model catalog**.

Key points:
- **Routing comes from the model catalog**: `(tenant, space)` → `{provider, deployment, dimension, store_table}`.
- The service calls **Azure OpenAI Embeddings** using the resolved **deployment name**.
- An optional `deployment` field exists as a **debug/escape hatch** (discouraged), to override the catalog-resolved deployment.

## Endpoint(s)
- `POST /embedding`

## Request/Response

### Request: `EmbeddingRequest`
Fields:
- `tenant` *(str, required)*: Tenant label used for catalog routing.
- `texts` *(list[str], required)*: The texts to embed.
- `space` *(str | null, optional)*: Embedding space override. If `null`, uses tenant default from catalog.
- `deployment` *(str | null, optional)*: **Discouraged**. If provided, forces this Azure deployment name instead of catalog’s deployment.

Example JSON:
```json
{
  "tenant": "ethz",
  "texts": ["Hello world", "Second text"],
  "space": null
}
```

### Response: `EmbeddingResponse`
Fields:
- `embeddings` *(list[list[float]])*: One embedding per input text.
- `tenant` *(str)*: Tenant actually used.
- `space` *(str)*: Space actually used (resolved via catalog if not provided).
- `provider` *(str)*: Provider name selected from catalog.
- `deployment` *(str)*: Azure deployment name used.
- `usage` *(object | null)*: Provider usage metadata (if available).

Example JSON (shape):
```json
{
  "embeddings": [[0.1, 0.2], [0.3, 0.4]],
  "tenant": "ethz",
  "space": "ada3_large",
  "provider": "ethz_azure_openai",
  "deployment": "EthelEmb3large",
  "usage": {"prompt_tokens": 12, "total_tokens": 12}
}
```

## Node adapter contract (LangGraph)
Adapter: `ethelflow.agents.embedding.node_adapter.embedding_node(...)`

**Inputs (state keys)**
- `texts` *(default key: `"texts"`)*: `list[str]`
- `tenant` *(default key: `"tenant"`)*: `str`
- `embedding_space` *(default key: `"embedding_space"`)*: `str | None`

**Outputs (state keys)**
- `embeddings` *(default output key: `"embeddings"`)*: `list[list[float]]`

Minimal example:
```python
from ethelflow.agents.embedding.node_adapter import embedding_node

workflow.add_node(
    "embedding",
    embedding_node(
        input_texts_key="prompts",      # your state key holding list[str]
        tenant_key="tenant",
        space_key="embedding_space",
        output_key="embeddings",
    ),
)
```

## Routing / Model catalog expectations
This service requires the model catalog to be available in the container and loadable via:
- ConfigMap mount: `/etc/ethelflow/catalog.yaml`
- Env var: `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`

Routing behavior:
- If `space` is `null`, the service uses the tenant’s default embedding space from the catalog.
- The catalog route must define:
  - `provider` (with `endpoint` and `api_key_env`)
  - `deployment` (Azure deployment name)
  - `space` and `dimension`
  - (store table is part of the route but is not used by this service)

Provider auth:
- The code reads the provider API key from the environment variable named by `provider.api_key_env`
  (e.g., `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`).

Other relevant env vars:
- `ETHELFLOW_AZURE_OPENAI_API_VERSION` (default: `2025-04-01-preview`)
- `ETHELFLOW_DEFAULT_TENANT` (optional fallback if request omits tenant)

## Storage / DB interaction
None. `embedding` is stateless and does not write to Postgres/S3.

## k8s deployment/service name
- Deployment: `embedding`
- Service: `embedding`
- Container command: `python -m ethelflow.agents.embedding`
- Port: `8000`

(Your flows should call it via cluster DNS)
- `http://embedding.default.svc:8000/embedding`

## Troubleshooting

### 500: Missing provider API key env var
Symptom:
- Response detail includes `Missing provider API key env var ...`

Fix:
- Ensure the correct secret is mounted as an environment variable matching the catalog’s `api_key_env`
  (commonly `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`).

### 500: Model catalog not initialized / missing catalog file
Symptom:
- Startup errors or runtime `Model catalog not initialized`

Fix:
- Ensure the catalog ConfigMap is mounted at `/etc/ethelflow/catalog.yaml`
- Ensure `ETHELFLOW_MODEL_CATALOG_PATH` points to that path.

### 400: tenant is required
Fix:
- Provide `tenant` in the request, or set `ETHELFLOW_DEFAULT_TENANT` in the deployment env.

### Transient provider errors (429 / 500 / 503)
Behavior:
- The service uses exponential backoff retry for rate limits/timeouts/transient API errors.

Fix:
- If persistent, lower request concurrency, check provider quotas, or verify the deployment name is correct.
