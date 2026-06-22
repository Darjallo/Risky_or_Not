# `ethelflow/routes` — HTTP API routes (Assets + Flows)

This directory defines the **public HTTP interface** of the EthelFlow service. It exposes two main route groups:

- **Assets API** (`/assets/...`): a logical, versioned filesystem backed by **Postgres metadata** + **S3 object storage**.
- **Flows API** (`/flow/...`): the orchestration API that runs (and optionally resumes) **LangGraph flows** under `ethelflow/flows`.

These routes are what external tools (debug scripts, UIs, integrations) call. Agents under `ethelflow/agents` are *internal microservices* that flows call.

---

## Big picture: how the components fit together

### Assets path → DB rows → S3 objects
When you upload a file to `/assets`:

1. The route parses a **logical path** of the form:

   ```
   /{tenant}/{collection}/{subpath...}/{filename.ext}
   ```

2. It writes/updates metadata in Postgres tables:
   - `assets` (one row per logical file path)
   - `etheldocuments` (one row per **version** of that logical file)

3. It stores the binary content in S3 using the **document UUID as the object key**.

Retrieval uses `assets.latest_document_id` (unless you request an explicit version like `file.2.pdf`).

### Flows API → dynamic import → handler → agents
When you call `/flow`:

1. The route imports a flow module dynamically:
   - `import ethelflow.flows.<flow_name>`
2. It injects `tenant` into the flow’s initial state (critical for catalog routing).
3. It calls `ethelflow.handler.handler(...)`, which drives the flow generator and returns either:
   - a final JSON response (non-streaming), or
   - a streaming response (streaming).

For “interactive” flows using `interrupt(...)`, `/flow/start` + `/flow/{run_id}/continue` allow resuming the run via the Postgres checkpointer.

---

# Assets API (`routes/assets.py`)

## Logical paths and versioning

### Path format
All asset operations use a **logical path** string like:

- `/ethz/physics/mechanics/angular.pdf`
- `/ethz/physics/mechanics/angular.2.pdf`  *(explicit version request)*

Rules enforced by the router:
- Must start with `/`
- Must contain at least 3 segments: `/tenant/collection/filename`
- Must not contain `.` or `..` segments
- Normalizes repeated slashes (`//` → `/`)

### Versioned filenames (`foo.N.ext`)
The router recognizes versioned filenames via the pattern:

```
{stem}.{N}.{ext}
```

Example: `angular.2.pdf` means **version = 2**, and the base stored filename is normalized to `angular.pdf` for asset lookup.

Behavior:
- **Download**: `angular.2.pdf` returns that exact version (if it exists).
- **Upload**:
  - If you upload `angular.pdf` (no explicit version), the system creates **max(version)+1**.
  - If you upload `angular.2.pdf`, it writes version **2** (overwriting version 2 only if `overwrite=true`).

### Directory markers (`.keep`)
The service maintains “directories” by creating marker assets with filename `.keep`:
- They allow `/assets/ls` and `/assets/mkdir` to behave like a filesystem.
- Use `include_markers=true` on `/assets/ls` if you want to see them.

---

## Endpoints

### `POST /assets`
Upload a file to a logical path (creating a new version or overwriting a specific version).

**Query params**
- `path` (required): logical path, e.g. `/ethz/physics/mechanics/angular.pdf` or `angular.2.pdf`
- `overwrite` (default `true`):
  - If `false` and the asset exists *and you did not request an explicit version*, returns **409**
  - If you requested an explicit version and it exists, returns **409** unless `overwrite=true`
- `title` (optional): document title (defaults to base filename)

**Body**
- `multipart/form-data` with a single file field named `file`

**Response (JSON)**
```json
{
  "path": "/ethz/physics/mechanics/angular.pdf",
  "asset_id": "…",
  "document_id": "…",
  "version": 3,
  "content_type": "application/pdf",
  "latest_document_id": "…"
}
```

**Example (curl)**
```bash
curl -X POST "http://localhost:8080/assets?path=/ethz/physics/mechanics/angular.pdf&overwrite=true" \
  -F "file=@angular.pdf"
```

---

### `GET /assets`
Download a file by logical path (latest unless you request a version).

**Query params**
- `path` (required): `/tenant/collection/.../file.ext` or `file.N.ext`

**Response**
- Raw bytes with `Content-Type` set from `etheldocuments.content_type`
- `Content-Disposition: attachment; filename="..."`
  - Versioned requests preserve the requested filename.

**Example**
```bash
curl -L "http://localhost:8080/assets?path=/ethz/physics/mechanics/angular.2.pdf" -o angular.2.pdf
```

---

### `GET /assets/by-id/{document_id}`
Download a specific document version by UUID.

**Path params**
- `document_id` (required): UUID

**Example**
```bash
curl -L "http://localhost:8080/assets/by-id/01234567-89ab-cdef-0123-456789abcdef" -o file.bin
```

---

### `GET /assets/ls`
List “directory” contents.

**Query params**
- `path` (default `/`)
  - `/` → lists tenants
  - `/tenant` → lists collections
  - `/tenant/collection[/subdir...]` → lists directories + files
- `include_markers` (default `false`): include `.keep` entries

**Response (JSON)**
```json
{
  "path": "/ethz/physics/per",
  "directories": ["week1", "week2"],
  "files": [
    {"name": "angular.pdf", "latest_document_id": "…"},
    {"name": "notes.txt", "latest_document_id": "…"}
  ]
}
```

---

### `POST /assets/mkdir`
Create directory markers for a path.

**Query params**
- `path` (required): directory path like `/tenant/collection/subdir/...` (no filename)

**Response**
```json
{"created": true, "path": "/ethz/physics/per/week1"}
```

---

### `POST /assets/mv`
Move/rename an asset (metadata only).

**Query params**
- `src` (required): source logical path
- `dst` (required): destination logical path

Notes:
- This does not move S3 objects (objects are keyed by document UUID).
- A destination conflict returns **409**.

---

### `DELETE /assets`
Delete an entire asset or a specific version.

**Query params**
- `path` (required):
  - `/.../file.pdf` → delete the whole asset and all versions
  - `/.../file.2.pdf` → delete only version 2

Behavior:
- Deleting the last version also deletes the `assets` row.
- S3 deletions are best-effort; errors are reported for bulk deletes.

**Example**
```bash
curl -X DELETE "http://localhost:8080/assets?path=/ethz/physics/mechanics/angular.2.pdf"
```

---

## Storage/DB interaction (Assets)
- Metadata tables:
  - `assets`: `(tenant, collection, subpath, filename)` is unique
  - `etheldocuments`: stores `asset_id`, `version`, `title`, `content_type`
- Binary storage:
  - S3 key is `str(document_id)`
- Latest pointer:
  - `assets.latest_document_id` points to the document UUID of the newest version

---

## Troubleshooting (Assets)
- **409 on upload**: the asset or that version already exists and `overwrite=false`.
- **404 on download**:
  - wrong path, or
  - asset exists but has no versions (`latest_document_id` is null)
- **Wrong “latest”**:
  - latest is only updated when the new version is `>=` current latest version.
- **MIME type surprises**:
  - content type is detected from bytes via `python-magic`, not from filename extension.

---

# Flows API (`routes/flows.py`)

Flows are Python modules under `ethelflow/flows/` that expose:

```python
async def run(thread_id: uuid.UUID, context=None, stream=False, checkpointer=None, command=None):
    ...
    yield ...
```

The flows routes handle:
- Starting a run with a fresh `thread_id` (run id)
- Persisting checkpoints via `AsyncPostgresSaver`
- Resuming runs that used `interrupt(...)` via `Command(resume=...)`
- Optional streaming

---

## Core request types

### Run a flow (typical)
`POST /flow` takes a JSON body (model: `FlowRequest`) with at least:

- `flow`: string module name under `ethelflow.flows` (e.g. `"rag_chat"`)
- `tenant`: routing label (required; injected into flow state)
- `context`: free-form JSON dict (inputs to your flow)
- `stream`: boolean (controls streaming behavior)

**Example**
```json
{
  "flow": "rag_retrieve_test",
  "tenant": "ethz",
  "context": {
    "document_ids": ["..."],
    "prompt": "What is angular momentum?",
    "top_k": 10
  },
  "stream": false
}
```

**Important:** `/flow` injects `tenant` into the context so nodes can route via the model catalog.

---

### Continue a flow run
`POST /flow/{run_id}/continue` takes a body (model: `FlowContinueRequest`) with:

- `data`: the value to resume an `interrupt(...)` with
- `stream`: boolean

This works only if the flow used a Postgres checkpointer and has a saved checkpoint for that run id.

---

## Endpoints

### `POST /flow`
Run a flow immediately.

- Creates a new run id internally.
- Imports `ethelflow.flows.<flow>`.
- Calls `handler(...)` which executes the generator and returns the final result (or a streaming response if requested).

Use this when you don’t need to “attach” later.

---

### `POST /flow/start`
Start a streaming run and return `run_id` immediately.

- Spawns an async task that runs `mod.run(..., stream=True, ...)`
- Writes streamed events into an **in-memory queue** keyed by `run_id`.

**Response**
```json
{"run_id": "..."}
```

**Use with** `GET /flow/{run_id}/attach`.

⚠️ **Scale note:** because this uses in-memory queues (`flow_streams`), it assumes a single process/replica, or sticky routing. For multi-replica production streaming, you typically want a shared broker or server-side state store.

---

### `GET /flow/{run_id}/attach`
Attach to a started run via SSE (`text/event-stream`).

Events:
- `event: start`
- `event: stream` (payload is JSON-encoded chunk/event data)
- `event: complete`

---

### `POST /flow/{run_id}/continue`
Resume a checkpointed run with a `Command(resume=...)`.

- Loads the last checkpoint for this run id from Postgres.
- Imports the flow module from `checkpoint.metadata["flow"]`.
- Calls `handler(...)` with `command=Command(resume=continue_request.data)`.

This is primarily for flows that include **human-in-the-loop** steps using `interrupt(...)` (see `quiz.py`).

---

### `GET /flow/{run_id}/status`
Return the latest checkpoint tuple (debugging / monitoring).

### `GET /flow/{run_id}/history`
Return all checkpoints for a run.

---

## Streaming: what to send and what to expect

Streaming has been a source of fragility in many orchestration stacks. In EthelFlow, the safest pattern is:

- Only stream **final model output** intended for the user (strings)
- Do **not** stream intermediate state dicts that contain non-JSON-serializable types (UUIDs, bytes, complex objects)

**Recommended convention for flow authors**
- If `stream=True`, yield **plain strings only**
- If `stream=False`, return a final dict with useful debug fields (e.g. chunks, prompt, routing)

(Your `rag_chat.py` follows this convention by streaming only the final reasoning output.)

---

## Troubleshooting (Flows)
- **404 on continue/attach**: run id not found (no checkpoint or no queue).
- **Resume fails**: ensure the flow was compiled with a Postgres checkpointer, and that you’re resuming with the correct interrupt value type.
- **Streaming crashes**: typically caused by yielding objects that cannot be JSON-encoded or by mixing event shapes.
- **Tenant routing issues**:
  - `/flow` injects `tenant` into the context, but flows should still validate `tenant` and treat it as required.
  - Agents that use the model catalog require `tenant` (and optionally `embedding_space` or `inference_class`).

---

## Where to look next
- Writing flows: see `ethelflow/flows/README.md`
- Agents API + node adapters: see `ethelflow/agents/README.md`
- Data model and DB session helpers: see `ethelflow/data/README.md`
