# file_to_text

## Overview
`file_to_text` is a text-extraction microservice.

Given a `document_id`, it:
1. Looks up the document metadata in Postgres (`EthelDocument`) to determine `content_type`.
2. Downloads the raw file bytes from S3/MinIO using the document UUID as the object key.
3. Extracts text based on `content_type`:
   - **`application/pdf`**: uses `pypdf.PdfReader` and concatenates `page.extract_text()` for all pages.
   - **`text/plain`**: decodes the file as UTF-8.
   - **`text/html`**: strips markup via `BeautifulSoup` (removes `script`, `style`, `noscript`) and returns normalized text lines.

This service only returns extracted text; it does **not** write anything back to the database. In ingestion flows, you typically call `store_text` afterwards using `extractor="file_to_text"` to persist the extracted text.

## Endpoint(s)
- `POST /file_to_text`

The in-cluster URL used by flows is:
- `http://file-to-text.default.svc:8000/file_to_text`

## Request/Response

### Request fields
- `document_id` (uuid, required): ID of a document that exists in Postgres and whose content is stored in S3/MinIO under the same UUID key.

### Request example
```json
{
  "document_id": "4a70cc94-93a2-4ef2-b751-e2a6399fdf6b"
}
```

### Response fields
- `text` (string): extracted plaintext (may be empty if extraction yields no text).

### Response example
```json
{
  "text": "Example extracted text\nSecond line"
}
```

## Node adapter contract

### Inputs (state keys → request)
- `document_id_key` (default: `"document_id"`) → `document_id`

### Outputs
- `output_key` (default: `"text"`) → extracted text string

### Notes / gotchas
- The current adapter calls `uuid.UUID(state.get(document_id_key))`. This means the state value should be a **string UUID** (not a `uuid.UUID` object), otherwise you can hit a type/attribute error.

## Routing/Model catalog expectations (tenant/space)
- None. This service does not use the model catalog and does not require `tenant`, `space`, or `inference_class`.

## Storage/DB interaction (tables/constraints)
Reads:
- Postgres: `EthelDocument` (to validate the document exists and to get `content_type`)

Reads:
- S3/MinIO: downloads the file bytes via `s3_manager.download_file(str(document.id), ...)`

Writes:
- None

## k8s deployment/service name
- Deployment: `file-to-text`
- Service: `file-to-text`
- Container port: `8000`
- Path: `/file_to_text`

This service requires the standard EthelFlow S3 and Postgres environment variables (same as other services using `s3_manager` and `get_session`), for example:
- `ETHELFLOW_S3_ENDPOINT_URL`, `ETHELFLOW_S3_ACCESS_KEY`, `ETHELFLOW_S3_SECRET_KEY`, `ETHELFLOW_S3_BUCKET_NAME`
- `ETHELFLOW_POSTGRES_HOST`, `ETHELFLOW_POSTGRES_PORT`, `ETHELFLOW_POSTGRES_DB`, `ETHELFLOW_POSTGRES_USER`, `ETHELFLOW_POSTGRES_PASSWORD`

## Troubleshooting
- **HTTP 404: Document not found**  
  The `document_id` does not exist in `EthelDocument`.

- **HTTP 400: Unsupported content type**  
  Only `application/pdf`, `text/plain`, and `text/html` are supported. Add additional extractors/services for other formats.

- **Empty output for PDFs**  
  Many PDFs (scans) contain no embedded text. `pypdf` will then return `None` per page. Use an OCR-based extractor for scanned PDFs.

- **Unicode decode errors for text/plain**  
  The service assumes UTF-8. If you have non-UTF8 plaintext files, you may need a more robust decoding strategy.

- **HTML output looks “squashed” or missing structure**  
  The HTML converter strips scripts/styles and normalizes whitespace. If you need richer structure, store both raw HTML and extracted text, or enhance the extractor.
