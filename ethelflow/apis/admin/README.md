# Admin API (`ethelflow/apis/admin`)

This package exposes a small **admin-only** HTTP API for managing **environment pods** stored in Postgres via the `PodStore` abstraction.

The main consumer today is the `chatapi` router, which can apply a “course environment” on every request (**follow-latest**): templates, intent definitions, RAG knobs, and reference document ids are pulled from an environment pod and merged into the flow context.

> ⚠️ **No authentication/authorization yet.**  
> This router is intentionally minimal while the system is still under construction. In production, this is where authn/authz and SpiceDB-based authorization will be enforced.

---

## Why this exists

We want “oblivious” clients (e.g., LTI plugins or LMS integrations) to call what looks like a stateless OpenAI-compatible API, without the client needing to know about:

- Templates (prompt assembly)
- Reference document lists (RAG)
- Intent definitions (classification schema)
- Future environment-like configuration

So we store these **course-specific** settings in a durable place: **Pods** in Postgres.

The `admin` API provides a clean way for *your own* provisioning or integration code to set/update those environment pods.

---

## Data model: Environment Pod

Environment pods are stored with:

- `pod_type = "environment"`
- `tenant` and `owner_api` as provided in the URL
- A deterministic `pod_id` generated from `(tenant, owner_api, pod_type, key)`

The payload is schema-less on purpose:

```json
{
  "env_type": "<string>",
  "env_id": "<string>",
  "config": { "... arbitrary JSON ..." }
}
```

### Deterministic IDs (not a secret)

The router uses `deterministic_pod_id(...)` from `ethelflow.data.pods`:

- It produces a stable UUID (UUIDv5) from a namespace + a string name.
- This is **not** security-sensitive and can live in open source.
- The value must remain stable to allow deterministic lookups without listing.

---

## Endpoints

Base prefix: `/admin`

### GET `/admin/environments/{owner_api}/{tenant}/{env_type}/{env_id}`

Fetch the environment pod for `(owner_api, tenant, env_type, env_id)`.

**Response (200):**
```json
{
  "pod_id": "uuid",
  "tenant": "ethz",
  "owner_api": "chatapi",
  "env_type": "course",
  "env_id": "default",
  "config": { "... merged config ..." },
  "rev": 3,
  "updated_at": "2026-01-23T09:00:00Z"
}
```

If missing, returns `404 Environment not found`.

---

### PUT `/admin/environments/{owner_api}/{tenant}/{env_type}/{env_id}`

Create or update an environment pod.

**Request body:**
```json
{
  "config": { "... arbitrary JSON ..." }
}
```

**Response:**
```json
{ "ok": true, "created": true, "pod_id": "uuid", "rev": 1 }
```

---

## Expected `config` shape (for ChatAPI)

`config` is intentionally schema-less, but the current `chatapi` router understands these keys when applying a course environment:

- `template_text` *(string)* → merged into `ctx.rag.template`
- `intent_options` *(object)* → merged into `ctx.intent_options`
- `document_ids` *(list)* → merged into `ctx.rag.document_ids`
- `rag` *(object)* → shallow-merged into `ctx.rag` (e.g. extractor/method/top_k/etc.)

Example:

```json
{
  "config": {
    "template_text": "You are a helpful assistant... {{prompt}}",
    "intent_options": {
      "version": 1,
      "default_intent": "chat",
      "options": {
        "exercise": {"description": "Practice problem", "examples": ["quiz me"]}
      },
      "confidence_threshold": 0.7
    },
    "document_ids": ["c2ef...-uuid", "9b1a...-uuid"],
    "rag": {
      "top_k": 8,
      "method": "recursive_char_1000_100_htmlstrip"
    }
  }
}
```

---

## Smoke test (curl)

Set a course environment for ChatAPI (tenant `ethz`, course `default`):

```bash
curl -i -X PUT \
  http://localhost:8080/admin/environments/chatapi/ethz/course/default \
  -H 'Content-Type: application/json' \
  -d '{
    "config": {
      "template_text": "You are a course bot.\n\nUser: {{prompt}}\n",
      "document_ids": [],
      "rag": {"top_k": 5},
      "intent_options": {
        "version": 1,
        "default_intent": "chat",
        "options": {"exercise": {"description": "Practice", "examples": ["quiz me"]}},
        "confidence_threshold": 0.7
      }
    }
  }'
```

Fetch it back:

```bash
curl -i \
  http://localhost:8080/admin/environments/chatapi/ethz/course/default
```

Then call ChatAPI using that course environment (the ChatAPI router uses `course_id` to select the env pod):

```bash
curl -i -X POST http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "rag_intent_chat",
    "messages": [{"role":"user","content":"hello"}],
    "metadata": {"tenant":"ethz","course_id":"default"}
  }'
```

---

## Files

- `router.py` — Admin router implementation (GET/PUT environment pods)

---

## Future work

- Authentication and authorization (SpiceDB)
- Support multiple environment types (course / org / tool / etc.)
- Validation layers for known config shapes (while keeping storage schema-less)
- Audit logging (who changed what, when)
