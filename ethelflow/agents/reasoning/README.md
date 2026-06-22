# reasoning

## Overview
The **reasoning** service is the LLM inference gateway for EthelFlow. It routes requests by **tenant** and an **inference class** (default: `reasoning`) using the **model catalog**, and calls the configured provider (e.g., Azure OpenAI) to produce a completion.

It exposes two endpoints:
- `/reasoning` for plain text prompts
- `/reasoning_with_document` for prompts/messages that include **images** (either inline base64 images or images loaded from S3 via `document_id(s)`)

This service supports **streaming** (incremental text chunks) or **non-streaming** (single final response).

---

## Endpoint(s)

### `POST /reasoning`
Text-only prompt inference.

### `POST /reasoning_with_document`
Inference where the prompt/messages may include images:
- `images`: inline images with base64 payloads
- `document_id` + `content_type`: fetch one object from S3 and attach it (images supported)
- `document_ids` + `content_types`: fetch multiple objects from S3 and attach them (images supported)

---

## Request/Response

### Request model: `ReasoningRequest`
Routing:
- `tenant` (string, required): tenant label used to route via `catalog.tenants`

Content (choose one of `prompt` OR `messages`):
- `prompt` (string | null): plain prompt text
- `messages` (list[dict] | null): chat-style messages (OpenAI-compatible shape; this service will add/attach images to the last message if needed)

Optional:
- `reasoning_effort` (`"low" | "medium" | "high"` | null): forwarded to provider (when supported)
- `stream` (bool, default `false`): if `true`, stream text chunks

Image/document inputs (used only by `/reasoning_with_document`):
- `images` (list[InlineImage] | null): inline images
- `document_id` (uuid | null) + `content_type` (string | null): single document from S3 (content_type required if document_id provided)
- `document_ids` (list[uuid] | null) + `content_types` (list[string] | null): multiple S3 documents (must match lengths)

Backward-compat escape hatch:
- `deployment` (string | null): if provided, overrides catalog deployment (debug/escape hatch)

Validation rules (enforced by model validators):
- If `document_id` is provided, `content_type` **must** be provided.
- If `document_ids` is provided, `content_types` **must** be provided and lengths must match.
- Exactly one of `prompt` or `messages` must be provided (not both).
- At least one of `prompt` or `messages` must be present.

### Response model: `ReasoningResponse` (non-streaming)
- `response` (string): final answer text
- `tenant` (string): routed tenant
- `provider` (string): provider name
- `deployment` (string): resolved deployment name

### Streaming response (when `stream=true`)
Returns `text/event-stream` and yields **raw text chunks** (not JSON). The service streams the incremental token text as it arrives from the provider.

---

## Example JSON

### 1) Non-streaming prompt (`/reasoning`)
```json
{
  "tenant": "ethz",
  "prompt": "Explain Maxwell's equations briefly.",
  "stream": false,
  "reasoning_effort": "low"
}
```

### 2) Streaming prompt (`/reasoning`)
```json
{
  "tenant": "ethz",
  "prompt": "Write a short poem about snow.",
  "stream": true
}
```

### 3) Non-streaming with a single S3 document (`/reasoning_with_document`)
> NOTE: current implementation only supports image/* content types for S3 retrieval in the multi-document path; single-document path base64-encodes any file, but image support is what models can consume.
```json
{
  "tenant": "ethz",
  "document_id": "4a70cc94-93a2-4ef2-b751-e2a6399fdf6b",
  "content_type": "image/png",
  "prompt": "Describe what you see in the image.",
  "stream": false
}
```

### 4) Inline images (`/reasoning_with_document`)
```json
{
  "tenant": "ethz",
  "images": [
    {"content_type": "image/png", "data_base64": "iVBORw0KGgoAAA..."}
  ],
  "prompt": "What is shown here?",
  "stream": false
}
```

### 5) Messages (chat-style) (`/reasoning_with_document`)
```json
{
  "tenant": "ethz",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Summarize the key idea."}
  ],
  "stream": false
}
```

---

## Node adapter contract

### Adapter
`ethelflow.agents.reasoning.node_adapter.reasoning_node(...)`

### Inputs (state keys)
Configurable via function arguments; defaults:
- `tenant_key="tenant"` → required
- `prompt_key="prompt"` → optional if `messages` is provided
- `messages_key="messages"` → optional if `prompt` is provided
- `stream_key="stream"` → default `False` if missing
- `reasoning_effort_key` → optional (if provided, read from state)
- Optional document/image keys (only if configured):
  - `document_id_key`, `content_type_key`
  - `document_ids_key`, `content_types_key`
  - `images_key`

### Outputs (state keys)
- Non-streaming: yields `{output_key: <final string>}` once
- Streaming (`stream=true`):
  - yields `{output_key: <text chunk>}` repeatedly
  - then yields `{output_key: None}` (end-of-stream sentinel)
  - then yields `{output_key: <full concatenated text>}`

Default:
- `output_key="reasoning_response"`

---

## Routing / Model catalog expectations

- The service loads the model catalog on startup.
- Requests are routed using:
  - `tenant` from the request
  - `INFERENCE_CLASS` from env var `ETHELFLOW_INFERENCE_CLASS` (default: `reasoning`)
- Route resolution:
  - `catalog.tenant_inference_route(tenant=req.tenant, class_name=INFERENCE_CLASS)`
  - provider + deployment are taken from the route unless `deployment` is explicitly provided

Provider credentials:
- Provider API key must be present in the environment variable specified by the provider entry in the catalog
  (e.g., `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`)

---

## Storage / DB interaction

- **No Postgres tables** are used by this service.
- When using `/reasoning_with_document`, documents are fetched from **S3** via `s3_manager` and base64-encoded for inclusion as `data:` URLs in the request to the LLM.

Required S3 configuration (typical):
- `ETHELFLOW_S3_ENDPOINT_URL`
- `ETHELFLOW_S3_ACCESS_KEY`
- `ETHELFLOW_S3_SECRET_KEY`
- `ETHELFLOW_S3_BUCKET_NAME`

---

## k8s deployment/service name

Typical:
- Deployment: `reasoning`
- Service: `reasoning`
- Container port: `8000`

Also required for catalog-based routing:
- ConfigMap mount: `ethelflow-model-catalog` → `/etc/ethelflow/catalog.yaml`
- Env: `ETHELFLOW_MODEL_CATALOG_PATH=/etc/ethelflow/catalog.yaml`

---

## Troubleshooting

### 500: “Missing provider API key env var …”
- The provider API key env var referenced by the model catalog is missing in the deployment.
- Confirm your Secret is mounted and the env var name matches the catalog (e.g., `ETHELFLOW_PROVIDER_ETHZ_AZURE_OPENAI_API_KEY`).

### Wrong model used / unexpected deployment
- Ensure `tenant` is correct.
- Ensure `ETHELFLOW_INFERENCE_CLASS` matches a class in `catalog.inference`.
- Avoid using `deployment` override except for debugging.

### Streaming clients see partial output / formatting oddities
- Streaming response is `text/event-stream` but chunks are **raw text**, not JSON.
- Your client should treat the response body as an incremental text stream.

### `/reasoning_with_document` errors about content types
- If providing `document_id`, you must also provide `content_type`.
- If providing `document_ids`, you must provide `content_types` and the lengths must match.
- Multi-document path currently enforces `image/*` content types.
