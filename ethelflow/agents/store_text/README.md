# store-text

## Overview
`store-text` persists extracted text for a specific document version (`document_id`) under a named **extractor** label (e.g. `pdfminer`, `ocr`, `bs4`, `file_to_text`).

It writes to `document_texts` and enforces **one text row per (`document_id`, `extractor`)**. If a row already exists, it is updated; otherwise it is created.

Typical placement in a pipeline:
1. Upload asset → get `document_id`
2. Extract text (file-to-text / OCR / PDFMiner / etc.)
3. **store-text** → get `text_id`
4. Chunk text → store-chunks → store-vectors → search-vectors / retrieve-chunks

---

## Endpoint(s)
- `POST /store_text`

Service listens on port `8000`.

---

## Request/Response

### Request fields
- `document_id` (UUID, required): Document version ID (`etheldocuments.id`)
- `extractor` (str, required): Extractor label (must be consistent across the pipeline)
- `text` (str | null, optional): Extracted text (may be `null`)

### Example request
```json
{
  "document_id": "697a9f34-72fe-4ed6-81c6-05eabf0747c7",
  "extractor": "file_to_text",
  "text": "…extracted text…"
}
```

### Response fields
- `success` (bool)
- `message` (str, optional): Error message on failure
- `text_id` (UUID | null): The `document_texts.id` for this (`document_id`, `extractor`)
- `created` (bool): `true` if a new row was inserted, `false` if an existing row was updated

### Example response (created)
```json
{
  "success": true,
  "message": "",
  "text_id": "fa63335e-2613-43c4-9994-1ef1e350420a",
  "created": true
}
```

### Example response (updated)
```json
{
  "success": true,
  "message": "",
  "text_id": "fa63335e-2613-43c4-9994-1ef1e350420a",
  "created": false
}
```

---

## Node adapter contract

### Node
`ethelflow.agents.store_text.node_adapter.store_text_node(...)`

### Input keys → Output keys
Defaults (overrideable via parameters):

**Inputs**
- `document_id` → `document_id_key`
- `extractor` → `extractor_key`
- `text` → `text_key`

**Outputs**
- `store_text_response` → `output_key` (full JSON response)
- `text_id` → `output_text_id_key` (UUID rendered as a string for downstream nodes)

### Notes
- The adapter validates `document_id` as UUID and `extractor` as non-empty string.
- If the service returns `success=false` or missing `text_id`, the node raises `ValueError`.

---

## Routing/Model catalog expectations (tenant/space)
None.

`store-text` does not use the model catalog. Routing is purely service discovery via Kubernetes DNS:
- `http://store-text.default.svc:8000/store_text`

---

## Storage/DB interaction (tables/constraints)
- Table: `document_texts`
- Key fields used:
  - `document_id` (FK to `etheldocuments.id`)
  - `extractor` (string label)
  - `text` (TEXT, nullable)
- Constraint expected (from schema/migrations):
  - Unique on (`document_id`, `extractor`)  
    (the service implements “update if exists else insert” behavior consistent with this.)

Behavior:
- If (`document_id`, `extractor`) exists → update `text`
- Else → insert new `DocumentText` row

---

## k8s deployment/service name
- Deployment: `store-text`
- Service: `store-text`
- Container command:
  - `python -m ethelflow.agents.store_text`
- Exposes port `8000` (ClusterIP)

(See the repo’s `k8s/store-text.yaml` for the canonical manifest pattern.)

---

## Troubleshooting
- **`success=false` and `extractor must be a non-empty string`**  
  Ensure you pass a non-empty `extractor` label and that your flow uses the same label consistently across chunking/search.

- **Foreign key / missing document**  
  If `document_id` doesn’t exist in `etheldocuments`, the insert may fail depending on FK enforcement.

- **Empty retrieval later**  
  Most often caused by mismatched labels:
  - `extractor` used for storing text differs from what downstream nodes filter on (e.g., search-vectors filters by `dt.extractor`).
  - Confirm your flow uses the same `extractor` string for store-text, chunking, search-vectors.

- **DB errors / migrations**  
  Make sure the DB schema is current (alembic upgraded) and the `document_texts` table exists.
