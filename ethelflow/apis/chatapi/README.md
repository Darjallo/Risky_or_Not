# ChatAPI (OpenAI-compatible façade) — `ethelflow/apis/chatapi`

This package exposes an **OpenAI-ish HTTP API** on top of EthelFlow “flows”, while still supporting **memoryless clients** (clients that do not manage chat state) and **course/environment configuration** (templates, reference docs, intent definitions, etc.).

The key idea is:

- Clients call `POST /v1/chat/completions` or `POST /v1/responses` (like OpenAI).
- The server stores and updates **conversation state** in a **Pod** (DB-backed).
- The server optionally merges in a **course environment** (also stored as a Pod) **on every request** (“follow latest”), so course staff can update templates/docs without breaking existing conversations.
- A client can remain stateless by simply replaying the returned `X-Pod-Id` header.

---

## Files

- `router.py` — FastAPI router implementing:
  - `POST /v1/chat/completions`
  - `POST /v1/responses`
- `schemas.py` — Pydantic request schemas.

---

## Concepts

### 1) Conversation Pod (“memory”)

A **conversation** is stored as a Pod of:

- `owner_api = "chatapi"`
- `pod_type  = "conversation_context"`

The Pod’s `data` field is a JSON object. The router keeps this **flow-agnostic** and only ensures basic container keys exist:

- `messages: []` (canonical chat history)
- `routing_state: {}` (optional flow routing metadata)
- `rag: {}` (RAG knobs: `document_ids`, `template`, `top_k`, etc — flow-dependent)
- `debug: {}` (kept in-memory; **not persisted**)

The Pod id (a UUID) is returned to clients via:

- HTTP response header: `X-Pod-Id: <uuid>`
- Response body field (extra, clients can ignore): `pod_id` (chat) or `conversation` (responses)

This makes the Pod id an **opaque capability handle** a stateless client can replay.

### 2) Environment Pod (“course config”, follow-latest)

A **course environment** is also a Pod:

- `owner_api = "chatapi"`
- `pod_type  = "environment"`

The environment is selected by `metadata.course_id` (default: `"default"`).  
Each request merges the latest environment into the conversation context **before running the flow**.

Environment Pod IDs are deterministic (UUIDv5), derived from `(tenant, course_id)`:

```python
uuid.uuid5(
    uuid.NAMESPACE_URL,
    f"ethelflow:chatapi:env:course:{tenant}:{course_id}",
)
```

This is **not a secret**—it’s just a stable key.

---

## Request processing pipeline

For **both** endpoints the router does (simplified):

1. Determine `tenant` (from header `X-Tenant` or `metadata.tenant`, else `ethz`)
2. Load or create conversation Pod (from `X-Pod-Id` / `metadata.pod_id` / `conversation`)
3. Ensure base context containers exist (`messages`, `routing_state`, `rag`, `debug`)
4. Append the incoming user turn
5. Merge environment Pod (based on `metadata.course_id`, “follow latest”)
6. Run the flow via `ethelflow.handler.handler()` with a stable per-pod `_thread_id`
7. Persist updated context back into the conversation Pod (**without** persisting `debug`)
8. Return an OpenAI-like response + `X-Pod-Id`

---

## Endpoints

### `POST /v1/chat/completions`

OpenAI-like chat endpoint.

**Request body (minimal):**
```json
{
  "model": "rag_intent_chat",
  "messages": [{"role":"user","content":"hello"}],
  "metadata": {"tenant":"ethz"}
}
```

**State & continuation:**
- First request creates a Pod and returns `X-Pod-Id`.
- Subsequent requests pass `X-Pod-Id` to continue the same conversation.

### `POST /v1/responses`

OpenAI-like “Responses” endpoint.

**Request body (minimal):**
```json
{
  "model": "rag_intent_chat",
  "input": "hello",
  "metadata": {"tenant":"ethz"}
}
```

**Continuation:**
- Use `conversation` field **or** `X-Pod-Id` header on subsequent calls.

---

## Headers and metadata fields

### Tenant selection

Tenant is resolved as:

1. `X-Tenant` header (if set)
2. `req.metadata.tenant` (if set)
3. `DEFAULT_TENANT = "ethz"`

### Pod selection (conversation state)

For `/v1/chat/completions`:

- `metadata.pod_id` or header `X-Pod-Id`

For `/v1/responses`:

- `req.conversation` (preferred)
- else `metadata.pod_id`
- else header `X-Pod-Id`

### Course environment selection (follow latest)

- `metadata.course_id` (default `"default"`)
- Optional for dev/testing: `metadata.env_pod_id` to override the computed environment Pod id.

---

## Environment pod schema (current)

The environment Pod’s JSON can be either:

- `{"config": {...}}` (recommended), or
- a “flat” dict directly in `data`

Recognized keys inside `config` (all optional; router stays generic):

- `intent_options` (dict)  
  → merged into `ctx["intent_options"]` (flow may or may not use it)

- `template_text` (string)  
  → written to `ctx["rag"]["template"]`

- `document_ids` (list)  
  → written to `ctx["rag"]["document_ids"]` as strings

- `rag` (dict)  
  → shallow-merged into `ctx["rag"]`

> **Note:** The router intentionally does not validate these beyond basic types. Flows own the “meaning” and validation of the context they consume.

Example environment config:

```json
{
  "config": {
    "template_text": "You are the course assistant for Physics 101... {{prompt}}",
    "document_ids": ["7b7b2d9b-..."],
    "intent_options": {
      "version": 1,
      "default_intent": "chat",
      "options": {
        "exercise": {"description": "...", "examples": ["quiz me"]}
      },
      "confidence_threshold": 0.7
    },
    "rag": {
      "top_k": 8,
      "method": "recursive_char_1000_100_htmlstrip"
    }
  }
}
```

---

## Debug behavior

Flows may put rich debugging details into `context["debug"]`.

- The router **returns** `debug` in the HTTP response (for dev tooling).
- The router **does not persist** `debug` into the Pod (it strips it before writes).

This keeps persistent state small and avoids accidentally persisting sensitive prompts/logs.

---

## Flow selection and responsibilities

The router defaults to:

- `DEFAULT_FLOW = "rag_intent_chat"`

A client can override per request using:

- `metadata.flow = "<flow_module_name>"` (e.g., `"rag_intent_chat"`)

**Important design choice:** the router is generic and does not hardcode flow-specific defaults
(template text, intent definitions, etc.). Those should live in:

- the flow implementation itself (defaults), and/or
- the environment pod (course-specific overrides), and/or
- client-provided `metadata.initial_context` (power-user clients)

---

## Smoke tests (curl)

### 1) Create a new conversation (chat/completions)

```bash
curl -i -X POST http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "rag_intent_chat",
    "messages": [{"role":"user","content":"hello smoketest"}],
    "metadata": {"tenant":"ethz"}
  }'
```

Look for:
- `HTTP/1.1 200 OK`
- `x-pod-id: <uuid>`

### 2) Continue that conversation (pass X-Pod-Id)

```bash
curl -i -X POST http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'X-Pod-Id: <PASTE_POD_ID_HERE>' \
  -d '{
    "model": "rag_intent_chat",
    "messages": [{"role":"user","content":"second turn; what did I just say?"}],
    "metadata": {"tenant":"ethz"}
  }'
```

### 3) Responses endpoint

```bash
curl -i -X POST http://localhost:8080/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "rag_intent_chat",
    "input": "hello from responses smoketest",
    "metadata": {"tenant":"ethz"}
  }'
```

---

## Failure modes you’ll see

- `400 Invalid pod_id` / `Invalid conversation/pod id`  
  → header/body id was not a UUID

- `404 Pod not found` / `Conversation not found`  
  → Pod id is valid but doesn’t exist (or wrong tenant/owner)

- `409 Pod update conflict`  
  → optimistic concurrency conflict while writing the Pod

- `500 Flow error: ...`  
  → flow raised an exception (often: missing required context keys)

---

## Security notes (current + future)

- Today, conversation Pods are keyed by UUID and treated as an opaque handle.
  In a production system this must be protected by authorization:
  - map Pod access to `(tenant, end_user_id, course_id, role, …)`
  - likely enforced by SpiceDB or equivalent policy layer
- Environment Pods are deliberately separate because they are course-owned resources
  and will need different authorization rules than personal conversations.

---

## Implementation notes (for maintainers)

- The router uses a per-conversation `_thread_id` stored in the context and passes it
  to the LangGraph checkpointer so state checkpoints are stable across requests.
- The router strips `debug` before storing to keep persistent state clean.
- Streaming is currently disabled (`stream=False`) even if the client passes `stream: true`.

