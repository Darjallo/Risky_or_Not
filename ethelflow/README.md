# EthelFlow (service)

## Big picture

**EthelFlow** is the FastAPI orchestration layer for Project Ethel. It provides:

- **Assets API** (`/assets/...`): an app-level, versioned “virtual filesystem” backed by **Postgres metadata** + **S3/MinIO object storage**.
- **Flows API** (`/flow/...`): a thin execution harness for **LangGraph flows** (state machines) that call out to **agent microservices** (embedding, reasoning, chunking, vector store, etc.) and optionally **pause/resume** via checkpoints.
- **ChatAPI** (`/v1/...`): an OpenAI-compatible facade (`/v1/chat/completions`, `/v1/responses`) that persists conversation state in **PODs** (Postgres-stored JSON blobs) so *memoryless* clients can interact with stateful flows.
- **Tenant-aware model routing** via `model_catalog.yaml`: model deployments, embedding spaces, and vector store tables are chosen based on `tenant` (and optional routing hints like `embedding_space` or `inference_class`).

If you are developing:
- **Flows**: see `ethelflow/flows/README.md` (how to write new flows systematically).
- **Agent services / adapters**: see `ethelflow/agents/README.md`.
- **DB models / storage**: see `ethelflow/data/README.md`.
- **APIs (ChatAPI/Admin/Common)**: see `ethelflow/apis/README.md`.

---

## Directory map

- `__main__.py` — FastAPI app, startup/shutdown, routers, and “render README as HTML” landing page.
- `routes/` — HTTP API routes (`assets.py`, `flows.py`).
- `apis/` — additional API surfaces:
  - `chatapi/` OpenAI-compatible facade that maps requests into flows and persists POD state
  - `admin/` environment management endpoints (schema-less config stored in environment PODs)
  - `common/` shared FastAPI dependencies (checkpointer + pod store)
- `flows/` — LangGraph flow modules (mostly debugging/testing flows).
- `agents/` — “node adapters” and microservice implementations (one service per agent).
- `model_catalog.py` — catalog loader + tenant routing helpers for embedding/inference.
- `data/` — SQLAlchemy/SQLModel models and DB utilities (including POD storage).
- `assets/` — async S3 client wrapper used by routes and agents.
- `settings/` — environment-driven configuration (Postgres, S3, legacy Azure settings).

---

## App entry point

`ethelflow/__main__.py` defines:

- FastAPI app lifecycle
- LangGraph checkpointer initialization (Postgres-backed)
- S3 client initialization (MinIO/S3 compatible)
- Route mounting: `assets_router`, `flows_router`, plus routers from `ethelflow/apis/*`
- `/` endpoint: renders this README as HTML

### Checkpointer initialization (LangGraph)

We use LangGraph’s Postgres checkpointer (`AsyncPostgresSaver`) so flows can be:

- inspected (`/flow/{run_id}/status`, `/flow/{run_id}/history`)
- resumed after an interrupt (`/flow/{run_id}/continue`)

The checkpointer is created from `ETHELFLOW_POSTGRES_*` settings and stored on `app.state`:

- `app.state.checkpointer`
- `app.state.checkpointer_pool`

### S3/MinIO manager

`s3_manager` is initialized once at startup and reused across routes and services. It:

- creates an async S3 client (aiobotocore)
- ensures the target bucket exists
- provides `upload_file`, `download_file`, `delete_file`

---

## Tenant-aware model routing (`model_catalog.py`)

### Why the model catalog exists

EthelFlow runs in multi-tenant mode. Different tenants can:

- use different providers/endpoints
- use different model deployments
- use different embedding spaces (dimension)
- store vectors in different Postgres tables

This routing is described in a YAML **model catalog** (mounted into containers), and read at runtime by agents that need to route (e.g., `embedding`, `reasoning`, `search_vectors`, `store_vectors`).

### Catalog location

By default, the catalog is expected at:

- `/etc/ethelflow/catalog.yaml`

Override via env var:

- `ETHELFLOW_MODEL_CATALOG_PATH=/path/to/catalog.yaml`

In Kubernetes deployments, this is typically mounted from a ConfigMap (for example `ethelflow-model-catalog`) and the env var points at the mounted path.

### Providers and secrets

The catalog stores **non-secret** provider configuration (kind + endpoint). API keys are **not** stored in YAML.

Instead, `ModelCatalog` derives a provider-specific env var name from the provider name:

- provider `ethz_azure_openai` → `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`

This keeps secrets in Kubernetes Secrets / environment variables, not in git.

### Tenant routing requirements

Many nodes/agents require a **tenant** string to route correctly. In the Flows API we enforce this by injecting it into the flow state:

- `POST /flow` and `POST /flow/start` copy `flow_request.tenant` into `context["tenant"]`.

If you are writing a flow, treat `tenant` as required input state and pass it through to node adapters.

Common optional routing hints used by node adapters / flows:

- `embedding_space`: which embedding space to use (otherwise tenant default in catalog)
- `inference_class`: which inference class to use (otherwise flow defaults)
- `deployment`: optional override in some flows (prefer catalog routing unless debugging)

---

# Routes

EthelFlow exposes these main API surfaces:

- `/assets` — manage documents (upload/download/list/mkdir/mv/rm) using logical paths
- `/flow` — execute LangGraph flows (run, start/attach, continue, status/history)
- `/v1` — OpenAI-compatible Chat API facade (conversation PODs + environment POD merge)
- `/admin` — environment management endpoints (used to set per-course config)

For canonical request/response shapes, always consult the live Swagger UI:
`http://localhost:8080/docs`.

## Assets API (`routes/assets.py`)

### Concepts: logical paths + versioning

Assets are addressed by an app-level logical path:

```
/{tenant}/{collection}/{subdirs...}/{filename.ext}
```

Assets are versioned. You can request a specific version by using a *versioned filename*:

```
/ethz/physics/mechanics/angular.2.pdf
```

Internally, versions are stored as separate `EthelDocument` rows with `version = N`.
The asset’s base filename used for lookup/storage is normalized back to `angular.pdf`.

### Storage model (DB + S3)

- **Postgres**
  - `assets` table: logical path components + pointer to latest document id
  - `etheldocuments` table: one row per version (UUID id, version int, content_type, title, timestamps)
- **S3/MinIO**
  - object key is the **document UUID** (`EthelDocument.id`)

### Key endpoints

- `POST /assets?path=...` — upload a new version (or overwrite a specific version)
- `GET /assets?path=...` — download latest (or a specific version if filename is versioned)
- `GET /assets/by-id/{document_id}` — download by document UUID
- `GET /assets/ls?path=/...` — list “directories” and files under a prefix
- `POST /assets/mkdir?path=/tenant/collection[/subdir...]` — create directory markers
- `POST /assets/mv?src=...&dst=...` — move/rename (metadata only; S3 objects unchanged)
- `DELETE /assets?path=...` — delete an entire asset or a specific version (if versioned filename)

### Upload semantics

- MIME type is detected using `python-magic` (based on file bytes, not extension).
- If you upload without specifying a version (plain filename), the service creates `max(version)+1`.
- If you upload with a versioned filename (`foo.N.ext`), the service targets that explicit version:
  - `overwrite=false` → 409 if that version already exists
  - `overwrite=true` → replace that version (DB row overwritten; old S3 object best-effort deleted)
- “Directories” are represented by `.keep` marker assets to support `ls` / `mkdir`.

### Minimal examples

**Upload (multipart):**

```bash
curl -X POST "http://localhost:8080/assets?path=/ethz/physics/mechanics/angular.pdf"   -F "file=@angular.pdf"
```

**Download latest:**

```bash
curl -L "http://localhost:8080/assets?path=/ethz/physics/mechanics/angular.pdf" -o angular.pdf
```

**Download version 2:**

```bash
curl -L "http://localhost:8080/assets?path=/ethz/physics/mechanics/angular.2.pdf" -o angular.2.pdf
```

## Flows API (`routes/flows.py`)

### What a “flow” is

A flow is a LangGraph state machine implemented in `ethelflow/flows/<name>.py` with an async entry point:

```py
async def run(thread_id: uuid.UUID, context=None, stream=False, checkpointer=None, command=None):
    ...
```

Flows typically call agent microservices via the node adapters in `ethelflow/agents/*/node_adapter.py`.

### Execution modes

EthelFlow supports two “streaming” patterns:

1. **Inline streaming** via `POST /flow` (`StreamingResponse`, `application/json`)
   - The handler directly yields whatever the flow yields in `stream=True` mode.
   - Best when you have a client that can consume chunked HTTP responses.

2. **Start + attach (SSE)** via `POST /flow/start` and `GET /flow/{run_id}/attach`
   - `/flow/start` starts the flow in a background task, returns `run_id`
   - `/flow/{run_id}/attach` streams Server-Sent Events (`text/event-stream`) from an in-memory queue
   - Best for UI clients that like SSE.

### Checkpointing and resume

- Every run has a `run_id` (UUID) which is also the LangGraph `thread_id`.
- Checkpoints are stored in Postgres via `AsyncPostgresSaver`.
- Flows that call `interrupt(...)` can be resumed using:

  - `POST /flow/{run_id}/continue`

  This endpoint loads the stored checkpoint, reconstructs the flow module, and calls `handler(..., command=Command(resume=...))`.

> Important: the value passed to `Command(resume=...)` is flow-defined.
> Some flows expect a simple string; others may expect structured JSON. Check the flow code and/or `/docs`.

### Key endpoints (summary)

- `POST /flow` — run a flow (optionally streaming) in one request
- `POST /flow/start` — start a flow in the background and return `run_id`
- `GET /flow/{run_id}/attach` — attach to background run via SSE
- `POST /flow/{run_id}/continue` — resume an interrupted flow run
- `GET /flow/{run_id}/status` — get latest checkpoint
- `GET /flow/{run_id}/history` — list all checkpoints

### Request shape: `FlowRequest`

```json
{
  "flow": "rag_chat",
  "tenant": "ethz",
  "context": { "prompt": "Hello", "document_ids": [] },
  "stream": false
}
```

- `tenant` is required and is injected into the flow state as `context["tenant"]`
- `context` is flow-specific
- `stream=true` enables streaming output where supported by the flow

### Streaming guidance for flow authors

If you want a flow to stream safely through both mechanisms:

- Prefer yielding **strings** (already-serialized content chunks) or small JSON-serializable dicts.
- Avoid yielding non-serializable objects (Pydantic models, UUID objects, sessions, etc.) in streaming mode.
- If streaming comes from an upstream service (e.g., reasoning), pass it through as decoded strings.

---

# Handler (`handler.py`)

The handler is used by the Flows API to execute `mod.run(...)`.

- If `stream=True`: returns a `StreamingResponse` and yields items from the flow generator.
- If `stream=False`: runs the flow generator and returns the **first** yielded result (or `{}`).

This design keeps the HTTP layer small and pushes the “what to yield” responsibility to the flow.

---

## See also

- `ethelflow/apis/README.md` — ChatAPI/Admin/Common design and POD-based configuration
- `ethelflow/agents/README.md` — agent services and node adapter contracts
- `ethelflow/flows/README.md` — how to write flows (inputs/outputs/streaming/interrupts)
- `ethelflow/routes/README.md` — API usage details and interplay with other system components
- `ethelflow/data/README.md` — DB schema (assets/text/chunks) and vector storage notes
