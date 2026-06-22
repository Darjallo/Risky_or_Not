# ethelflow/apis

This directory contains the **public HTTP APIs** exposed by the EthelFlow gateway service (FastAPI).
It is intentionally small and split into three concerns:

- `chatapi/` — **OpenAI-compatible** endpoints (`/v1/chat/completions`, `/v1/responses`) with *pod-backed memory*
- `admin/` — **operator / platform** endpoints used to configure pod-backed **environments** (today: course environments)
- `common/` — shared FastAPI **dependency providers** (DB session → `PodStore`, `checkpointer`, etc.)

The goal is to let **oblivious clients** (e.g., an LTI chatbot plugin written by someone else) talk to something that *looks*
like a stateless OpenAI API, while EthelFlow transparently manages:

1) **Conversation state** (history)  
2) **Environment state** (templates, reference document sets, routing knobs)  


## Key concepts

### Pods (state, stored server-side)
A **pod** is a JSON object stored in Postgres (via `PodStore`) with metadata fields (tenant, owner API, type, revision, timestamps…).

EthelFlow uses pods for two distinct types of state:

- **Conversation pods** (`pod_type="conversation_context"`, `owner_api="chatapi"`)  
  Hold the canonical chat context (messages, routing_state, rag config, etc.).
  Clients can be memoryless: they only need to store/return an opaque `pod_id`.

- **Environment pods** (`pod_type="environment"`, `owner_api="<api>"`)  
  Hold configuration shared across many conversations, such as course templates and reference document sets.
  Environment pods are looked up by *deterministic IDs* so they can be updated “in place”.

Both are the same storage primitive; **pod_type + owner_api** is what separates concerns.


### “Follow latest” environments
Chat conversations should **use the latest environment** configuration for their course each request.
That means:

- Faculty can update templates/reference docs (fix typos, add documents)
- Existing conversations automatically pick up the newest environment settings

Implementation pattern:
- Router loads the conversation pod (history), then merges the current environment pod into the context (template/docs/knobs), then runs the flow, then writes back updated conversation context.


### Tenant routing
Requests must supply a tenant (e.g. `ethz`) so agents can route to the right model deployments and embedding spaces.
In ChatAPI this is accepted from:
- `X-Tenant` header, or
- `metadata.tenant`, or
- a default (currently `DEFAULT_TENANT = "ethz"`)

Flows generally expect `tenant` inside the context, so the router ensures it before invoking a flow.


## Directory overview

### `common/`
Shared dependency functions used by multiple APIs:

- `get_checkpointer(request)` returns the `AsyncPostgresSaver` stored on `app.state.checkpointer`
- `get_pod_store(session)` returns a `PostgresPodStore(session=session)`

This avoids duplicating “how to get DB session / pod store / checkpointer” across routers.

See: `common/deps.py`


### `chatapi/`
Provides OpenAI-ish API compatibility:

- `POST /v1/chat/completions`
- `POST /v1/responses`

Important behaviors:

- **Conversation memory** is stored in a conversation pod.
  - The server returns the pod id in `X-Pod-Id` header and a `pod_id` field in the JSON response.
  - The client can pass it back via `X-Pod-Id` header or `metadata.pod_id` (or `conversation` for `/v1/responses`).

- **Environment merge (follow latest)** happens every request:
  - `course_id` is taken from `metadata.course_id` (default `"default"`)
  - An environment pod id is deterministically derived from `(tenant, owner_api, env_type, env_id)`; for chatapi the current env_type is `"course"` and env_id is `course_id`.
  - If no environment pod exists, the flow runs with its own defaults.

- Router stays intentionally **generic**:
  - It normalizes base keys (`messages`, `routing_state`, `rag`, `debug`)
  - It merges environment knobs if present (template, docs, rag knobs, intent options)
  - It does **not** hardcode templates or intent schemas; those are flow concerns

See: `chatapi/router.py`, `chatapi/schemas.py`


### `admin/`
Provides the initial, schema-less management surface for environments:

- `GET /admin/environments/{owner_api}/{tenant}/{env_type}/{env_id}`
- `PUT /admin/environments/{owner_api}/{tenant}/{env_type}/{env_id}`

These endpoints create/update the environment pod at a deterministic id derived from the four path components.
The payload is intentionally open-ended (`config: Dict[str, Any]`) so that:
- different APIs can store different environment types
- future environment knobs can be added without migrations

See: `admin/router.py`


## How the pieces work together

### End-to-end request lifecycle (ChatAPI)
1) Client calls `POST /v1/chat/completions` or `POST /v1/responses`
2) Router resolves tenant
3) Router resolves conversation pod:
   - if `pod_id` provided → load it
   - else → create a new conversation pod with minimal empty context
4) Router appends new user message(s)
5) Router loads environment pod for `(tenant, course_id)` and merges its config into `ctx`
6) Router runs the flow via `handler(mod=..., context=ctx, checkpointer=..., thread_id=...)`
7) Router persists updated conversation context back into the same conversation pod (rev increments)
8) Router returns OpenAI-ish JSON plus `X-Pod-Id` header (capability handle for memoryless clients)


## How to use it (quick smoke tests)

### 1) Chat Completions (new conversation)
```bash
curl -i -X POST http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "rag_intent_chat",
    "messages": [{"role":"user","content":"hello"}],
    "metadata": {"tenant":"ethz", "course_id":"default"}
  }'
```

### 2) Chat Completions (continue conversation)
```bash
curl -i -X POST http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Pod-Id: <PASTE_FROM_PREVIOUS_RESPONSE_HEADER>' \
  -d '{
    "model": "rag_intent_chat",
    "messages": [{"role":"user","content":"second turn"}],
    "metadata": {"tenant":"ethz", "course_id":"default"}
  }'
```

### 3) Configure a course environment (Admin API)
Example: set a template + reference document ids for `course_id="physics101"`:

```bash
curl -i -X PUT http://localhost:8080/admin/environments/chatapi/ethz/course/physics101 \
  -H 'Content-Type: application/json' \
  -d '{
    "config": {
      "template_text": "You are the course assistant for Physics 101.\n\nUser: {{prompt}}\n",
      "document_ids": ["<uuid1>", "<uuid2>"],
      "rag": {"top_k": 8},
      "intent_options": {"version": 1, "default_intent": "chat", "options": {"exercise": {"description":"...", "examples":["..."]}}, "confidence_threshold": 0.7}
    }
  }'
```

Then call ChatAPI with:
```json
"metadata": {"tenant":"ethz","course_id":"physics101"}
```


## Adding a new API under `apis/`

When you add a new API (e.g. `filesapi/`, `gradeapi/`, `whateverapi/`), aim for:

### 1) New package structure
```
ethelflow/apis/<newapi>/
  router.py
  schemas.py   (optional but recommended)
  README.md
```

### 2) Give it an `owner_api` name
Pick a stable string, e.g. `"filesapi"`. Use it consistently:
- pod ownership (`owner_api`)
- admin environment paths (`/admin/environments/<owner_api>/...`)
- deterministic id derivation

### 3) Decide what state it needs
Common patterns:

- **Request-scoped only** (no pods)  
  If the API is fully stateless and doesn’t need memory or environments, skip pods.

- **Conversation/session state** (pods)  
  Store evolving state (history, progress, tool state) in a pod, return an opaque handle, accept it back.

- **Shared environment state** (pods)  
  Store reusable knobs (templates, course documents, default settings) in environment pods.
  Recompute/load the environment every request (follow-latest).

### 4) Reuse `common/deps`
Most APIs that touch pods should:
- depend on `get_pod_store`
- depend on `get_checkpointer` if using LangGraph checkpoints

This keeps wiring consistent and makes routers smaller.

### 5) Keep routers generic; keep flow logic in flows
Rule of thumb:

- Router is responsible for **protocol**:
  - request shapes, headers, metadata, pod lookup, environment merge, persistence, HTTP errors

- Flow is responsible for **behavior**:
  - templates (vanilla defaults)
  - intent schemas/defaults
  - what `ctx.rag` keys mean
  - what outputs are produced

This avoids “router knows too much about one flow”.

### 6) Register the router in the FastAPI app
In `ethelflow/__main__.py` (or wherever the FastAPI app is assembled):
- import the router
- `app.include_router(<new_router>)`
- keep the prefix/tag conventions consistent


## Environment schema conventions (recommended)

Even though environment pods are schema-less, it helps to converge on a few common keys so different APIs can share tooling:

- `config` (dict): the effective configuration object
- inside `config`, common optional keys:
  - `template_text` (str): template override
  - `document_ids` (list[str]): reference documents
  - `rag` (dict): rag knobs (top_k, extractor, method, embedding_space, etc.)
  - `intent_options` (dict): intent definitions/options

APIs are free to define additional keys. The Admin API deliberately does not validate beyond “is it JSON object”.


## Notes on authorization
Admin endpoints currently have **no auth** (by design during early build).
Long-term, Admin must be protected (authn/authz) and will likely integrate with SpiceDB.
Keep the routing stable now so auth can be layered in later without changing client integrations.


## Troubleshooting tips

- If ChatAPI returns 404 “Pod not found”: the client supplied an unknown/expired `pod_id`.
- If ChatAPI ignores your environment: make sure you used the right `owner_api`/`tenant`/`env_type`/`env_id` path in Admin.
- If you see `PodConflict`: you have concurrent writers; add `expected_rev` support or retry logic (future work).
- If flows error on missing keys: keep router normalization minimal, but ensure flow-specific defaults live in the flow itself.
