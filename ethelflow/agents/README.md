# EthelFlow Agents

This directory contains **agent services** (each one is a small FastAPI microservice) plus **LangGraph node adapters** that make those services easy to call from flows.

The intended pattern is:

- **Agents** do one thing well (embed, search, retrieve, render templates, etc.).
- **Flows** (in `ethelflow/flows/`) compose agents into end-to-end pipelines (ingest, retrieve, chat, tooling).

---

## What’s in here

Each agent subdirectory typically contains:

- `__main__.py` — the FastAPI service (runs in its own k8s Deployment/Service)
- `models.py` — Pydantic request/response models (HTTP contract)
- `node_adapter.py` — a LangGraph-compatible async generator that:
  1) reads values from state by key  
  2) calls the service over HTTP  
  3) yields JSON-friendly results back into state

---

## Quick map: agents and what they do

### Retrieval / RAG core
- **embedding/**
  - **Purpose:** Convert text → embeddings via catalog-routed provider (Azure OpenAI).
  - **Endpoint:** `POST /embedding`
  - **Node adapter:** `embedding_node(...)` → yields `{"embeddings": [[...], ...]}`

- **search_vectors/**
  - **Purpose:** Vector search in pgvector tables (space/table/dimension resolved via model catalog).
  - **Endpoint:** `POST /search_vectors`
  - **Node adapter:** `search_vectors_node(...)` → yields chunk IDs (plus full response)

- **retrieve_chunks/**
  - **Purpose:** Fetch chunk texts from DB by chunk IDs (dedup + preserve order).
  - **Endpoint:** `POST /retrieve_chunks`
  - **Node adapter:** `retrieve_chunks_node(...)` → yields `chunk_texts`

### Ingestion / indexing
- **file_to_text/**
  - **Purpose:** Extract text from stored documents (S3), supports `application/pdf`, `text/plain`, `text/html`.
  - **Endpoint:** `POST /file_to_text`
  - **Node adapter:** `file_to_text_node(...)` → yields `text`

- **chunk_text/**
  - **Purpose:** Split text into overlapping chunks (LangChain `RecursiveCharacterTextSplitter`).
  - **Endpoint:** `POST /chunk_text`
  - **Node adapter:** `chunk_text_node(...)` → yields `texts` (list of chunk strings)

- **store_text/**
  - **Purpose:** Store extracted text in DB as a `document_texts` row (`document_id`, `extractor`, `text`).
  - **Endpoint:** `POST /store_text`
  - **Node adapter:** `store_text_node(...)` → yields `text_id` and response

- **store_chunks/**
  - **Purpose:** Store chunk strings into DB as a `chunksets` + `chunks` for a given `text_id` + `method`.
  - **Endpoint:** `POST /store_chunks`
  - **Node adapter:** `store_chunks_node(...)` → yields response (chunk IDs etc.)

- **store_vectors/**
  - **Purpose:** Insert/update embeddings into the pgvector table for the tenant’s embedding space.
  - **Endpoint:** `POST /store_vectors`
  - **Node adapter:** `store_vectors_node(...)` → yields response

### Prompt construction / reasoning / tools
- **complete_template/**
  - **Purpose:** Render Mustache templates (chevron) with optional conditional sections; supports “empty-normalization”.
  - **Endpoint:** `POST /complete_template`
  - **Node adapter:** `complete_template_node(...)` → yields `rendered_template`

- **reasoning/**
  - **Purpose:** LLM inference (catalog-routed); supports prompt-only and optional inline images / documents.
  - **Endpoints:** `POST /reasoning`, `POST /reasoning_with_document`
  - **Node adapter:** `reasoning_node(...)` → yields either streamed text chunks or final text (depending on `stream`)

- **executor/**
  - **Purpose:** Run code in a Kubernetes Job (python/r/maxima) using an image + base64 script.
  - **Endpoint:** `POST /execute`
  - **Node adapter:** `executor_node(...)` → yields execution result

---

## How flow developers should think about composition

### The “ingest” pipeline (document → indexed vectors)

Typical end-to-end steps:

1. **file_to_text**: `document_id` → `text`
2. **store_text**: (`document_id`, `extractor`, `text`) → `text_id`
3. **chunk_text**: `text` → `chunks[]`
4. **store_chunks**: (`text_id`, `chunks[]`, `method`) → `chunk_ids[]`
5. **embedding**: `chunks[]` → `embeddings[]`
6. **store_vectors**: (`chunk_ids[]`, `embeddings[]`, `tenant`, `space`) → stored

Key idea: you want the same `(extractor, method, space)` triplet to be used consistently so retrieval can find the right chunkset + embeddings.

### The “retrieve” pipeline (prompt + scope → chunks)

Typical steps:

1. **embedding**: prompt → `query_embedding`
2. **search_vectors**: (`document_ids`, `extractor`, `method`, `query_embedding`) → `hit_chunk_ids`
3. **retrieve_chunks**: `hit_chunk_ids` → `chunk_texts`

Example: `rag_retrieve_test` does exactly this.

### The “chat RAG” pipeline (prompt + history + chunks → final answer)

Common extension:

1. retrieve chunks (as above)
2. **complete_template**: build the final model prompt from:
   - user prompt
   - dialogue history
   - retrieved chunks
   - system/instructions
3. **reasoning**: call the model and (optionally) stream the *final answer only*

---

## Node adapter contract (what flows need to know)

All node adapters in this repo follow the same shape:

- They read required inputs from `state` using configured `*_key` parameters.
- They validate types early and throw `ValueError` on bad/missing state.
- They call the service using cluster DNS (`http://<svc>.<ns>.svc:8000/<endpoint>`).
- They yield a dict update to be merged into graph state.

**Practical implications:**

- Keep state JSON-friendly when possible (UUIDs often become strings).
- Be consistent with key names across your flow; adapt with `..._key=` parameters rather than adding “glue” nodes unless needed.

---

## Routing and the model catalog

Embedding + reasoning route via `ModelCatalog`:

- **Embedding** resolves:
  - `tenant` + optional `space` → provider + deployment + dimension + store_table
- **Reasoning** resolves:
  - `tenant` + inference class (env `ETHELFLOW_INFERENCE_CLASS`, typically `"reasoning"`) → provider + deployment

**Important (current behavior):** the `/flow` endpoint does not always inject `FlowRequest.tenant` into `context`, so many flows defensively require `context["tenant"]`. When debugging, always include `tenant` inside `context`.

---

## About “halfvec” (high-dimensional embedding spaces)

For high-dimensional vectors (e.g., 3072-d embeddings like `ada3_large`), `search_vectors` uses a `halfvec(dim)` cast to hit an ANN index (`halfvec_cosine_ops`). This is a performance tradeoff:

- **Pros:** much faster ANN similarity search at scale
- **Cons:** quantization to half precision can slightly reduce similarity fidelity

In practice, the impact is usually small compared to other RAG quality factors (chunking, extractor quality, filtering scope), but it *can* affect borderline matches. If you ever need exact scoring, you’d add a reranking step or a second-stage exact distance computation (not implemented here).

---

## Kubernetes / service naming conventions

By convention, each agent runs as:

- **Service:** `<agent-name>.default.svc` (e.g. `embedding.default.svc`)
- **Container port:** `8000`

Flows call agents by those stable service names via the adapters.

Embedding + reasoning additionally require:

- Model catalog mounted at `/etc/ethelflow/catalog.yaml` and env:
  - `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`
- Provider API key env var defined by the catalog provider entry
  (e.g. `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`)

---

## Troubleshooting (flow developer focused)

### 1) “HTTP 500” from an agent
- Check agent pod logs (`kubectl logs deploy/<agent> -n default`).
- Validate request payload matches Pydantic model (types + required fields).

### 2) “Vector dimension mismatch”
- The embedding service is returning vectors for a different space than your DB/index expects.
- Ensure you pass the intended `embedding_space` (or rely on tenant default consistently).
- Ensure the DB table for that space exists (alembic migrated).

### 3) Retrieval returns empty chunks
Common causes:
- No embeddings stored for the `(document_ids, extractor, method, space)` combination
- Different `method` label between ingestion and retrieval
- Wrong extractor label (e.g., `file_to_text` vs `pdfminer` etc.)
- `document_ids` don’t correspond to texts/chunks you indexed

### 4) Streaming issues
We’ve seen issues streaming intermediate LangGraph states through `/flow`.
A safe pattern is:
- run intermediate steps non-streaming
- stream **only the final LLM answer** (via the reasoning agent)

---

## Minimal example snippets

### Retrieval-only (shape similar to `rag_retrieve_test`)
- prompt → embed → vector search → retrieve chunk texts

Key state keys you typically use:
- `tenant`, `embedding_space` (optional)
- `document_ids`, `extractor`, `method`, `top_k`
- `prompt` → `prompts` → `embeddings` → `query_embedding`
- `hit_chunk_ids` → `chunk_texts`

### Ingestion sketch (document → vectors)
- `document_id` → file_to_text → store_text → chunk_text → store_chunks → embedding(chunks) → store_vectors

---

## Where to look next

- Flows: `ethelflow/flows/`
- Debug scripts: `ethelflow/debug/`
- Model routing: `ethelflow/model_catalog.py`
- DB models/tables: `ethelflow/data/models.py`

If you add a new agent, follow the existing pattern:
1) define Pydantic models  
2) implement FastAPI service in `__main__.py`  
3) write `node_adapter.py` with strict input validation + JSON-friendly output  
4) add a per-agent `README.md` describing inputs/outputs and flow integration
