# chunk_text

## Overview
`chunk_text` is a lightweight utility service that splits a raw text string into overlapping chunks using LangChain’s `RecursiveCharacterTextSplitter`.

It is intended for preprocessing text before storing chunks (e.g., via `store_chunks`) and later embedding/searching. It is **stateless** (no DB, no model catalog) and deterministic given `(text, chunk_size, chunk_overlap)`.

## Endpoint(s)
- `POST /chunk_text`

## Request/Response

### Request (JSON fields)
- `text` (string, required): The raw text to split.
- `chunk_size` (int, required, > 0): Target chunk size in characters.
- `chunk_overlap` (int, required, > 0, < chunk_size): Character overlap between consecutive chunks.

Validation rules:
- `chunk_size > 0`
- `chunk_overlap > 0`
- `chunk_overlap < chunk_size`

### Response (JSON fields)
- `created` (datetime string): Timestamp when chunking was performed (UTC).
- `chunks` (list[string]): The resulting text chunks.

### Example
Request:
```json
{
  "text": "This is a long text ...",
  "chunk_size": 500,
  "chunk_overlap": 50
}
```

Response:
```json
{
  "created": "2026-01-10T12:34:56.789012+00:00",
  "chunks": [
    "This is a long text ...",
    "..."
  ]
}
```

## Node adapter contract

### Node
`ethelflow.agents.chunk_text.node_adapter.chunk_text_node(...)`

### State inputs
- Reads `state[input_text_key]` (default: `"text"`) → must be a `str`.

### Node configuration parameters (set when constructing the node)
- `chunk_size` (default: `500`)
- `chunk_overlap` (default: `50`)

> Note: In the current adapter, `chunk_size` and `chunk_overlap` are **not** read from the state; they are fixed by the node constructor arguments.

### State outputs
- Writes `state[output_key]` (default: `"texts"`) → `list[str]` of chunks.

### Minimal flow snippet
```python
from ethelflow.agents.chunk_text.node_adapter import chunk_text_node

chunk = chunk_text_node(
    input_text_key="text",
    output_key="chunks",
    chunk_size=1000,
    chunk_overlap=100,
)
workflow.add_node("chunk_text", chunk)
```

## Routing / Model catalog expectations (tenant/space)
None.
- No tenant routing
- No embedding space / inference class usage
- No provider keys required

## Storage / DB interaction (tables/constraints)
None.
- No database reads/writes

## k8s deployment/service name
- **Service DNS name used by adapters:** `chunk-text.default.svc:8000`
- **Endpoint path:** `/chunk_text`

(Deployment name is typically aligned with the service name; if you standardize differently, keep the Service name `chunk-text` consistent with the adapter URL or update `CHUNK_TEXT_URL`.)

## Troubleshooting
- **HTTP 422 / validation errors**: Ensure `chunk_size > 0`, `chunk_overlap > 0`, and `chunk_overlap < chunk_size`.
- **HTTP != 200 from the adapter**: Check the `chunk-text` pod logs and verify the Service is reachable:
  - `kubectl -n default get pods,svc | grep chunk-text`
- **Unexpected chunk boundaries**: `RecursiveCharacterTextSplitter` chunks by characters with heuristic separators; if you need token-based chunking, consider switching to a token-aware splitter in the service implementation.
